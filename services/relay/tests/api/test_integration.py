"""
Integration tests — end-to-end flows through the API layer.

These tests compute valid HMAC signatures so requests pass auth
and reach the actual service handlers (bulk/external flows).
"""

import io
import pytest
from datetime import datetime, timezone
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig
from src.core.auth import compute_signature


# ── Fixtures ──────────────────────────────────────────────────────────────


TEST_API_KEY = "integration-key-001"
TEST_SECRET = "integration-secret-001"
INTERNAL_TOKEN = "integration-internal-token"


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-integration-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
        internal_service_token=INTERNAL_TOKEN,
    )


@pytest.fixture
def test_secrets():
    return {TEST_API_KEY: TEST_SECRET}


@pytest.fixture
async def client(test_config, test_secrets):
    app = create_app(config=test_config, api_key_secrets=test_secrets)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _build_multipart_and_sign(
    files: list[tuple[str, bytes, str]],
    form_fields: dict | None = None,
) -> tuple[dict, dict, dict]:
    """
    Build multipart form data and compute a valid HMAC signature.

    For multipart requests, the raw body that gets signed is the
    multipart-encoded payload. We use httpx to encode it, extract
    the raw bytes, compute HMAC, then return headers.

    Returns: (files_dict, data_dict, auth_headers)
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build the multipart payload using httpx to get the exact bytes
    import httpx

    files_param = [("files", (name, data, ctype)) for name, data, ctype in files]
    data_param = form_fields or {}

    # Create a request to extract multipart body bytes
    req = httpx.Request(
        "POST",
        "http://test/api/ingest",
        files=files_param,
        data=data_param,
    )
    raw_body = b"".join(req.stream)
    content_type = req.headers["content-type"]

    signature = compute_signature(TEST_API_KEY, timestamp, raw_body, TEST_SECRET)

    headers = {
        "X-API-Key": TEST_API_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": content_type,
    }

    return raw_body, headers


# ── Bulk Flow Integration ────────────────────────────────────────────────


class TestBulkFlowIntegration:
    """Full bulk flow through the API — auth + ingest + preview."""

    @pytest.mark.asyncio
    async def test_bulk_flow_returns_queued(self, client):
        """Bulk upload with valid auth → status=queued (Core is stub)."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.pdf", b"%PDF-1.4 test content", "application/pdf")],
            form_fields={"call_type": "bulk"},
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("processed", "queued")
        assert data["data_uuid"] is not None
        assert data["queue_id"] is not None
        assert data["filenames"] == ["invoice.pdf"]
        assert data["file_count"] == 1
        assert data["file_hash"] is not None

    @pytest.mark.asyncio
    async def test_bulk_flow_default_call_type(self, client):
        """No call_type defaults to bulk."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("test.csv", b"col1,col2\na,b", "text/csv")],
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("processed", "queued")

    @pytest.mark.asyncio
    async def test_bulk_flow_multi_file(self, client):
        """Multiple files uploaded together."""
        raw_body, headers = _build_multipart_and_sign(
            files=[
                ("invoice1.pdf", b"%PDF-1.4 first", "application/pdf"),
                ("invoice2.pdf", b"%PDF-1.4 second", "application/pdf"),
            ],
            form_fields={"call_type": "bulk"},
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["file_count"] == 2
        assert data["filenames"] == ["invoice1.pdf", "invoice2.pdf"]


# ── External Flow Integration ────────────────────────────────────────────


class TestExternalFlowIntegration:
    """Full external flow through the API — auth + ingest + IRN/QR."""

    @pytest.mark.asyncio
    async def test_external_flow_returns_processed(self, client):
        """External upload with valid auth → status=processed + IRN + QR."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.xml", b"<invoice>test</invoice>", "application/xml")],
            form_fields={"call_type": "external"},
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["irn"] is not None
        assert data["qr_code"] is not None
        assert data["data_uuid"] is not None

    @pytest.mark.asyncio
    async def test_external_flow_with_invoice_data(self, client):
        """External flow with invoice_data_json."""
        import json

        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.json", b'{"total": 100}', "application/json")],
            form_fields={
                "call_type": "external",
                "invoice_data_json": json.dumps({"total": 100, "currency": "NGN"}),
            },
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"
        assert data["irn"] is not None

    @pytest.mark.asyncio
    async def test_external_flow_bad_invoice_json(self, client):
        """External flow with invalid JSON → ignores it gracefully."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.pdf", b"%PDF-1.4 data", "application/pdf")],
            form_fields={
                "call_type": "external",
                "invoice_data_json": "not-valid-json{{{",
            },
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processed"


# ── Auth Failure Integration ─────────────────────────────────────────────


class TestAuthFailureIntegration:
    """Authentication failures at the route level."""

    @pytest.mark.asyncio
    async def test_wrong_signature_returns_401(self, client):
        """Valid headers but wrong signature → 401."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        import httpx

        req = httpx.Request(
            "POST",
            "http://test/api/ingest",
            files=[("files", ("invoice.pdf", b"data", "application/pdf"))],
            data={"call_type": "bulk"},
        )
        raw_body = b"".join(req.stream)
        content_type = req.headers["content-type"]

        headers = {
            "X-API-Key": TEST_API_KEY,
            "X-Timestamp": timestamp,
            "X-Signature": "wrong-signature-value",
            "Content-Type": content_type,
        }

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_api_key_returns_401(self, client):
        """Unknown API key → 401."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        signature = compute_signature("unknown-key", timestamp, b"", "fake-secret")

        import httpx

        req = httpx.Request(
            "POST",
            "http://test/api/ingest",
            files=[("files", ("invoice.pdf", b"data", "application/pdf"))],
        )
        raw_body = b"".join(req.stream)

        headers = {
            "X-API-Key": "unknown-key",
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": req.headers["content-type"],
        }

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 401


# ── Trace ID Integration ─────────────────────────────────────────────────


class TestTraceIDIntegration:
    """Verify trace ID flows through the entire stack."""

    @pytest.mark.asyncio
    async def test_trace_id_generated_on_authenticated_request(self, client):
        """Authenticated request gets X-Trace-ID in response."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.pdf", b"%PDF-1.4 test", "application/pdf")],
        )

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        assert "x-trace-id" in response.headers
        # Auto-generated UUID format
        trace_id = response.headers["x-trace-id"]
        assert len(trace_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_custom_trace_id_preserved_through_flow(self, client):
        """Client-provided X-Trace-ID preserved through auth + processing."""
        raw_body, headers = _build_multipart_and_sign(
            files=[("invoice.pdf", b"%PDF-1.4 test", "application/pdf")],
        )
        headers["X-Trace-ID"] = "custom-trace-abc-123"

        response = await client.post(
            "/api/ingest",
            content=raw_body,
            headers=headers,
        )

        assert response.status_code == 200
        assert response.headers["x-trace-id"] == "custom-trace-abc-123"
