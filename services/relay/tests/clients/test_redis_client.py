"""
Tests for RedisClient (rate limiting)

Tests use mock Redis — no real Redis instance needed.
Graceful degradation is the primary design contract:
if Redis is down or unconfigured, all requests are allowed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.clients.redis_client import RedisClient, RateLimitResult


# ── RateLimitResult Dataclass ────────────────────────────────────────────


class TestRateLimitResult:
    """Test the result dataclass."""

    def test_defaults(self):
        r = RateLimitResult(allowed=True, current_count=5, limit=500, remaining=495)
        assert r.source == "redis"

    def test_degraded_source(self):
        r = RateLimitResult(
            allowed=True, current_count=0, limit=500, remaining=500, source="degraded"
        )
        assert r.source == "degraded"


# ── No URL (Disabled) ───────────────────────────────────────────────────


class TestRedisClientNoUrl:
    """When redis_url is empty, Redis is disabled. All requests allowed."""

    @pytest.mark.asyncio
    async def test_connect_returns_false(self):
        client = RedisClient(redis_url="")
        result = await client.connect()
        assert result is False
        assert client.is_available is False

    @pytest.mark.asyncio
    async def test_check_rate_limit_allows_all(self):
        client = RedisClient(redis_url="")
        result = await client.check_rate_limit("company-1", file_count=1)
        assert result.allowed is True
        assert result.source == "degraded"
        assert result.remaining == 500
        assert result.limit == 500

    @pytest.mark.asyncio
    async def test_check_rate_limit_custom_default(self):
        client = RedisClient(redis_url="", default_limit=1000)
        result = await client.check_rate_limit("company-1")
        assert result.limit == 1000
        assert result.remaining == 1000

    @pytest.mark.asyncio
    async def test_health_check_false_when_no_url(self):
        client = RedisClient(redis_url="")
        assert await client.health_check() is False

    @pytest.mark.asyncio
    async def test_close_no_op_when_not_connected(self):
        client = RedisClient(redis_url="")
        await client.close()  # Should not raise
        assert client.is_available is False

    def test_is_available_false_by_default(self):
        client = RedisClient(redis_url="")
        assert client.is_available is False


# ── Connection Failure ───────────────────────────────────────────────────


class TestRedisClientConnectionFailure:
    """When Redis URL is set but connection fails."""

    @pytest.mark.asyncio
    async def test_connect_failure_returns_false(self):
        client = RedisClient(redis_url="redis://nonexistent:6379/0")
        # This will fail because the host doesn't exist
        result = await client.connect()
        assert result is False
        assert client.is_available is False

    @pytest.mark.asyncio
    async def test_degraded_after_connect_failure(self):
        client = RedisClient(redis_url="redis://nonexistent:6379/0")
        await client.connect()
        result = await client.check_rate_limit("company-1")
        assert result.allowed is True
        assert result.source == "degraded"


# ── Connected (Mocked Redis) ────────────────────────────────────────────


class TestRedisClientConnected:
    """When Redis is available, rate limiting should enforce limits."""

    def _make_connected_client(self, default_limit=500):
        """Create a RedisClient with a mocked Redis connection."""
        client = RedisClient(redis_url="redis://fake:6379/0", default_limit=default_limit)
        client._available = True
        client._redis = AsyncMock()
        return client

    def _setup_pipeline(self, client, incrby_result, ttl_result):
        """Set up mock pipeline to return INCRBY and TTL results."""
        pipe_mock = AsyncMock()
        pipe_mock.incrby = MagicMock(return_value=pipe_mock)
        pipe_mock.ttl = MagicMock(return_value=pipe_mock)
        pipe_mock.execute = AsyncMock(return_value=[incrby_result, ttl_result])
        client._redis.pipeline = MagicMock(return_value=pipe_mock)
        return pipe_mock

    @pytest.mark.asyncio
    async def test_under_limit_allowed(self):
        client = self._make_connected_client(default_limit=500)
        self._setup_pipeline(client, incrby_result=5, ttl_result=86000)

        result = await client.check_rate_limit("company-1", file_count=1)
        assert result.allowed is True
        assert result.current_count == 5
        assert result.remaining == 495
        assert result.source == "redis"

    @pytest.mark.asyncio
    async def test_at_exact_limit_allowed(self):
        """Count == limit should still be allowed (limit is inclusive)."""
        client = self._make_connected_client(default_limit=500)
        self._setup_pipeline(client, incrby_result=500, ttl_result=86000)

        result = await client.check_rate_limit("company-1")
        assert result.allowed is True
        assert result.remaining == 0

    @pytest.mark.asyncio
    async def test_over_limit_rejected(self):
        client = self._make_connected_client(default_limit=500)
        self._setup_pipeline(client, incrby_result=501, ttl_result=86000)

        result = await client.check_rate_limit("company-1")
        assert result.allowed is False
        assert result.remaining == 0
        assert result.current_count == 501

    @pytest.mark.asyncio
    async def test_custom_limit_override(self):
        client = self._make_connected_client(default_limit=500)
        self._setup_pipeline(client, incrby_result=11, ttl_result=86000)

        result = await client.check_rate_limit("company-1", limit=10)
        assert result.allowed is False
        assert result.limit == 10

    @pytest.mark.asyncio
    async def test_file_count_increments(self):
        """file_count > 1 should increment by that amount."""
        client = self._make_connected_client(default_limit=500)
        pipe_mock = self._setup_pipeline(client, incrby_result=3, ttl_result=86000)

        await client.check_rate_limit("company-1", file_count=3)
        # Verify INCRBY was called with the correct key pattern and file_count
        pipe_mock.incrby.assert_called_once()
        call_args = pipe_mock.incrby.call_args
        assert call_args[0][1] == 3  # file_count argument

    @pytest.mark.asyncio
    async def test_new_key_sets_expiry(self):
        """When TTL is -1 (no expiry), EXPIRE should be called."""
        client = self._make_connected_client()
        self._setup_pipeline(client, incrby_result=1, ttl_result=-1)

        await client.check_rate_limit("company-1")
        client._redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_existing_key_no_expire(self):
        """When TTL is positive, EXPIRE should NOT be called."""
        client = self._make_connected_client()
        self._setup_pipeline(client, incrby_result=5, ttl_result=86000)

        await client.check_rate_limit("company-1")
        client._redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_error_degrades_gracefully(self):
        client = self._make_connected_client()
        client._redis.pipeline = MagicMock(side_effect=Exception("Connection lost"))

        result = await client.check_rate_limit("company-1")
        assert result.allowed is True
        assert result.source == "degraded"
        assert client.is_available is False

    @pytest.mark.asyncio
    async def test_pipeline_execute_error_degrades(self):
        """Error during pipeline.execute() should degrade."""
        client = self._make_connected_client()
        pipe_mock = AsyncMock()
        pipe_mock.incrby = MagicMock(return_value=pipe_mock)
        pipe_mock.ttl = MagicMock(return_value=pipe_mock)
        pipe_mock.execute = AsyncMock(side_effect=Exception("Redis timeout"))
        client._redis.pipeline = MagicMock(return_value=pipe_mock)

        result = await client.check_rate_limit("company-1")
        assert result.allowed is True
        assert result.source == "degraded"

    @pytest.mark.asyncio
    async def test_key_format_uses_prefix_and_date(self):
        """Key should follow: {prefix}:daily:{company_id}:{YYYY-MM-DD}"""
        client = RedisClient(redis_url="redis://fake", prefix="myrelay", default_limit=500)
        client._available = True
        client._redis = AsyncMock()
        pipe_mock = AsyncMock()
        pipe_mock.incrby = MagicMock(return_value=pipe_mock)
        pipe_mock.ttl = MagicMock(return_value=pipe_mock)
        pipe_mock.execute = AsyncMock(return_value=[1, -1])
        client._redis.pipeline = MagicMock(return_value=pipe_mock)

        await client.check_rate_limit("company-abc")

        # Check the key passed to incrby
        call_args = pipe_mock.incrby.call_args[0]
        key = call_args[0]
        assert key.startswith("myrelay:daily:company-abc:")
        # Should end with a date like 2026-02-16
        date_part = key.split(":")[-1]
        assert len(date_part) == 10  # YYYY-MM-DD

    @pytest.mark.asyncio
    async def test_close_calls_redis_close(self):
        client = self._make_connected_client()
        await client.close()
        client._redis is None
        assert client.is_available is False

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        client = self._make_connected_client()
        client._redis.ping = AsyncMock(return_value=True)
        assert await client.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        client = self._make_connected_client()
        client._redis.ping = AsyncMock(side_effect=Exception("down"))
        assert await client.health_check() is False
        assert client.is_available is False


# ── Multiple Companies ───────────────────────────────────────────────────


class TestRedisClientMultiCompany:
    """Verify different companies get different keys."""

    @pytest.mark.asyncio
    async def test_different_companies_different_keys(self):
        client = RedisClient(redis_url="redis://fake", default_limit=500)
        client._available = True
        client._redis = AsyncMock()

        keys_used = []

        def capture_pipeline():
            pipe = AsyncMock()
            def capture_incrby(key, amount):
                keys_used.append(key)
                return pipe
            pipe.incrby = MagicMock(side_effect=capture_incrby)
            pipe.ttl = MagicMock(return_value=pipe)
            pipe.execute = AsyncMock(return_value=[1, -1])
            return pipe

        client._redis.pipeline = MagicMock(side_effect=capture_pipeline)

        await client.check_rate_limit("company-A")
        await client.check_rate_limit("company-B")

        assert len(keys_used) == 2
        assert "company-A" in keys_used[0]
        assert "company-B" in keys_used[1]
        assert keys_used[0] != keys_used[1]


# ── CSSV1 S7 (R5) Duplicate Lookup ───────────────────────────────────────


class TestRedisClientCheckDuplicate:
    """RedisClient.check_duplicate — primary tier of /api/duplicate/lookup."""

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self):
        """No URL configured → not available → None (route falls back to HB)."""
        client = RedisClient(redis_url="")
        result = await client.check_duplicate(
            tenant_id="tenant-a", file_hash="a" * 64
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_cached_dict_on_hit(self):
        """Redis returns cached side-response — JSON-decoded into dict."""
        client = RedisClient(redis_url="redis://stub")
        client._redis = AsyncMock()
        client._available = True
        cached_payload = {
            "is_duplicate": True,
            "matched_batch_display_id": "BATCH-1",
            "basis": "file_hash",
        }
        import json as _json
        client._redis.get = AsyncMock(return_value=_json.dumps(cached_payload))

        result = await client.check_duplicate(
            tenant_id="tenant-a", file_hash="b" * 64
        )

        assert result == cached_payload

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self):
        """Key not present → None (caller falls back to HB)."""
        client = RedisClient(redis_url="redis://stub")
        client._redis = AsyncMock()
        client._available = True
        client._redis.get = AsyncMock(return_value=None)

        result = await client.check_duplicate(
            tenant_id="tenant-a", file_hash="c" * 64
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_redis_exception_flips_available_off_returns_none(self):
        """Redis exception → mark unavailable + return None. Subsequent
        calls in the same request short-circuit through the disabled
        branch instead of re-raising."""
        client = RedisClient(redis_url="redis://stub")
        client._redis = AsyncMock()
        client._available = True
        client._redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        result = await client.check_duplicate(
            tenant_id="tenant-a", file_hash="d" * 64
        )

        assert result is None
        assert client.is_available is False

    @pytest.mark.asyncio
    async def test_corrupt_cache_entry_returns_none_does_not_disable(self):
        """A bad JSON entry is treated as a miss; Redis itself stays up."""
        client = RedisClient(redis_url="redis://stub")
        client._redis = AsyncMock()
        client._available = True
        client._redis.get = AsyncMock(return_value="not-valid-json{")

        result = await client.check_duplicate(
            tenant_id="tenant-a", file_hash="e" * 64
        )

        assert result is None
        # Redis itself is fine; only this entry is bad.
        assert client.is_available is True

    @pytest.mark.asyncio
    async def test_key_includes_tenant_and_hash(self):
        """Tenant id is the cross-tenant guard at the cache layer —
        wrong-tenant probe gets a different key, hence 'not present'."""
        client = RedisClient(redis_url="redis://stub", prefix="relay")
        client._redis = AsyncMock()
        client._available = True

        keys_seen = []

        async def capture_get(key):
            keys_seen.append(key)
            return None

        client._redis.get = capture_get

        await client.check_duplicate(tenant_id="tenant-a", file_hash="f" * 64)
        await client.check_duplicate(tenant_id="tenant-b", file_hash="f" * 64)

        assert len(keys_seen) == 2
        assert keys_seen[0] != keys_seen[1]
        assert "tenant-a" in keys_seen[0]
        assert "tenant-b" in keys_seen[1]
        assert "f" * 64 in keys_seen[0]
