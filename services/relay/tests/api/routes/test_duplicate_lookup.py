"""
Tests for ``POST /api/duplicate/lookup`` (CSSV1 S7 R5).

Pin:
- Auth: HMAC (ERP) and JWT (user) both route into the handler.
- Body validation: file_hash must be 64 lowercase SHA-256 hex.
- 3-tier degrade: Redis hit / HB-fallback hit / true miss /
  Redis-down / both-down — each maps to one counter label.
- Response shape stable across all branches (matches /api/ingest's
  duplicate side-response).
- Pure preflight: NEVER calls /api/blobs/register or /api/blobs/write.

The autouse ``mock_heartbeat_http`` fixture in
``tests/api/conftest.py`` mocks every HB endpoint the lifespan needs
to start; per-test overrides (`respx_mock.post(...).mock(...)`) take
precedence for the dedup path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.config import RelayConfig
from src.core.auth import compute_signature
from src.observability import counters


# ── Auth helpers ──────────────────────────────────────────────────────────


TEST_API_KEY = "test-key-001"
TEST_SECRET = "secret-001"
TEST_TENANT_ID = "tenant-test-001"


def _hmac_headers(body: bytes) -> dict:
    """3-header ERP HMAC (X-API-Key + X-Timestamp + X-Signature)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = compute_signature(TEST_API_KEY, timestamp, body, TEST_SECRET)
    return {
        "X-API-Key": TEST_API_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_counters():
    """Counters are process-global; reset between cases."""
    counters.reset()
    yield
    counters.reset()


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
        internal_service_token="test-internal-token",
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
    )


@pytest.fixture
def test_secrets():
    return {TEST_API_KEY: TEST_SECRET}


@pytest.fixture
def tenant_registry():
    """Tenants registry — maps api_key → Tenant-ish stub with tenant_id."""
    return {
        TEST_API_KEY: SimpleNamespace(
            tenant_id=TEST_TENANT_ID,
            api_secret=TEST_SECRET,
        ),
    }


@pytest.fixture
async def client(test_config, test_secrets, tenant_registry):
    """ASGI client with lifespan; tenant_registry pre-installed."""
    app = create_app(config=test_config, api_key_secrets=test_secrets)
    async with LifespanManager(app):
        # The lifespan creates an empty tenant_registry because we passed
        # api_key_secrets directly; install ours so the HMAC path resolves
        # the test tenant_id.
        app.state.tenant_registry = tenant_registry
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


def _hash_64() -> str:
    """A valid 64-char lowercase SHA-256 hex digest."""
    return "abcdef0123456789" * 4


def _post_body(file_hash: Optional[str] = None, file_size: Optional[int] = None) -> bytes:
    payload: Dict[str, Any] = {"file_hash": file_hash or _hash_64()}
    if file_size is not None:
        payload["file_size"] = file_size
    return json.dumps(payload).encode("utf-8")


def _counter_value(name: str, labels: Dict[str, str]) -> int:
    """Look up a single counter row's value (0 if absent)."""
    label_tuple = tuple(sorted(labels.items()))
    for n, l, v in counters.get_all():
        if n == name and tuple(sorted(l.items())) == label_tuple:
            return v
    return 0


# ── Auth ──────────────────────────────────────────────────────────────────


class TestDuplicateLookupAuth:
    """Combined dispatcher (PR #9, coverage in PR #17) routes JWT + HMAC."""

    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, client):
        c, _ = client
        body = _post_body()
        resp = await c.post("/api/duplicate/lookup", content=body)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_hmac_path_routes(self, client):
        c, _ = client
        body = _post_body()
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text


# ── Validation ────────────────────────────────────────────────────────────


class TestDuplicateLookupValidation:
    """file_hash must be 64-char lowercase SHA-256 hex."""

    @pytest.mark.asyncio
    async def test_invalid_hash_uppercase_rejected(self, client):
        c, _ = client
        body = json.dumps({"file_hash": "A" * 64}).encode("utf-8")
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_hash_short_rejected(self, client):
        c, _ = client
        body = json.dumps({"file_hash": "abc"}).encode("utf-8")
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_hash_rejected(self, client):
        c, _ = client
        body = json.dumps({}).encode("utf-8")
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_optional_file_size_accepted(self, client):
        c, _ = client
        body = _post_body(file_size=1024)
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_negative_file_size_rejected(self, client):
        c, _ = client
        body = json.dumps({"file_hash": _hash_64(), "file_size": -1}).encode("utf-8")
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422


# ── Tier 1 — Redis primary ────────────────────────────────────────────────


class _FakeRedis:
    """Stand-in for RedisClient that lets us pin the cached payload + flag."""

    def __init__(self, *, available: bool = True, cached: Optional[Dict[str, Any]] = None):
        self.is_available = available
        self._cached = cached
        self.calls: list = []

    async def check_duplicate(self, tenant_id: str, file_hash: str):
        self.calls.append((tenant_id, file_hash))
        return self._cached


class TestDuplicateLookupRedisPrimary:
    """Redis hit returns cached payload without touching HB."""

    @pytest.mark.asyncio
    async def test_redis_hit_returns_cached_shape(self, client):
        c, app = client
        cached = {
            "is_duplicate": True,
            "matched_batch_display_id": "BATCH-2025-001",
            "original_received_at": "2025-12-01T08:00:00Z",
            "original_processed_at": "2025-12-01T08:01:00Z",
            "basis": "file_hash",
            "initiator": {"user_id": "creator-007", "display_name": "Alice"},
        }
        app.state.redis = _FakeRedis(available=True, cached=cached)

        body = _post_body()
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["is_duplicate"] is True
        assert payload["matched_batch_display_id"] == "BATCH-2025-001"
        assert payload["initiator"] == {
            "user_id": "creator-007",
            "display_name": "Alice",
        }
        assert payload["basis"] == "file_hash"

        # Counter: hit_redis exactly once.
        assert _counter_value("relay_duplicate_lookup_total", {"result": "hit_redis"}) == 1
        # Did NOT fall through to HB fallback or miss.
        assert _counter_value("relay_duplicate_lookup_total", {"result": "miss"}) == 0
        assert _counter_value("relay_duplicate_lookup_total", {"result": "hit_hb_fallback"}) == 0

    @pytest.mark.asyncio
    async def test_redis_hit_uses_callers_tenant(self, client):
        """The Redis lookup MUST be tenant-keyed via CallerContext.tenant_id."""
        c, app = client
        fake = _FakeRedis(available=True, cached={"is_duplicate": False})
        app.state.redis = fake

        body = _post_body()
        await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )

        assert len(fake.calls) == 1
        called_tenant, called_hash = fake.calls[0]
        assert called_tenant == TEST_TENANT_ID
        assert called_hash == _hash_64()


# ── Tier 2 — HB fallback ──────────────────────────────────────────────────


class TestDuplicateLookupHbFallback:
    """Redis miss → HB call. HB hit returns enriched response."""

    @pytest.mark.asyncio
    async def test_redis_miss_hb_hit_counts_as_hb_fallback(self, client, respx_mock=None):
        c, app = client
        # Redis available but returns None (cache miss).
        app.state.redis = _FakeRedis(available=True, cached=None)

        with respx.mock:
            respx.post("http://localhost:9000/api/dedup/check").mock(
                return_value=httpx.Response(200, json={
                    "is_duplicate": True,
                    "file_hash": _hash_64(),
                    "original_queue_id": "queue-abc",
                })
            )

            body = _post_body()
            resp = await c.post(
                "/api/duplicate/lookup",
                content=body,
                headers={**_hmac_headers(body), "Content-Type": "application/json"},
            )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["is_duplicate"] is True
        # Phase-1 default: initiator masked when HB doesn't surface
        # uploader identity.
        assert payload["initiator"] == {
            "user_id": "[hidden]",
            "display_name": "[hidden]",
        }
        assert payload["basis"] == "file_hash"
        # HB hasn't surfaced these yet — placeholder None per
        # # TODO(R5-phase2) markers in the route module.
        assert payload["matched_batch_display_id"] is None
        assert payload["original_received_at"] is None
        assert payload["original_processed_at"] is None

        assert _counter_value(
            "relay_duplicate_lookup_total", {"result": "hit_hb_fallback"}
        ) == 1

    @pytest.mark.asyncio
    async def test_redis_miss_hb_miss_counts_as_miss(self, client):
        c, app = client
        app.state.redis = _FakeRedis(available=True, cached=None)

        # Default mock_heartbeat_http returns is_duplicate=False; assert
        # that path increments "miss".
        body = _post_body()
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["is_duplicate"] is False
        assert payload["initiator"] is None

        assert _counter_value(
            "relay_duplicate_lookup_total", {"result": "miss"}
        ) == 1


# ── Degraded — Redis down / both down ─────────────────────────────────────


class TestDuplicateLookupDegraded:
    """Redis unavailable → HB authoritative. Both unavailable → allow miss."""

    @pytest.mark.asyncio
    async def test_redis_down_hb_miss_counts_as_redis_down(self, client):
        c, app = client
        # Redis explicitly unavailable from the start (matches lifespan
        # default when redis_url is empty).
        app.state.redis = _FakeRedis(available=False)

        body = _post_body()
        resp = await c.post(
            "/api/duplicate/lookup",
            content=body,
            headers={**_hmac_headers(body), "Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["is_duplicate"] is False

        assert _counter_value(
            "relay_duplicate_lookup_total", {"result": "redis_down"}
        ) == 1

    @pytest.mark.asyncio
    async def test_both_down_counts_as_both_down(self, client):
        c, app = client
        app.state.redis = _FakeRedis(available=False)

        # Replace HB client with one that raises on check_duplicate.
        original_check = app.state.heartbeat.check_duplicate

        async def _boom(file_hash):
            raise RuntimeError("HB unreachable")

        app.state.heartbeat.check_duplicate = _boom  # type: ignore[assignment]
        try:
            body = _post_body()
            resp = await c.post(
                "/api/duplicate/lookup",
                content=body,
                headers={**_hmac_headers(body), "Content-Type": "application/json"},
            )
        finally:
            app.state.heartbeat.check_duplicate = original_check

        # 200 with miss — data safety > rate limiting.
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["is_duplicate"] is False

        assert _counter_value(
            "relay_duplicate_lookup_total", {"result": "both_down"}
        ) == 1


# ── No bytes uploaded ─────────────────────────────────────────────────────


class TestDuplicateLookupPureRead:
    """R5 must NEVER call /api/blobs/register or /api/blobs/write."""

    @pytest.mark.asyncio
    async def test_does_not_register_blob(self, client):
        c, app = client
        # Default conftest mock allows /api/blobs/register; we assert
        # via the recorded _calls list on the (real) HeartBeatClient.
        # The lifespan-created HB client is the real one but mocked at
        # HTTP via respx; we instead read the _calls property on our
        # stub-style override.

        with respx.mock:
            register_route = respx.post("http://localhost:9000/api/blobs/register").mock(
                return_value=httpx.Response(201, json={
                    "blob_uuid": "should-not-be-called",
                    "status": "registered",
                })
            )
            write_route = respx.post("http://localhost:9000/api/blobs/write").mock(
                return_value=httpx.Response(200, json={"status": "written"})
            )
            # Re-mock dedup since respx.mock is exclusive when used as
            # a context manager.
            respx.post("http://localhost:9000/api/dedup/check").mock(
                return_value=httpx.Response(200, json={
                    "is_duplicate": False,
                    "file_hash": _hash_64(),
                    "original_queue_id": None,
                })
            )

            body = _post_body()
            resp = await c.post(
                "/api/duplicate/lookup",
                content=body,
                headers={**_hmac_headers(body), "Content-Type": "application/json"},
            )

            assert resp.status_code == 200
            assert register_route.call_count == 0
            assert write_route.call_count == 0
