"""
Tests for POST /api/artifacts/fetch — R-M4 / §B-RelayArtifactFetch.

Coverage (per the chip brief):
    - the route is **POST** with a JSON body; there is NO GET variant and
      ``artifact_ref`` is read from the BODY, not the path (VERB_DELTA);
    - HARD artifact kinds return raw **bytes** with the per-kind Content-Type;
    - LIFECYCLE artifact kinds return raw **JSON** (sourced from Core);
    - a miss returns 404 with the EXACT body
      ``{"code": "ARTIFACT_NOT_FOUND", "artifact_ref": <ref>}``;
    - auth is required (the shared dispatcher rejects a credential-less call);
    - response carries ``X-Relay-Artifact-*`` headers and NO ``X-SBS-*`` headers
      (CANON — the SBS-branded headers must not ship).

Auth model mirrors test_finalize_route.py: HMAC over the EXACT JSON body bytes
(the fetch call has no multipart, so the signature is computable). The route
resolves ``authenticate_request`` INSIDE the handler (not via Depends), so
``app.dependency_overrides`` would NOT intercept it — real HMAC is used instead.

The HB/Core client methods are mocked on the live ``app.state`` instances after
lifespan startup, so these tests prove the ROUTE is correct without a real HB
blob store or Core lifecycle store (NEEDS-HB / NEEDS-CORE).
"""

import json
from datetime import datetime, timezone

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.api.routes.artifacts import classify_artifact
from src.config import RelayConfig
from src.core.auth import compute_signature


TEST_API_KEY = "test-key-001"
TEST_SECRET = "secret-001"


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


@pytest.fixture
def test_secrets():
    return {TEST_API_KEY: TEST_SECRET}


@pytest.fixture
async def app_and_client(test_config, test_secrets):
    """App + ASGI client over real lifespan startup. Client methods are mocked
    per-test on the live ``app.state.heartbeat`` / ``app.state.core``."""
    app = create_app(config=test_config, api_key_secrets=test_secrets)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield app, c


def _hmac_headers_for_body(body: bytes) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sig = compute_signature(TEST_API_KEY, ts, body, TEST_SECRET)
    return {
        "X-API-Key": TEST_API_KEY,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _post_args(payload: dict) -> tuple[bytes, dict]:
    """Serialise the JSON body + build matching HMAC headers.

    The EXACT bytes are passed as ``content=`` so the signed body equals the
    wire body (httpx's ``json=`` would re-serialise and could differ).
    """
    body = json.dumps(payload).encode("utf-8")
    return body, _hmac_headers_for_body(body)


def _assert_no_sbs_headers(resp) -> None:
    """CANON: no simulator-branded ``X-SBS-*`` header may appear."""
    for name in resp.headers.keys():
        assert not name.lower().startswith("x-sbs-"), f"leaked SBS header: {name}"


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

        body, headers = _post_args(
            {"artifact_ref": "blob-signed-001", "artifact_type": "signed_pdf"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == pdf_bytes
        # X-Relay-Artifact-* headers present; NO X-SBS-*.
        assert resp.headers["x-relay-artifact"] == "true"
        assert resp.headers["x-relay-artifact-ref"] == "blob-signed-001"
        assert resp.headers["x-relay-artifact-kind"] == "hard"
        assert resp.headers["x-relay-artifact-etag"].startswith("sha256:")
        _assert_no_sbs_headers(resp)

    @pytest.mark.asyncio
    async def test_qr_invoice_content_type_and_durable_marker(self, app_and_client):
        app, client = app_and_client
        qr_bytes = b'{"irn": "ABC-123", "qr": true}'

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            # HB may report octet-stream; the route serves the per-KIND type.
            return {"content_type": "application/octet-stream", "data": qr_bytes}

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        body, headers = _post_args(
            {"artifact_ref": "blob-qr-001", "artifact_type": "qr_invoice"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/vnd.helium.invoice-qr+json"
        assert resp.content == qr_bytes
        # QR blobs carry the de-branded durable-invoice-data marker.
        assert resp.headers["x-relay-durable-invoice-data"] == "qr_bytes"
        _assert_no_sbs_headers(resp)

    @pytest.mark.asyncio
    async def test_backend_copy_scout_kind_routes_to_pdf_bytes(self, app_and_client):
        """``backend_copy`` is the Scout adapter's name for a backend PDF copy
        (scout.py:7981/8193) — must route to HARD/PDF bytes, not JSON."""
        app, client = app_and_client

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            return {"content_type": "application/pdf", "data": b"%PDF-1.4\nx\n%%EOF"}

        async def boom_lifecycle(artifact_ref, artifact_type=None):
            raise AssertionError("backend_copy must route to bytes, not lifecycle")

        app.state.heartbeat.fetch_blob = fake_fetch_blob
        app.state.core.fetch_lifecycle_artifact = boom_lifecycle

        body, headers = _post_args(
            {"artifact_ref": "blob-backend-1", "artifact_type": "backend_copy"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"


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

        # If the route mistakenly took the HARD path it would hit fetch_blob;
        # make that explode so the test fails loudly on misrouting.
        async def boom_fetch_blob(artifact_ref, jwt_token=None):
            raise AssertionError("hlx must route to lifecycle JSON, not bytes")

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle
        app.state.heartbeat.fetch_blob = boom_fetch_blob

        body, headers = _post_args(
            {"artifact_ref": "hlx-ref-001", "artifact_type": "hlx"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == payload
        assert resp.headers["x-relay-artifact-kind"] == "lifecycle"
        assert resp.headers["x-relay-artifact-ref"] == "hlx-ref-001"
        assert resp.headers["x-relay-artifact-etag"].startswith("sha256:")
        _assert_no_sbs_headers(resp)

    @pytest.mark.asyncio
    async def test_manifest_inferred_as_json_without_artifact_type(self, app_and_client):
        """No artifact_type → ref prefix ``manifest-`` infers a lifecycle JSON
        (SBS parity, relay.py:1035)."""
        app, client = app_and_client
        payload = {"artifact_ref": "manifest-doc-9", "artifacts": {"signed_pdf_ref": "x"}}

        async def fake_fetch_lifecycle(artifact_ref, artifact_type=None):
            assert artifact_ref == "manifest-doc-9"
            return payload

        async def boom_fetch_blob(artifact_ref, jwt_token=None):
            raise AssertionError("manifest must route to lifecycle JSON, not bytes")

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle
        app.state.heartbeat.fetch_blob = boom_fetch_blob

        body, headers = _post_args({"artifact_ref": "manifest-doc-9"})
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.json() == payload
        assert resp.headers["x-relay-artifact-kind"] == "lifecycle"


# ── Miss → 404 contract body ──────────────────────────────────────────────


class TestArtifactNotFound:
    @pytest.mark.asyncio
    async def test_hard_miss_returns_404_contract_body(self, app_and_client):
        app, client = app_and_client

        async def fake_fetch_blob(artifact_ref, jwt_token=None):
            return None  # HB miss

        app.state.heartbeat.fetch_blob = fake_fetch_blob

        body, headers = _post_args(
            {"artifact_ref": "blob-missing", "artifact_type": "signed_pdf"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 404
        # EXACT contract body — no status/error_code/message/details envelope.
        assert resp.json() == {"code": "ARTIFACT_NOT_FOUND", "artifact_ref": "blob-missing"}
        _assert_no_sbs_headers(resp)

    @pytest.mark.asyncio
    async def test_lifecycle_miss_returns_404_contract_body(self, app_and_client):
        app, client = app_and_client

        async def fake_fetch_lifecycle(artifact_ref, artifact_type=None):
            return None  # Core miss

        app.state.core.fetch_lifecycle_artifact = fake_fetch_lifecycle

        body, headers = _post_args(
            {"artifact_ref": "hlx-missing", "artifact_type": "hlx"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 404
        assert resp.json() == {"code": "ARTIFACT_NOT_FOUND", "artifact_ref": "hlx-missing"}

    @pytest.mark.asyncio
    async def test_missing_artifact_ref_returns_404_empty_ref(self, app_and_client):
        """An absent artifact_ref can never resolve → 404 with empty-ref body.
        (No path source exists — VERB_DELTA forbids a path ref.)"""
        app, client = app_and_client

        async def boom_fetch_blob(artifact_ref, jwt_token=None):
            raise AssertionError("must not reach HB for an empty ref")

        app.state.heartbeat.fetch_blob = boom_fetch_blob

        body, headers = _post_args({"artifact_type": "signed_pdf"})
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)

        assert resp.status_code == 404
        assert resp.json() == {"code": "ARTIFACT_NOT_FOUND", "artifact_ref": ""}


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

        body, headers = _post_args(
            {"artifact_ref": "from-body-ref", "artifact_type": "original_pdf"}
        )
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)
        assert resp.status_code == 200, resp.text
        assert seen["ref"] == "from-body-ref"


# ── Auth required ─────────────────────────────────────────────────────────


class TestAuthRequired:
    @pytest.mark.asyncio
    async def test_no_credentials_rejected(self, app_and_client):
        """Without HMAC/Bearer headers, the shared dispatcher rejects (401)."""
        _, client = app_and_client
        body = json.dumps(
            {"artifact_ref": "blob-1", "artifact_type": "signed_pdf"}
        ).encode("utf-8")
        resp = await client.post(
            "/api/artifacts/fetch",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTHENTICATION_FAILED"

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self, app_and_client):
        _, client = app_and_client
        body = json.dumps(
            {"artifact_ref": "blob-1", "artifact_type": "signed_pdf"}
        ).encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        headers = {
            "X-API-Key": TEST_API_KEY,
            "X-Timestamp": ts,
            "X-Signature": "deadbeef",  # wrong
            "Content-Type": "application/json",
        }
        resp = await client.post("/api/artifacts/fetch", content=body, headers=headers)
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
            ("backend_copy", "application/pdf"),
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
        [
            "hlx",
            "firs_returned_artifact",
            "approval_lifecycle_json",
            "approval_lifecycle",
            "manifest",
        ],
    )
    def test_lifecycle_kinds(self, artifact_type):
        kind_class, content_type = classify_artifact(
            artifact_type=artifact_type, artifact_ref="ref-x"
        )
        assert kind_class == "lifecycle"
        assert content_type is None

    def test_manifest_prefix_inference_without_type(self):
        kind_class, _ = classify_artifact(
            artifact_type=None, artifact_ref="manifest-doc-1"
        )
        assert kind_class == "lifecycle"

    def test_unknown_type_falls_back_to_hard_pdf(self):
        kind_class, content_type = classify_artifact(
            artifact_type="totally_unknown", artifact_ref="ref-x"
        )
        assert kind_class == "hard"
        assert content_type == "application/pdf"

    def test_unknown_type_with_manifest_prefix_is_lifecycle(self):
        """Unknown explicit kind still honours the manifest- prefix fallback."""
        kind_class, _ = classify_artifact(
            artifact_type="totally_unknown", artifact_ref="manifest-doc-1"
        )
        assert kind_class == "lifecycle"

    def test_case_insensitive_type(self):
        kind_class, _ = classify_artifact(
            artifact_type="SIGNED_PDF", artifact_ref="ref-x"
        )
        assert kind_class == "hard"
