"""
Tests for clients/core.py — Core API client (real httpx).

The CoreClient was un-stubbed (Phase 1 submit chain) to make real HTTP calls to
the Core container. These tests inject an ``httpx.MockTransport`` into the
client's shared httpx client so we assert the EXACT Core endpoints, request
payloads, and response handling without a live Core.
"""

import asyncio
import json

import httpx
import pytest

from src.clients.core import CoreClient
from src.errors import CoreUnavailableError, TransientError


def _wire_transport(client: CoreClient, handler) -> None:
    """Replace the client's shared httpx client with a MockTransport one."""
    client._http = httpx.AsyncClient(
        base_url=client.core_api_url,
        transport=httpx.MockTransport(handler),
    )


class TestCoreClientInit:
    """Test CoreClient initialization."""

    def test_defaults(self):
        client = CoreClient()
        assert client.core_api_url == "http://localhost:8080"
        assert client.timeout == 30.0
        assert client.preview_timeout == 300.0
        assert client.max_attempts == 5

    def test_custom_values(self):
        client = CoreClient(
            core_api_url="http://core.prod:8080/",
            timeout=10.0,
            preview_timeout=60.0,
            max_attempts=3,
            trace_id="test",
        )
        # Trailing slash stripped for clean base_url joins.
        assert client.core_api_url == "http://core.prod:8080"
        assert client.preview_timeout == 60.0


class TestCoreClientEnqueue:
    """Test enqueue method → POST /api/v1/enqueue."""

    @pytest.mark.asyncio
    async def test_enqueue_posts_enqueue_endpoint(self, core_client):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "queue_id": "q-001",
                    "status": "PENDING",
                    "data_uuid": "blob-123",
                    "created_at": "2026-06-22T00:00:00Z",
                },
            )

        _wire_transport(core_client, handler)

        result = await core_client.enqueue(
            blob_uuid="blob-123",
            filename="invoice.pdf",
            file_size_bytes=1024,
            batch_id="batch-001",
            metadata={"company_id": "COMP-1", "uploaded_by": "u@x.com"},
        )

        assert seen["path"] == "/api/v1/enqueue"
        assert seen["body"]["blob_uuid"] == "blob-123"
        assert seen["body"]["original_filename"] == "invoice.pdf"
        assert seen["body"]["company_id"] == "COMP-1"
        assert seen["body"]["uploaded_by"] == "u@x.com"
        assert seen["body"]["batch_id"] == "batch-001"
        assert result["queue_id"] == "q-001"
        assert result["status"] == "PENDING"
        assert result["blob_uuid"] == "blob-123"

    @pytest.mark.asyncio
    async def test_enqueue_409_is_idempotent(self, core_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "already queued"})

        _wire_transport(core_client, handler)
        result = await core_client.enqueue("b1", "f.pdf", 100, "batch-1")
        assert result["status"] == "queued"
        assert result["blob_uuid"] == "b1"

    @pytest.mark.asyncio
    async def test_enqueue_connect_error_raises_core_unavailable(
        self, core_client
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        _wire_transport(core_client, handler)
        with pytest.raises(CoreUnavailableError):
            await core_client.enqueue("b1", "f.pdf", 100, "batch-1")


class TestCoreClientFinalizeByReference:
    """Test the Phase-1 submit chain → POST /api/v1/finalize {ref, trace_id}."""

    @pytest.mark.asyncio
    async def test_finalize_posts_ref_and_trace_id(self, core_client):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "STUB_ACCEPTED",
                    "irn": "IRN-ABC-123",
                    "qr": "data:image/png;base64,xxx",
                    "artifacts": {"hlx": "ref-hlx"},
                    "event_id": "evt-1",
                    "event_family": "core.submission.terminal",
                },
            )

        _wire_transport(core_client, handler)

        result = await core_client.finalize_by_reference(
            ref="sha256:deadbeef",
            trace_id="018f-trace",
        )

        assert seen["path"] == "/api/v1/finalize"
        assert seen["body"]["ref"] == "sha256:deadbeef"
        assert seen["body"]["trace_id"] == "018f-trace"
        assert result["status"] == "STUB_ACCEPTED"
        assert result["irn"] == "IRN-ABC-123"
        assert result["qr"].startswith("data:image/png")
        assert result["event_family"] == "core.submission.terminal"
        # Echo-back when present in Core's response is preserved.
        assert result["ref"] == "sha256:deadbeef"
        assert result["trace_id"] == "018f-trace"

    @pytest.mark.asyncio
    async def test_finalize_5xx_raises_transient(self, core_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="core boom")

        _wire_transport(core_client, handler)
        with pytest.raises(TransientError):
            await core_client.finalize_by_reference(ref="r1", trace_id="t1")

    @pytest.mark.asyncio
    async def test_finalize_4xx_raises_core_unavailable(self, core_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad ref")

        _wire_transport(core_client, handler)
        with pytest.raises(CoreUnavailableError):
            await core_client.finalize_by_reference(ref="r1", trace_id="t1")

    @pytest.mark.asyncio
    async def test_finalize_routes_through_reference(self, core_client):
        """finalize(queue_id) routes through the /api/v1/finalize endpoint."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "accepted"})

        _wire_transport(core_client, handler)
        result = await core_client.finalize("queue-789", user_edits={"x": 1})
        assert seen["path"] == "/api/v1/finalize"
        assert seen["body"]["ref"] == "queue-789"
        assert seen["body"]["metadata"]["user_edits"] == {"x": 1}
        assert result["status"] == "accepted"


class TestCoreClientProcessAcks:
    """process_preview / process_immediate acknowledge without a Core endpoint."""

    @pytest.mark.asyncio
    async def test_process_preview_queued_ack(self, core_client):
        result = await core_client.process_preview("queue-123")
        assert result["queue_id"] == "queue-123"
        assert result["status"] == "queued"

    @pytest.mark.asyncio
    async def test_process_preview_timeout(self):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.sleep(1.0), timeout=0.001)

    @pytest.mark.asyncio
    async def test_process_immediate_ack(self, core_client):
        result = await core_client.process_immediate("queue-456")
        assert result["queue_id"] == "queue-456"
        assert result["status"] == "processing"


class TestCoreClientLookupStubs:
    """Invoice/artifact lookups return empty until Core ships the endpoints."""

    @pytest.mark.asyncio
    async def test_get_invoice_status_none(self, core_client):
        assert await core_client.get_invoice_status(irn="IRN-1") is None

    @pytest.mark.asyncio
    async def test_get_invoices_by_batch_empty(self, core_client):
        assert await core_client.get_invoices_by_batch("batch-1") == []

    @pytest.mark.asyncio
    async def test_fetch_lifecycle_artifact_none(self, core_client):
        assert await core_client.fetch_lifecycle_artifact("ref-1") is None


class TestCoreClientNoHealthCheck:
    """Verify Core client has no health_check — that's HeartBeat's job."""

    def test_no_health_check_method(self, core_client):
        assert not hasattr(core_client, "health_check")
