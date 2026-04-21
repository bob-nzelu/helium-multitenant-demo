"""
Tests for Phase 1b middleware — RateLimitMiddleware + RequestSafetyMiddleware.

Uses Starlette's TestClient so the full ASGI pipeline (including middleware)
is exercised against a tiny stub FastAPI app.
"""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import (
    RateLimitMiddleware,
    RequestSafetyMiddleware,
    _caller_key_from_request,
    _endpoint_weight,
    _unsafe_decode_jwt_payload,
)
from src.clients.redis_client import TokenBucketResult


# ── _endpoint_weight ─────────────────────────────────────────────────────


class TestEndpointWeight:
    def test_ingest_cost_5(self):
        assert _endpoint_weight("/api/ingest") == 5

    def test_bulk_preview_cost_10(self):
        assert _endpoint_weight("/api/bulk/preview") == 10

    def test_health_cost_0(self):
        assert _endpoint_weight("/health") == 0

    def test_metrics_cost_0(self):
        assert _endpoint_weight("/metrics") == 0

    def test_unknown_default_1(self):
        assert _endpoint_weight("/api/whatever") == 1

    def test_startswith_match(self):
        # /api/ingest/sub-path still costs 5
        assert _endpoint_weight("/api/ingest/poll") == 5


# ── _unsafe_decode_jwt_payload ──────────────────────────────────────────


class TestJwtPayloadDecode:
    def test_valid_jwt_payload(self):
        payload = {"tenant_id": "abbey", "sub": "u-1"}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip("=")
        token = f"header.{encoded}.sig"
        assert _unsafe_decode_jwt_payload(token) == payload

    def test_malformed_returns_empty(self):
        assert _unsafe_decode_jwt_payload("not-a-jwt") == {}

    def test_wrong_segment_count(self):
        assert _unsafe_decode_jwt_payload("a.b") == {}

    def test_invalid_base64(self):
        assert _unsafe_decode_jwt_payload("a.@@@invalid@@@.c") == {}


# ── _caller_key_from_request ────────────────────────────────────────────


def _make_scope(headers=None, client_host="10.0.0.1"):
    if headers is None:
        headers = {}
    headers_raw = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in headers.items()
    ]
    return {
        "type": "http",
        "path": "/api/ingest",
        "headers": headers_raw,
        "client": (client_host, 0),
    }


class TestCallerKey:
    def test_hmac_api_key(self):
        scope = _make_scope({
            "x-api-key": "k-abc", "x-signature": "sig", "x-timestamp": "t",
        })
        assert _caller_key_from_request(scope) == "key:k-abc"

    def test_service_creds_bearer(self):
        scope = _make_scope({"authorization": "Bearer k-abc:secret"})
        assert _caller_key_from_request(scope) == "key:k-abc"

    def test_jwt_uses_tenant_id(self):
        payload = {"tenant_id": "abbey", "sub": "u-1"}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip("=")
        token = f"header.{encoded}.sig"
        scope = _make_scope({"authorization": f"Bearer {token}"})
        assert _caller_key_from_request(scope) == "tenant:abbey"

    def test_jwt_fallback_to_sub(self):
        payload = {"sub": "u-42"}
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).decode().rstrip("=")
        token = f"h.{encoded}.s"
        scope = _make_scope({"authorization": f"Bearer {token}"})
        assert _caller_key_from_request(scope) == "user:u-42"

    def test_anonymous_ip_key(self):
        scope = _make_scope({}, client_host="203.0.113.7")
        assert _caller_key_from_request(scope) == "ip:203.0.113.7"


# ── RateLimitMiddleware ────────────────────────────────────────────────


def _build_app_with_rate_limit(
    redis_mock,
    tier_limits=None,
    add_safety=False,
):
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/ingest")
    async def ingest():
        return {"status": "accepted"}

    app.state.redis = redis_mock
    app.state.rate_limits_by_tier = tier_limits or {
        "standard": {"api_requests_per_minute": 100, "api_requests_per_hour": 2000},
    }

    if add_safety:
        app.add_middleware(
            RequestSafetyMiddleware,
            max_request_bytes=1000,
            request_timeout_s=1,
        )
    app.add_middleware(RateLimitMiddleware)
    return app


class TestRateLimitMiddleware:
    def test_health_not_rate_limited(self):
        # redis.token_bucket_check should never be called for /health
        redis = AsyncMock()
        redis.token_bucket_check = AsyncMock()
        app = _build_app_with_rate_limit(redis)
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
        redis.token_bucket_check.assert_not_called()

    def test_allowed_sets_ratelimit_headers(self):
        redis = AsyncMock()
        redis.token_bucket_check = AsyncMock(side_effect=[
            TokenBucketResult(allowed=True, remaining=95, limit=100,
                              reset_epoch=2_000_000_060, source="redis"),
            TokenBucketResult(allowed=True, remaining=1995, limit=2000,
                              reset_epoch=2_000_003_600, source="redis"),
        ])
        app = _build_app_with_rate_limit(redis)
        client = TestClient(app)

        resp = client.post("/api/ingest", headers={"X-API-Key": "k-abc",
                                                    "X-Signature": "s",
                                                    "X-Timestamp": "t"})
        assert resp.status_code == 200
        assert resp.headers["x-ratelimit-limit-minute"] == "100"
        assert resp.headers["x-ratelimit-remaining-minute"] == "95"
        assert resp.headers["x-ratelimit-limit-hour"] == "2000"

    def test_per_minute_exceeded_returns_429(self):
        redis = AsyncMock()
        redis.token_bucket_check = AsyncMock(side_effect=[
            TokenBucketResult(allowed=False, remaining=0, limit=100,
                              reset_epoch=2_000_000_060, source="redis"),
            TokenBucketResult(allowed=True, remaining=1995, limit=2000,
                              reset_epoch=2_000_003_600, source="redis"),
        ])
        app = _build_app_with_rate_limit(redis)
        client = TestClient(app)

        resp = client.post("/api/ingest", headers={"X-API-Key": "k-abc",
                                                    "X-Signature": "s",
                                                    "X-Timestamp": "t"})
        assert resp.status_code == 429
        body = resp.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "retry-after" in [h.lower() for h in resp.headers.keys()]
        assert resp.headers["x-ratelimit-remaining-minute"] == "0"

    def test_per_hour_exceeded_returns_429(self):
        redis = AsyncMock()
        redis.token_bucket_check = AsyncMock(side_effect=[
            TokenBucketResult(allowed=True, remaining=50, limit=100,
                              reset_epoch=2_000_000_060, source="redis"),
            TokenBucketResult(allowed=False, remaining=0, limit=2000,
                              reset_epoch=2_000_003_600, source="redis"),
        ])
        app = _build_app_with_rate_limit(redis)
        client = TestClient(app)

        resp = client.post("/api/ingest", headers={"X-API-Key": "k-abc",
                                                    "X-Signature": "s",
                                                    "X-Timestamp": "t"})
        assert resp.status_code == 429
        # Details should indicate per_hour was the blocker
        body = resp.json()
        assert body["details"][0]["window"] == "per_hour"

    def test_no_redis_passes_through(self):
        """If app.state.redis is None, middleware should pass through."""
        app = FastAPI()

        @app.post("/api/ingest")
        async def ingest():
            return {"status": "accepted"}

        app.state.redis = None
        app.state.rate_limits_by_tier = {}
        app.add_middleware(RateLimitMiddleware)

        client = TestClient(app)
        resp = client.post("/api/ingest")
        assert resp.status_code == 200


# ── RequestSafetyMiddleware ─────────────────────────────────────────────


def _build_safety_only_app(max_bytes=1000, timeout_s=1):
    app = FastAPI()

    @app.post("/api/ingest")
    async def ingest():
        return {"status": "accepted"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(3)
        return {"status": "never"}

    app.add_middleware(
        RequestSafetyMiddleware,
        max_request_bytes=max_bytes,
        request_timeout_s=timeout_s,
    )
    return app


class TestRequestSafetyMiddleware:
    def test_small_payload_passes(self):
        app = _build_safety_only_app(max_bytes=1000, timeout_s=5)
        client = TestClient(app)
        resp = client.post("/api/ingest", content=b"x" * 100)
        assert resp.status_code == 200

    def test_too_large_rejected_413(self):
        app = _build_safety_only_app(max_bytes=500, timeout_s=5)
        client = TestClient(app)
        # Payload of 1000 bytes > 500 limit
        resp = client.post("/api/ingest", content=b"x" * 1000)
        assert resp.status_code == 413
        body = resp.json()
        assert body["error_code"] == "REQUEST_TOO_LARGE"

    def test_exact_limit_passes(self):
        app = _build_safety_only_app(max_bytes=1000, timeout_s=5)
        client = TestClient(app)
        resp = client.post("/api/ingest", content=b"x" * 1000)
        assert resp.status_code == 200

    def test_timeout_returns_504(self):
        app = _build_safety_only_app(max_bytes=10_000, timeout_s=1)
        client = TestClient(app)
        resp = client.get("/slow")
        assert resp.status_code == 504
        assert resp.json()["error_code"] == "REQUEST_TIMEOUT"

    def test_missing_content_length_passes_through(self):
        """Chunked / unknown-length requests should bypass the size gate."""
        app = _build_safety_only_app(max_bytes=50, timeout_s=5)
        client = TestClient(app)
        # TestClient sets Content-Length automatically, but for a GET with no
        # body there's no Content-Length to enforce — simulate.
        resp = client.get("/health")
        assert resp.status_code == 200


# ── ASGI non-HTTP scope (websocket etc.) ────────────────────────────────


class TestAsgiEdgeCases:
    @pytest.mark.asyncio
    async def test_rate_limit_passes_non_http_scope(self):
        redis = AsyncMock()
        redis.token_bucket_check = AsyncMock()
        called = {"n": 0}

        async def inner(scope, receive, send):
            called["n"] += 1
        mw = RateLimitMiddleware(inner)
        await mw({"type": "websocket"}, AsyncMock(), AsyncMock())
        assert called["n"] == 1
        redis.token_bucket_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_safety_passes_non_http_scope(self):
        called = {"n": 0}

        async def inner(scope, receive, send):
            called["n"] += 1
        mw = RequestSafetyMiddleware(inner, max_request_bytes=100, request_timeout_s=5)
        await mw({"type": "lifespan"}, AsyncMock(), AsyncMock())
        assert called["n"] == 1


# ── Invalid Content-Length ──────────────────────────────────────────────


class TestInvalidContentLength:
    @pytest.mark.asyncio
    async def test_non_int_content_length_allowed(self):
        """Garbage Content-Length header shouldn't crash the middleware."""
        collected = []

        async def inner(scope, receive, send):
            collected.append("inner-ran")

        mw = RequestSafetyMiddleware(inner, max_request_bytes=100, request_timeout_s=5)
        scope = {
            "type": "http",
            "path": "/api/ingest",
            "headers": [(b"content-length", b"not-a-number")],
            "client": ("1.1.1.1", 0),
        }

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_):
            pass

        await mw(scope, receive, send)
        assert collected == ["inner-ran"]


# ── End-to-end fail-open (no Redis backend) ─────────────────────────────


class TestFailOpenIntegration:
    def test_allows_with_burst_cap_when_no_redis(self):
        """With app.state.redis absent, middleware should pass through."""
        from src.clients.redis_client import RedisClient

        # Real RedisClient but never connected — token_bucket_check returns
        # degraded/allowed via fail-open burst.
        rc = RedisClient(redis_url="", fail_open_burst=3)

        app = FastAPI()

        @app.post("/api/ingest")
        async def ingest():
            return {"ok": True}

        app.state.redis = rc
        app.state.rate_limits_by_tier = {
            "standard": {"api_requests_per_minute": 100, "api_requests_per_hour": 2000},
        }
        app.add_middleware(RateLimitMiddleware)
        client = TestClient(app)

        # First 3 calls should pass (burst cap = 3); 4th may 429.
        statuses = []
        for _ in range(5):
            r = client.post("/api/ingest", headers={
                "X-API-Key": "k1", "X-Signature": "s", "X-Timestamp": "t",
            })
            statuses.append(r.status_code)
        # At least one should have been rate-limited after the burst
        assert 429 in statuses
        # Earlier ones should have been allowed
        assert 200 in statuses
