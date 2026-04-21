"""
Tests for RedisClient Phase 1b extensions — token bucket + nonce claim.

Mock Redis throughout. Fail-open burst is exercised without a Redis
backend (the burst cap is in-process).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.clients.redis_client import RedisClient, TokenBucketResult


def _make_connected(default_limit=500, burst=10):
    client = RedisClient(
        redis_url="redis://fake:6379/0",
        prefix="relay",
        default_limit=default_limit,
        fail_open_burst=burst,
    )
    client._available = True
    client._redis = AsyncMock()
    return client


# ── token_bucket_check — happy path ─────────────────────────────────────


class TestTokenBucketHappyPath:
    @pytest.mark.asyncio
    async def test_first_call_allowed_full_bucket(self):
        client = _make_connected()
        # Simulate Lua returning [allowed, remaining, reset_epoch]
        client._redis.eval = AsyncMock(return_value=[1, 99, 2_000_000_060])

        r = await client.token_bucket_check("key:abc", "per_minute", 100, cost=1)
        assert r.allowed is True
        assert r.limit == 100
        assert r.remaining == 99
        assert r.source == "redis"

    @pytest.mark.asyncio
    async def test_rejected_when_empty(self):
        client = _make_connected()
        client._redis.eval = AsyncMock(return_value=[0, 0, 2_000_000_060])

        r = await client.token_bucket_check("key:abc", "per_minute", 100, cost=5)
        assert r.allowed is False
        assert r.remaining == 0

    @pytest.mark.asyncio
    async def test_cost_weight_applied(self):
        client = _make_connected()
        client._redis.eval = AsyncMock(return_value=[1, 95, 2_000_000_060])

        await client.token_bucket_check("key:x", "per_minute", 100, cost=5)
        call_args = client._redis.eval.call_args
        # args: (lua_script, num_keys, key, capacity, refill, cost, now, ttl)
        # Positional ARGV[3] (cost) lives at index 5 of args
        assert call_args[0][5] == 5  # cost passed through


# ── token_bucket_check — unlimited + invalid window ─────────────────────


class TestTokenBucketSpecialCases:
    @pytest.mark.asyncio
    async def test_capacity_zero_means_unlimited(self):
        client = _make_connected()
        client._redis.eval = AsyncMock()  # must not be called

        r = await client.token_bucket_check("key:x", "per_hour", 0)
        assert r.allowed is True
        assert r.source == "unlimited"
        client._redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_window_raises(self):
        client = _make_connected()
        with pytest.raises(ValueError, match="Unknown window"):
            await client.token_bucket_check("key:x", "per_second", 100)

    @pytest.mark.asyncio
    async def test_per_hour_uses_3600_refill_base(self):
        client = _make_connected()
        client._redis.eval = AsyncMock(return_value=[1, 1999, 2_000_003_600])

        await client.token_bucket_check("key:x", "per_hour", 2000, cost=1)
        call_args = client._redis.eval.call_args
        # refill (ARGV[2]) = capacity / window_seconds
        # positional: (script, 1, key, capacity, refill, cost, now, ttl)
        capacity = call_args[0][3]
        refill = call_args[0][4]
        assert capacity == 2000
        assert refill == pytest.approx(2000 / 3600)


# ── Fail-open burst cap (Redis unreachable) ─────────────────────────────


class TestFailOpenBurst:
    @pytest.mark.asyncio
    async def test_first_request_allowed_when_redis_down(self):
        client = RedisClient(redis_url="", fail_open_burst=5)
        # Not connected → _available False, _redis None
        r = await client.token_bucket_check("key:x", "per_minute", 100)
        assert r.allowed is True
        assert r.source == "degraded"
        assert r.remaining == 4  # 5 - 1 already used

    @pytest.mark.asyncio
    async def test_burst_cap_blocks_past_threshold(self):
        client = RedisClient(redis_url="", fail_open_burst=3)
        results = []
        for _ in range(5):
            results.append(
                await client.token_bucket_check("key:x", "per_minute", 100)
            )
        # First 3 allowed, last 2 rejected within the same second
        allowed = [r.allowed for r in results]
        assert allowed[:3] == [True, True, True]
        assert allowed[3:] == [False, False]

    @pytest.mark.asyncio
    async def test_burst_cap_per_caller_independent(self):
        client = RedisClient(redis_url="", fail_open_burst=2)
        a1 = await client.token_bucket_check("key:A", "per_minute", 100)
        a2 = await client.token_bucket_check("key:A", "per_minute", 100)
        a3 = await client.token_bucket_check("key:A", "per_minute", 100)
        b1 = await client.token_bucket_check("key:B", "per_minute", 100)
        assert [a1.allowed, a2.allowed, a3.allowed] == [True, True, False]
        assert b1.allowed is True  # B's bucket is separate

    @pytest.mark.asyncio
    async def test_redis_eval_failure_degrades(self):
        client = _make_connected(burst=2)
        client._redis.eval = AsyncMock(side_effect=Exception("Redis down"))

        r = await client.token_bucket_check("key:x", "per_minute", 100)
        assert r.allowed is True
        assert r.source == "degraded"
        assert client.is_available is False


# ── nonce_claim ─────────────────────────────────────────────────────────


class TestNonceClaim:
    @pytest.mark.asyncio
    async def test_first_claim_true(self):
        client = _make_connected()
        client._redis.set = AsyncMock(return_value="OK")

        ok = await client.nonce_claim("abc-123", ttl_s=600)
        assert ok is True

    @pytest.mark.asyncio
    async def test_replay_claim_false(self):
        client = _make_connected()
        # SETNX returns None when key already existed
        client._redis.set = AsyncMock(return_value=None)

        ok = await client.nonce_claim("abc-123", ttl_s=600)
        assert ok is False

    @pytest.mark.asyncio
    async def test_uses_prefix_and_nonce_key(self):
        client = _make_connected()
        client._redis.set = AsyncMock(return_value="OK")

        await client.nonce_claim("my-nonce")
        call_kwargs = client._redis.set.call_args
        key = call_kwargs[0][0]
        assert key == "relay:nonce:my-nonce"
        assert call_kwargs[1].get("nx") is True
        assert call_kwargs[1].get("ex") == 600

    @pytest.mark.asyncio
    async def test_redis_down_fails_open_true(self):
        client = RedisClient(redis_url="")  # never connected
        ok = await client.nonce_claim("abc")
        assert ok is True

    @pytest.mark.asyncio
    async def test_redis_error_degrades_allow(self):
        client = _make_connected()
        client._redis.set = AsyncMock(side_effect=Exception("down"))

        ok = await client.nonce_claim("abc")
        assert ok is True
        assert client.is_available is False
