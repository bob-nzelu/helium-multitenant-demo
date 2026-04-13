"""
Redis Client for Rate Limiting

Provides atomic rate limiting using Redis INCR + EXPIRE.
Graceful degradation: if Redis is unavailable, all requests are allowed.

This is the ONLY Redis consumer in Relay. Dedup stays with HeartBeat.
Blob storage stays with HeartBeat. Only rate limiting uses Redis directly.

Does NOT inherit BaseClient — HTTP retry logic doesn't apply to Redis
(sub-millisecond atomic ops, not multi-second HTTP round-trips).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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
