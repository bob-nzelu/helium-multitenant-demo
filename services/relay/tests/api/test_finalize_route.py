"""
Tests for POST /api/finalize — the #3 reference-only fiscalize call (R-M2).

Covers (§B-Submit / SCOUT contract §3.3):
    - reference-only shape: NO bytes; ref/trace_id in the JSON body
    - trace_id echo on the response (and on the relay.finalize.accepted event)
    - 409 ALREADY_FINALIZED on a duplicate trace_id (client treats as success)
    - the lifecycle event relay.finalize.accepted is emitted via the publisher
      seam (mock the Core sink) with the trace_id echoed
    - finalize=false vs finalize=true routing on /api/ingest (the contract axis)

Auth: HMAC over the exact JSON body bytes (the #3 call has no multipart, so the
signature is computable — unlike the ingest-route tests which intentionally
fail HMAC on multipart).
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig
from src.core.auth import compute_signature
from src.services.lifecycle import (
    FAMILY_FINALIZE_ACCEPTED,
    RecordingLifecyclePublisher,
)
from src.services.finalize import FinalizeService


TEST_API_KEY = "test-key-001"
TEST_SECRET = "secret-001"


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
async def client_and_publisher(test_config, test_secrets):
    """App with the lifecycle publisher swapped for an in-memory recorder so we
    can assert relay.finalize.accepted was emitted with the trace_id echo.

    The seam is the whole point: we replace app.state.lifecycle_publisher and
    rebuild the finalize_service around it AFTER lifespan startup, exercising
    the swappability the contract requires (Q15 topology unresolved).
    """
    app = create_app(config=test_config, api_key_secrets=test_secrets)
    async with LifespanManager(app):
        recorder = RecordingLifecyclePublisher()
        app.state.lifecycle_publisher = recorder
        app.state.finalize_service = FinalizeService(app.state.core, recorder)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, recorder


def _hmac_headers_for_body(body: bytes) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sig = compute_signature(TEST_API_KEY, ts, body, TEST_SECRET)
    return {
        "X-API-Key": TEST_API_KEY,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _post_finalize_args(payload: dict) -> tuple[bytes, dict]:
    """Serialise the JSON body and build matching HMAC headers.

    We pass the EXACT bytes as ``content=`` so the signed body equals the wire
    body (httpx's ``json=`` would re-serialise and could differ).
    """
    body = json.dumps(payload).encode("utf-8")
    return body, _hmac_headers_for_body(body)


# ── Reference-only shape + trace_id echo ─────────────────────────────────


class TestFinalizeReferenceOnly:
    @pytest.mark.asyncio
    async def test_finalize_by_ref_no_bytes_returns_202(self, client_and_publisher):
        client, _ = client_and_publisher
        body, headers = _post_finalize_args(
            {"ref": "sha256:abc123", "trace_id": "018f-trace-aaa"}
        )
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["call"] == "finalize"
        assert data["finalize_by_reference"] is True
        assert data["raw_bytes_sent"] is False
        assert data["ref"] == "sha256:abc123"
        assert data["trace_id"] == "018f-trace-aaa"
        assert data["event_family"] == FAMILY_FINALIZE_ACCEPTED
        assert data["idempotent_replay"] is False

    @pytest.mark.asyncio
    async def test_trace_id_echoed_on_lifecycle_event(self, client_and_publisher):
        client, recorder = client_and_publisher
        body, headers = _post_finalize_args(
            {"ref": "sha256:doc-xyz", "trace_id": "018f-echo-me"}
        )
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.status_code == 202

        # relay.finalize.accepted emitted via the seam, trace_id echoed both in
        # the event and lifted to top-level on the frame (events.py:530-532).
        assert len(recorder.events) == 1
        evt = recorder.last()
        assert evt.family == FAMILY_FINALIZE_ACCEPTED
        assert evt.trace_id == "018f-echo-me"
        frame = recorder.frames[-1]
        assert frame["trace_id"] == "018f-echo-me"
        assert frame["data"]["trace_id"] == "018f-echo-me"
        assert frame["data"]["raw_bytes_in_event"] is False
        assert frame["data"]["finalize_by_reference"] is True

    @pytest.mark.asyncio
    async def test_finalize_with_only_trace_id(self, client_and_publisher):
        """ref may be empty if a trace_id is supplied (it is a valid ref)."""
        client, recorder = client_and_publisher
        body, headers = _post_finalize_args({"trace_id": "018f-only-trace"})
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.status_code == 202
        assert resp.json()["trace_id"] == "018f-only-trace"
        assert recorder.last().trace_id == "018f-only-trace"

    @pytest.mark.asyncio
    async def test_finalize_missing_reference_400(self, client_and_publisher):
        """Neither ref nor trace_id → 400 (never a silent no-op)."""
        client, recorder = client_and_publisher
        body, headers = _post_finalize_args({})
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "VALIDATION_FAILED"
        # No lifecycle event for a rejected finalize.
        assert recorder.events == []


# ── 409 on duplicate / already-finalized trace_id ────────────────────────


class TestFinalizeDuplicate409:
    @pytest.mark.asyncio
    async def test_duplicate_trace_id_returns_409(self, client_and_publisher):
        client, recorder = client_and_publisher
        payload = {"ref": "sha256:dup", "trace_id": "018f-dup-trace"}

        body1, headers1 = _post_finalize_args(payload)
        first = await client.post("/api/finalize", content=body1, headers=headers1)
        assert first.status_code == 202

        # Fresh headers (new timestamp), SAME trace_id → 409 ALREADY_FINALIZED.
        body2, headers2 = _post_finalize_args(payload)
        second = await client.post("/api/finalize", content=body2, headers=headers2)
        assert second.status_code == 409, second.text
        err = second.json()
        assert err["error_code"] == "ALREADY_FINALIZED"

        # Only ONE lifecycle event — the duplicate did not re-emit / re-trigger.
        assert len(recorder.events) == 1

    @pytest.mark.asyncio
    async def test_duplicate_by_ref_when_no_trace(self, client_and_publisher):
        """Dedup also works when only a ref is supplied (no trace_id)."""
        client, recorder = client_and_publisher
        payload = {"ref": "sha256:ref-only-dup"}

        b1, h1 = _post_finalize_args(payload)
        assert (await client.post("/api/finalize", content=b1, headers=h1)).status_code == 202
        b2, h2 = _post_finalize_args(payload)
        second = await client.post("/api/finalize", content=b2, headers=h2)
        assert second.status_code == 409
        assert len(recorder.events) == 1


# ── Auth on the finalize route ───────────────────────────────────────────


class TestFinalizeAuth:
    @pytest.mark.asyncio
    async def test_no_credentials_401(self, client_and_publisher):
        client, _ = client_and_publisher
        body = json.dumps({"ref": "x", "trace_id": "t"}).encode("utf-8")
        resp = await client.post(
            "/api/finalize", content=body, headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTHENTICATION_FAILED"

    @pytest.mark.asyncio
    async def test_bad_signature_401(self, client_and_publisher):
        client, _ = client_and_publisher
        body = json.dumps({"ref": "x", "trace_id": "t"}).encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        headers = {
            "X-API-Key": TEST_API_KEY,
            "X-Timestamp": ts,
            "X-Signature": "deadbeef",  # wrong
            "Content-Type": "application/json",
        }
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_trace_id_header_echoed_in_response_headers(self, client_and_publisher):
        client, _ = client_and_publisher
        body, headers = _post_finalize_args({"ref": "r", "trace_id": "018f-hdr"})
        headers["X-Trace-ID"] = "request-trace-zzz"
        resp = await client.post("/api/finalize", content=body, headers=headers)
        assert resp.headers["x-trace-id"] == "request-trace-zzz"


# ── §B-Submit: metadata.finalize routing on /api/ingest ──────────────────


class TestIngestFinalizeAxis:
    """metadata.finalize is the contract axis; assert it drives the path.

    We assert routing via the response *shape*: the external (#2, finalize=true)
    path returns irn+qr_code; the bulk (#1, finalize=false) path returns
    status="queued" and never irn/qr_code (Q4: the bulk preview is no longer
    inline — it arrives via the core.preview.available lifecycle event).
    Multipart bodies can't be HMAC-signed in these tests (same constraint as
    test_ingest_route.py), so we drive the router via a JWT-introspect
    monkeypatch to reach the handler with a real CallerContext.
    """

    @staticmethod
    def _patch_user_introspect(app, monkeypatch):
        from src.api import deps as deps_mod

        async def _fake_user(request, jwt_token, bypass_cache=False):
            from src.api.caller_context import CallerContext
            return CallerContext(
                actor_type="user",
                tenant_id="test-tenant-001",
                identifier="user-123",
                permissions=["*"],
                trace_id=getattr(request.state, "trace_id", ""),
                downstream_auth_header=f"Bearer {jwt_token}",
            )

        monkeypatch.setattr(deps_mod, "_verify_user_jwt", _fake_user)

    @pytest.mark.asyncio
    async def test_finalize_true_routes_external_irn_qr(
        self, test_config, test_secrets, monkeypatch
    ):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        self._patch_user_introspect(app, monkeypatch)
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/api/ingest",
                    files={"files": ("inv.pdf", b"%PDF-1.4 finalize-true", "application/pdf")},
                    data={"call_type": "bulk", "metadata": json.dumps({"finalize": True})},
                    headers={"Authorization": "Bearer eyJ.eyJ.SIG"},
                )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # finalize=true overrode call_type=bulk → external path → irn+qr present.
        assert data["irn"] is not None
        assert data["qr_code"] is not None
        # preview_data was removed from the response model entirely (Q4).
        assert "preview_data" not in data

    @pytest.mark.asyncio
    async def test_finalize_false_routes_bulk_preview(
        self, test_config, test_secrets, monkeypatch
    ):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        self._patch_user_introspect(app, monkeypatch)
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/api/ingest",
                    files={"files": ("inv.pdf", b"%PDF-1.4 finalize-false", "application/pdf")},
                    data={"call_type": "external", "metadata": json.dumps({"finalize": False})},
                    headers={"Authorization": "Bearer eyJ.eyJ.SIG"},
                )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # finalize=false overrode call_type=external → bulk path. Bulk now
        # returns status="queued" with no irn/qr and no inline preview (Q4).
        assert data["irn"] is None
        assert data["qr_code"] is None
        assert data["status"] == "queued"
        assert "preview_data" not in data
