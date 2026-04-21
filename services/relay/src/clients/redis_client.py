"""
Redis Client for Rate Limiting

Provides atomic rate limiting using Redis INCR + EXPIRE (legacy daily) and a
Lua-backed token bucket (Phase 1b per-minute / per-hour) plus nonce-replay
protection (SETNX) for the HMAC path.

Graceful degradation: if Redis is unavailable, all requests are allowed at a
bounded burst cap (spec §6.6) — this service is ingestion-critical, failing
closed would wedge the platform for every caller on a Redis blip.

This is the ONLY Redis consumer in Relay. Dedup stays with HeartBeat. Blob
storage stays with HeartBeat. Only rate limiting + nonce replay use Redis
directly.

Does NOT inherit BaseClient — HTTP retry logic doesn't apply to Redis
(sub-millisecond atomic ops, not multi-second HTTP round-trips).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Lua script for atomic token-bucket refill + consume.
#
# KEYS[1] = bucket hash key
# ARGV[1] = capacity           (int, max tokens)
# ARGV[2] = refill_per_second  (float, tokens/sec)
# ARGV[3] = cost               (int, tokens this request)
# ARGV[4] = now                (float, epoch seconds)
# ARGV[5] = bucket_ttl         (int, seconds of idle before Redis evicts)
#
# Returns: {allowed:int(0/1), remaining:int, reset_epoch:int}
#   reset_epoch = estimated time bucket will be full again
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, ttl)

local deficit = capacity - tokens
local reset_epoch = now
if refill > 0 then
  reset_epoch = now + (deficit / refill)
end

return {allowed, math.floor(tokens), math.floor(reset_epoch)}
"""


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    current_count: int
    limit: int
    remaining: int
    source: str = "redis"  # "redis" | "degraded"


@dataclass
class TokenBucketResult:
    """
    Result of a token-bucket check.

    `reset_epoch` is the unix timestamp at which the bucket will be full
    again — callers use it to set the X-RateLimit-Reset-* header.
    `source` discriminates the Redis path from degraded (burst-cap) fail-open.
    """

    allowed: bool
    remaining: int
    limit: int
    reset_epoch: int
    source: str = "redis"  # "redis" | "degraded"


class RedisClient:
    """
    Async Redis client for Relay rate limiting.

    Usage:
        client = RedisClient(redis_url="redis://localhost:6379/0")
        await client.connect()
        result = await client.check_rate_limit("company-123", limit=500)
        if not result.allowed:
            raise RateLimitExceededError(...)
        await client.close()

    Graceful degradation:
        If redis_url is empty or Redis is unreachable, all requests are allowed.
        This matches the existing HeartBeat degradation pattern.
    """

    def __init__(
        self,
        redis_url: str = "",
        prefix: str = "relay",
        default_limit: int = 500,
        fail_open_burst: int = 10,
    ):
        self._redis_url = redis_url
        self._prefix = prefix
        self._default_limit = default_limit
        self._fail_open_burst = fail_open_burst
        self._redis = None  # redis.asyncio.Redis instance (lazy import)
        self._available = False
        # Fail-open burst tracking: {caller_key: (window_start, count)} — only
        # used when Redis is unreachable (spec §6.6).
        self._burst_state: dict[str, tuple[float, int]] = {}

    async def connect(self) -> bool:
        """
        Connect to Redis.

        Returns:
            True if connected, False if unavailable (graceful degradation).
        """
        if not self._redis_url:
            logger.info("Redis URL not configured — rate limiting degraded (allow all)")
            return False

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # Ping to verify connection
            await self._redis.ping()
            self._available = True
            logger.info(f"Redis connected — {self._redis_url}")
            return True

        except Exception as e:
            logger.warning(f"Redis connection failed — rate limiting degraded: {e}")
            self._available = False
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._available = False

    async def check_rate_limit(
        self,
        company_id: str,
        file_count: int = 1,
        limit: Optional[int] = None,
    ) -> RateLimitResult:
        """
        Check and increment daily rate limit for a company.

        Uses Redis INCR + EXPIRE for atomic counter with auto-expiry.
        Key format: {prefix}:daily:{company_id}:{YYYY-MM-DD}
        TTL: 86400 seconds (24 hours) — auto-cleanup, no manual purge.

        Args:
            company_id: Company/API-key identifier.
            file_count: Number of files in this request.
            limit: Daily limit override (defaults to config value).

        Returns:
            RateLimitResult with allowed flag, counts, and remaining.
        """
        daily_limit = limit or self._default_limit

        if not self._available or self._redis is None:
            # Graceful degradation: Redis unavailable -> allow
            return RateLimitResult(
                allowed=True,
                current_count=0,
                limit=daily_limit,
                remaining=daily_limit,
                source="degraded",
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"{self._prefix}:daily:{company_id}:{today}"

        try:
            # Atomic INCR + conditional EXPIRE via pipeline
            pipe = self._redis.pipeline()
            pipe.incrby(key, file_count)
            pipe.ttl(key)
            results = await pipe.execute()

            current_count = results[0]  # INCRBY returns new value
            ttl = results[1]            # TTL: -1 = no expiry, -2 = key gone

            # Set TTL if this is a new key (TTL = -1 means no expiry set)
            if ttl == -1:
                await self._redis.expire(key, 86400)

            remaining = max(0, daily_limit - current_count)
            allowed = current_count <= daily_limit

            return RateLimitResult(
                allowed=allowed,
                current_count=current_count,
                limit=daily_limit,
                remaining=remaining,
                source="redis",
            )

        except Exception as e:
            logger.warning(f"Redis rate limit check failed — allowing: {e}")
            self._available = False  # Mark unavailable for subsequent calls
            return RateLimitResult(
                allowed=True,
                current_count=0,
                limit=daily_limit,
                remaining=daily_limit,
                source="degraded",
            )

    # ── Phase 1b: token-bucket rate limit ──────────────────────────────

    async def token_bucket_check(
        self,
        caller_key: str,
        window: str,
        capacity: int,
        cost: int = 1,
        bucket_ttl_s: int = 7200,
    ) -> TokenBucketResult:
        """
        Atomic token-bucket consume-or-reject via Redis Lua.

        Key shape: ``ratelimit:{prefix}:{window}:{caller_key}``
        Refill rate is derived as ``capacity / window_seconds`` — a full
        bucket refills in exactly one window, matching spec §6.2.

        Args:
            caller_key: api_key (service/erp) or tenant_id (user path).
            window: "per_minute" | "per_hour" — selects the refill rate.
            capacity: max tokens (tier limit for this window).
            cost: tokens this request costs (endpoint weight, default 1).
            bucket_ttl_s: seconds of idle before Redis evicts. Default 2h so
                per-hour buckets always outlive their refill cycle.

        Returns:
            TokenBucketResult with allowed/remaining/reset_epoch.

        Fail-open: if Redis unreachable, enforces a ``fail_open_burst`` cap
            per caller per second (spec §6.6) to prevent a Redis outage from
            opening a full-rate abuse window.
        """
        window_seconds = {"per_minute": 60, "per_hour": 3600}.get(window)
        if window_seconds is None:
            raise ValueError(f"Unknown window: {window!r}")

        if capacity <= 0:
            # Tier with capacity=0 means "unlimited" (enterprise per-hour can
            # be configured as empty/null per spec §6.3). Treat as allowed
            # with remaining=capacity and no real enforcement.
            return TokenBucketResult(
                allowed=True, remaining=0, limit=0,
                reset_epoch=int(time.time()), source="unlimited",
            )

        if not self._available or self._redis is None:
            return self._fail_open_check(caller_key, capacity)

        key = f"ratelimit:{self._prefix}:{window}:{caller_key}"
        refill = capacity / window_seconds
        now = time.time()

        try:
            result = await self._redis.eval(
                _TOKEN_BUCKET_LUA, 1, key,
                capacity, refill, cost, now, bucket_ttl_s,
            )
            allowed = int(result[0]) == 1
            remaining = int(result[1])
            reset_epoch = int(result[2])
            return TokenBucketResult(
                allowed=allowed,
                remaining=remaining,
                limit=capacity,
                reset_epoch=reset_epoch,
                source="redis",
            )
        except Exception as e:
            logger.warning(
                f"token_bucket_check failed for {caller_key}:{window} — "
                f"fail-open with burst cap: {e}"
            )
            self._available = False
            return self._fail_open_check(caller_key, capacity)

    def _fail_open_check(
        self, caller_key: str, capacity: int
    ) -> TokenBucketResult:
        """
        Local 1-second burst cap when Redis is unreachable.

        Tracks per-caller count in an in-process dict, reset every second.
        Caps at ``self._fail_open_burst`` (default 10) requests/second per
        caller — prevents a Redis outage from letting a single caller
        stampede the backend.
        """
        now = time.time()
        window_start, count = self._burst_state.get(caller_key, (now, 0))

        if now - window_start >= 1.0:
            window_start = now
            count = 0

        count += 1
        self._burst_state[caller_key] = (window_start, count)

        allowed = count <= self._fail_open_burst
        return TokenBucketResult(
            allowed=allowed,
            remaining=max(0, self._fail_open_burst - count),
            limit=capacity,
            reset_epoch=int(window_start + 1),
            source="degraded",
        )

    # ── Phase 1b: nonce replay protection ──────────────────────────────

    async def nonce_claim(self, nonce: str, ttl_s: int = 600) -> bool:
        """
        Claim a nonce. First caller gets True, replays get False.

        Key shape: ``{prefix}:nonce:{nonce}``
        Uses ``SET ... NX EX`` for atomicity + auto-expiry.

        When Redis is unreachable, fail-open (returns True). Replay
        protection silently degrades; the timestamp-window check (300s) in
        the HMAC verifier is the secondary defense.
        """
        if not self._available or self._redis is None:
            return True

        key = f"{self._prefix}:nonce:{nonce}"
        try:
            # SET key value NX EX ttl — returns "OK" if set, None if exists.
            result = await self._redis.set(key, "1", nx=True, ex=ttl_s)
            return result is not None
        except Exception as e:
            logger.warning(f"nonce_claim failed, allowing: {e}")
            self._available = False
            return True

    @property
    def is_available(self) -> bool:
        """Whether Redis is currently connected and responding."""
        return self._available

    async def health_check(self) -> bool:
        """Check if Redis is reachable."""
        if not self._redis:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception:
            self._available = False
            return False
