"""
Tests for POST /api/artifacts/fetch — §B-RelayArtifactFetch.

Coverage:
    - the route is **POST** with a JSON body; there is no GET variant and
      ``artifact_ref`` is read from the BODY, not the path (VERB_DELTA);
    - HARD artifact kinds return raw **bytes** with the per-kind Content-Type;
    - LIFECYCLE artifact kinds return raw **JSON** (sourced from Core);
    - a miss returns 404 with the exact body
      ``{"code": "ARTIFACT_NOT_FOUND", "artifact_ref": <ref>}``;
    - auth is required (the shared dispatcher rejects a credential-less call);
    - ``ETag`` / ``X-Artifact-Ref`` response headers mirror the SBS.

The HB/Core client methods are mocked on the live ``app.state`` instances after
lifespan startup, so these tests prove the ROUTE is correct without a real HB
blob store or Core lifecycle store (NEEDS-HB / NEEDS-CORE).
"""

import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.api.caller_context import CallerContext
from src.api.deps import authenticate_request
from src.api.routes.artifacts import classify_artifact
from src.config import RelayConfig


# ── Fixtures ──────────────────────────────────────────────────────────────


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


def _fake_ctx() -> CallerContext:
    """A resolved service-caller context used to bypass real auth."""
    return CallerContext(
        actor_type="service",
        tenant_id="test-tenant-001",
        identifier="relay-test-svc",
        permissions=["*"],
        trace_id="test-trace",
    )


@pytest.fixture
async def app_and_client(test_config):
    """App + ASGI client. Auth is overridden to a fake service context so the
    data-shape tests focus on bytes-vs-JSON routing; the auth-required test
    uses a separate un-overridden client below."""
    app = create_app(config=test_config, api_key_secrets={"k": "s"})
    app.dependency_overrides[authenticate_request] = _fake_ctx
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield app, c
    app.dependency_overrides.clear()


# ── HARD artifact → bytes ─────────────────────────────────────────────────


class TestHardArtifactBytes:
    @pytest.mark.asyncio
    async def test_signed_pdf_returns_pdf_bytes(self, app_and_client):
        app, client = app_and_client
        pdf_bytes = b"%PDF-1.4\nsigned artifact body\n%%EOF\n"

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            assert artifact_ref == "blob-signed-001"
            return {"content_type": "application/pdf", "data": pdf_bytes}

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "blob-signed-001", "artifact_type": "signed_pdf"},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == pdf_bytes
        assert resp.headers["x-artifact-ref"] == "blob-signed-001"
        assert resp.headers["etag"].startswith("sha256:")

    @pytest.mark.asyncio
    async def test_qr_invoice_content_type(self, app_and_client):
        app, client = app_and_client
        qr_bytes = b'{"irn": "ABC-123", "qr": true}'

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            # HB may report octet-stream; the route serves the per-KIND type.
            return {"content_type": "application/octet-stream", "data": qr_bytes}

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "blob-qr-001", "artifact_type": "qr_invoice"},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.helium.invoice-qr+json"
        assert resp.content == qr_bytes


# ── LIFECYCLE artifact → JSON ─────────────────────────────────────────────


class TestLifecycleArtifactJson:
    @pytest.mark.asyncio
    async def test_hlx_returns_raw_json(self, app_and_client):
        app, client = app_and_client
        payload = {"document_id": "doc-1", "summary": "HLX body", "artifact_family": "hlx"}

        async def fake_fetch_lifecycle(artifact_ref, artifact_type=None):
            assert artifact_ref == "hlx-ref-001"
            assert artifact_type == "hlx"
            return payload

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "hlx-ref-001", "artifact_type": "hlx"},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == payload
        assert resp.headers["x-artifact-ref"] == "hlx-ref-001"
        assert resp.headers["etag"].startswith("sha256:")

    @pytest.mark.asyncio
    async def test_manifest_inferred_as_json_without_artifact_type(self, app_and_client):
        """No artifact_type → ref prefix ``manifest-`` infers a lifecycle JSON."""
        app, client = app_and_client
        payload = {"artifact_ref": "manifest-doc-9", "artifacts": {"signed_pdf_ref": "x"}}

        async def fake_fetch_lifecycle(artifact_ref, artifact_type=None):
            assert artifact_ref == "manifest-doc-9"
            return payload

        # If the route mistakenly took the HARD path it would hit fetch_blob;
        # make that explode so the test fails loudly on misrouting.
        async def boom_fetch_blob(artifact_ref, jwt_token=None):
            raise AssertionError("manifest must route to lifecycle JSON, not bytes")

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle
        app.state.heartbeat.fetch_blob = boom_fetch_blob

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "manifest-doc-9"},
        )

        assert resp.status_code == 200
        assert resp.json() == payload


# ── Miss → 404 contract body ──────────────────────────────────────────────


class TestArtifactNotFound:
    @pytest.mark.asyncio
    async def test_hard_miss_returns_404_contract_body(self, app_and_client):
        app, client = app_and_client

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            return None  # HB miss

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "blob-missing", "artifact_type": "signed_pdf"},
        )

        assert resp.status_code == 404
        # EXACT contract body — no status/message/details envelope.
        assert resp.json() == {"code": "ARTIFACT_NOT_FOUND", "artifact_ref": "blob-missing"}

    @pytest.mark.asyncio
    async def test_lifecycle_miss_returns_404_contract_body(self, app_and_client):
        app, client = app_and_client

        async def fake_fetch_lifecycle(artifact_ref, artifact_type=None):
            return None  # Core miss

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "hlx-missing", "artifact_type": "hlx"},
        )

        assert resp.status_code == 404
        assert resp.json() == {"code": "ARTIFACT_NOT_FOUND", "artifact_ref": "hlx-missing"}


# ── VERB: POST-only, body-not-path ────────────────────────────────────────


class TestVerbAndBody:
    @pytest.mark.asyncio
    async def test_no_get_variant_with_ref_in_path(self, app_and_client):
        """There is NO ``GET /api/artifacts/<ref>`` — the ref must not be in a URL."""
        app, client = app_and_client
        resp = await client.get("/api/artifacts/blob-signed-001")
        assert resp.status_code in (404, 405)  # route simply does not exist

    @pytest.mark.asyncio
    async def test_artifact_ref_read_from_body_not_path(self, app_and_client):
        """The body's artifact_ref is the one fetched (proves body-sourcing)."""
        app, client = app_and_client
        seen = {}

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            seen["ref"] = artifact_ref
            return {"content_type": "application/pdf", "data": b"%PDF-1.4\nx\n%%EOF"}

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_ref": "from-body-ref", "artifact_type": "original_pdf"},
        )
        assert resp.status_code == 200
        assert seen["ref"] == "from-body-ref"

    @pytest.mark.asyncio
    async def test_missing_artifact_ref_field_is_422(self, app_and_client):
        """artifact_ref is required by the pydantic model."""
        app, client = app_and_client
        resp = await client.post(
            "/api/artifacts/fetch",
            json={"artifact_type": "signed_pdf"},
        )
        assert resp.status_code == 422


# ── Auth required ─────────────────────────────────────────────────────────


class TestAuthRequired:
    @pytest.mark.asyncio
    async def test_no_credentials_rejected(self, test_config):
        """Without the dependency override, a credential-less call is rejected
        by the shared ``authenticate_request`` dispatcher (401)."""
        app = create_app(config=test_config, api_key_secrets={"k": "s"})
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/artifacts/fetch",
                    json={"artifact_ref": "blob-1", "artifact_type": "signed_pdf"},
                )
        # No HMAC / Bearer headers → AuthenticationFailedError (401).
        assert resp.status_code == 401


# ── classify_artifact unit coverage ───────────────────────────────────────


class TestClassifyArtifact:
    @pytest.mark.parametrize(
        "artifact_type,expected_ct",
        [
            ("signed_pdf", "application/pdf"),
            ("fixed_pdf", "application/pdf"),
            ("original_pdf", "application/pdf"),
            ("backend_pdf", "application/pdf"),
            ("qr_invoice", "application/vnd.helium.invoice-qr+json"),
            ("qr_blob", "application/vnd.helium.invoice-qr+json"),
            ("signature", "application/octet-stream"),
        ],
    )
    def test_hard_kinds(self, artifact_type, expected_ct):
        kind_class, content_type = classify_artifact(
            artifact_type=artifact_type, artifact_ref="ref-x"
        )
        assert kind_class == "hard"
        assert content_type == expected_ct

    @pytest.mark.parametrize(
        "artifact_type",
        ["hlx", "firs_returned_artifact", "approval_lifecycle_json", "manifest"],
    )
    def test_lifecycle_kinds(self, artifact_type):
        kind_class, content_type = classify_artifact(
            artifact_type=artifact_type, artifact_ref="ref-x"
        )
        assert kind_class == "lifecycle"
        assert content_type is None

    def test_manifest_prefix_inference_without_type(self):
        kind_class, content_type = classify_artifact(
            artifact_type=None, artifact_ref="manifest-doc-1"
        )
        assert kind_class == "lifecycle"

    def test_unknown_type_falls_back_to_hard_pdf(self):
        kind_class, content_type = classify_artifact(
            artifact_type="totally_unknown", artifact_ref="ref-x"
        )
        assert kind_class == "hard"
        assert content_type == "application/pdf"

    def test_case_insensitive_type(self):
        kind_class, _ = classify_artifact(
            artifact_type="SIGNED_PDF", artifact_ref="ref-x"
        )
        assert kind_class == "hard"
