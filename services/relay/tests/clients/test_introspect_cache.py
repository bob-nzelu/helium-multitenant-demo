"""
Tests for IntrospectClient cache (Keel HANDOFF_RELAY_JWT_INTROSPECT.md §5).

Mocks httpx.AsyncClient.post to avoid real HB calls. Verifies:
  - cache hits skip HTTP
  - TTL expiry forces fresh HTTP call
  - negative cache re-raises the original error
  - jti extraction handles malformed tokens
  - LRU eviction when over cache_max
"""

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.introspect import (
    IntrospectClient,
    IntrospectResult,
    _extract_jti,
)
from src.errors import JWTRejectedError, HeartBeatUnavailableError


def _make_jwt(payload: dict) -> str:
    """Compact JWS shape; signature doesn't matter for introspect tests."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _make_http_response(body: dict, status: int = 200):
    """Build a fake httpx.Response."""
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.is_success = 200 <= status < 300
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def _make_client(cache_ttl_s=30.0, cache_max=100):
    return IntrospectClient(
        heartbeat_url="http://hb.test",
        service_api_key="svc-key",
        service_api_secret="svc-secret",
        timeout_s=5.0,
        cache_ttl_s=cache_ttl_s,
        cache_max_entries=cache_max,
    )


# ── _extract_jti ──────────────────────────────────────────────────────


class TestExtractJti:
    def test_present(self):
        token = _make_jwt({"jti": "abc-123", "sub": "u-1"})
        assert _extract_jti(token) == "abc-123"

    def test_missing(self):
        token = _make_jwt({"sub": "u-1"})  # no jti
        assert _extract_jti(token) is None

    def test_malformed(self):
        assert _extract_jti("not-a-jwt") is None
        assert _extract_jti("a.b") is None

    def test_non_string_jti_stringified(self):
        token = _make_jwt({"jti": 12345, "sub": "u-1"})
        assert _extract_jti(token) == "12345"


# ── Cache hit/miss ────────────────────────────────────────────────────


class TestCacheHit:
    @pytest.mark.asyncio
    async def test_second_call_hits_cache(self):
        client = _make_client()
        token = _make_jwt({"jti": "j-1", "sub": "u-1"})
        positive_body = {
            "active": True, "user_id": "u-1", "tenant_id": "t-1",
            "permissions": ["blob.upload"], "step_up_satisfied": True,
        }

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(positive_body))
            mock_client_factory.return_value = http_mock

            r1 = await client.introspect(token)
            r2 = await client.introspect(token)

            assert r1.active is True
            assert r2.active is True
            # Second call served from cache
            assert http_mock.post.call_count == 1
            stats = client.cache_stats()
            assert stats["hits"] >= 1
            assert stats["misses"] >= 1

    @pytest.mark.asyncio
    async def test_different_tokens_different_cache_entries(self):
        client = _make_client()
        t1 = _make_jwt({"jti": "j-1"})
        t2 = _make_jwt({"jti": "j-2"})
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            await client.introspect(t1)
            await client.introspect(t2)
            # Two distinct jtis → two HTTP calls
            assert http_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_different_permissions_different_cache_slots(self):
        """Same jti + different required_permission → separate cache slots."""
        client = _make_client()
        token = _make_jwt({"jti": "j-1"})
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            await client.introspect(token, required_permission="blob.upload")
            await client.introspect(token, required_permission="invoice.approve")
            assert http_mock.post.call_count == 2


# ── TTL expiry ─────────────────────────────────────────────────────────


class TestCacheTtl:
    @pytest.mark.asyncio
    async def test_expiry_forces_refetch(self):
        client = _make_client(cache_ttl_s=0.05)  # 50ms TTL
        token = _make_jwt({"jti": "j-1"})
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            await client.introspect(token)
            await asyncio.sleep(0.1)  # past TTL
            await client.introspect(token)
            assert http_mock.post.call_count == 2

    @pytest.mark.asyncio
    async def test_bypass_cache_forces_refetch(self):
        client = _make_client()
        token = _make_jwt({"jti": "j-1"})
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            await client.introspect(token)
            await client.introspect(token, bypass_cache=True)
            assert http_mock.post.call_count == 2


# ── Negative cache ─────────────────────────────────────────────────────


class TestNegativeCache:
    @pytest.mark.asyncio
    async def test_rejected_token_cached_and_reraised(self):
        """A revoked-token response should be cached so repeat calls don't hit HB."""
        client = _make_client()
        token = _make_jwt({"jti": "j-revoked"})
        neg_body = {
            "active": False, "error_code": "TOKEN_REVOKED",
            "message": "Session revoked",
        }

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(neg_body))
            mock_client_factory.return_value = http_mock

            with pytest.raises(JWTRejectedError) as first:
                await client.introspect(token)
            with pytest.raises(JWTRejectedError) as second:
                await client.introspect(token)

            assert first.value.error_code == "TOKEN_REVOKED"
            assert second.value.error_code == "TOKEN_REVOKED"
            # Only one HTTP call — second served from negative cache
            assert http_mock.post.call_count == 1


# ── LRU bounds ─────────────────────────────────────────────────────────


class TestLruBounds:
    @pytest.mark.asyncio
    async def test_evicts_oldest_when_over_max(self):
        client = _make_client(cache_max=3)
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            for i in range(5):
                await client.introspect(_make_jwt({"jti": f"j-{i}"}))

            assert client.cache_stats()["size"] == 3  # only last 3 survive


# ── Tokens without jti ─────────────────────────────────────────────────


class TestNoJti:
    @pytest.mark.asyncio
    async def test_token_without_jti_skips_cache(self):
        client = _make_client()
        token = _make_jwt({"sub": "u-1"})  # no jti
        body = {"active": True, "user_id": "u", "tenant_id": "t",
                "permissions": [], "step_up_satisfied": True}

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response(body))
            mock_client_factory.return_value = http_mock

            await client.introspect(token)
            await client.introspect(token)
            # Both calls hit HTTP — no cache key available
            assert http_mock.post.call_count == 2


# ── Error propagation ─────────────────────────────────────────────────


class TestErrors:
    @pytest.mark.asyncio
    async def test_upstream_5xx_raises_unavailable(self):
        client = _make_client()
        token = _make_jwt({"jti": "j-1"})

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(return_value=_make_http_response({}, status=503))
            mock_client_factory.return_value = http_mock

            with pytest.raises(HeartBeatUnavailableError):
                await client.introspect(token)

    @pytest.mark.asyncio
    async def test_connect_error_raises_unavailable(self):
        client = _make_client()
        token = _make_jwt({"jti": "j-1"})

        with patch.object(client, "_client") as mock_client_factory:
            http_mock = AsyncMock()
            http_mock.post = AsyncMock(side_effect=httpx.ConnectError("down"))
            mock_client_factory.return_value = http_mock

            with pytest.raises(HeartBeatUnavailableError):
                await client.introspect(token)

    @pytest.mark.asyncio
    async def test_own_creds_missing_raises(self):
        client = IntrospectClient(
            heartbeat_url="http://hb.test",
            service_api_key="",
            service_api_secret="",
        )
        from src.errors import AuthenticationFailedError
        with pytest.raises(AuthenticationFailedError):
            await client.introspect(_make_jwt({"jti": "j"}))
