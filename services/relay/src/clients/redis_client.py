"""
Redis Client for Rate Limiting + CSSV1 R5 Duplicate Lookup

Provides:
- Atomic rate limiting via Redis INCR + EXPIRE.
- CSSV1 R5 (S7) duplicate-lookup primary tier — tenant-keyed
  preflight cache read for ``POST /api/duplicate/lookup``. Ten-millisecond
  p99 budget on the Redis-direct path; the route falls back to HB on
  miss or Redis-down per ``RELAY_PHASE1_DESIGN_ALIGNMENT_2026_05_09 §4.5``.

Graceful degradation: if Redis is unavailable, rate-limit requests are
allowed and duplicate lookups return ``None`` (route then tries HB
fallback, and if that also fails, allows the upload — data safety > rate
limiting).

Does NOT inherit BaseClient — HTTP retry logic doesn't apply to Redis
(sub-millisecond atomic ops, not multi-second HTTP round-trips).

Note: the duplicate-lookup tier reads only. The cache write-back (i.e.
populating Redis from ``/api/ingest`` happy path) is wired in CSSV1 S4
(R7 + ``record_duplicate()`` cleanup) — until S4 lands, the Redis tier
will essentially always miss and traffic falls to the HB tier. Expected.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    current_count: int
    limit: int
    remaining: int
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
    ):
        self._redis_url = redis_url
        self._prefix = prefix
        self._default_limit = default_limit
        self._redis = None  # redis.asyncio.Redis instance (lazy import)
        self._available = False

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

    # ── CSSV1 S7 (R5) — Duplicate Lookup Primary Tier ──────────────────

    async def check_duplicate(
        self,
        tenant_id: str,
        file_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Tenant-keyed Redis lookup for ``POST /api/duplicate/lookup`` primary tier.

        Key shape: ``{prefix}:dedup:{tenant_id}:{file_hash}``.

        The ``tenant_id`` segment IS the cross-tenant guard at the cache
        layer. A SET-membership probe by a wrong tenant returns "not
        present" without the request ever leaving Relay.

        Args:
            tenant_id: Caller's tenant id (from ``CallerContext``).
            file_hash: SHA-256 hex digest, lowercase, 64 chars.

        Returns:
            - Cached side-response dict on hit (caller returns directly).
            - ``None`` on miss OR on Redis exception (caller falls back
              to HB). On exception, ``self._available`` is flipped to
              ``False`` so subsequent calls within the same request
              don't re-raise.
        """
        if not self._available or self._redis is None:
            return None

        key = f"{self._prefix}:dedup:{tenant_id}:{file_hash}"

        try:
            cached = await self._redis.get(key)
        except Exception as e:
            logger.warning(
                f"Redis check_duplicate failed — falling through to HB: {e}"
            )
            self._available = False
            return None

        if cached is None:
            return None

        try:
            return json.loads(cached)
        except (TypeError, json.JSONDecodeError) as e:
            # Corrupt cache entry — log + treat as miss. Do NOT poison
            # the response; the HB fallback will return authoritative
            # state. Don't flip _available here either — Redis itself
            # is fine; only this entry is bad.
            logger.warning(
                f"Redis check_duplicate cache decode failed for "
                f"{key[:64]}...: {e}"
            )
            return None
