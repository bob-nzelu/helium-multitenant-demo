"""
Tests for HeartBeatBlobClient.update_blob_status()

Verifies:
- Correct payload construction
- Retry on 5xx
- Non-fatal on final failure (returns None)
- Success returns response dict
"""

from __future__ import annotations

import asyncio
import sys

import pytest
import respx
from httpx import Response

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.ingestion.heartbeat_client import HeartBeatBlobClient


BASE_URL = "http://heartbeat-test:9000"
BLOB_UUID = "blob-abc-123"
STATUS_URL = f"/api/v1/heartbeat/blob/{BLOB_UUID}/status"


@pytest.fixture
def client():
    return HeartBeatBlobClient(base_url=BASE_URL, api_key="test-key", timeout=5.0)


@pytest.mark.asyncio
class TestUpdateBlobStatus:

    @respx.mock
    async def test_success_returns_dict(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(200, json={"ok": True})
        )

        result = await client.update_blob_status(BLOB_UUID, "processing", "fetch")

        assert result == {"ok": True}
        request = respx.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"

    @respx.mock
    async def test_payload_includes_all_fields(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(200, json={})
        )

        await client.update_blob_status(
            BLOB_UUID,
            status="error",
            processing_stage="enrich",
            error_message="HIS timeout",
            processing_stats={
                "extracted_invoice_count": 10,
                "rejected_invoice_count": 2,
            },
        )

        import json
        body = json.loads(respx.calls.last.request.content)
        assert body["status"] == "error"
        assert body["processing_stage"] == "enrich"
        assert body["error_message"] == "HIS timeout"
        assert body["extracted_invoice_count"] == 10
        assert body["rejected_invoice_count"] == 2

    @respx.mock
    async def test_minimal_payload(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(200, json={})
        )

        await client.update_blob_status(BLOB_UUID, "processing")

        import json
        body = json.loads(respx.calls.last.request.content)
        assert body == {"status": "processing"}
        assert "processing_stage" not in body
        assert "error_message" not in body

    @respx.mock
    async def test_retries_on_5xx(self, client):
        route = respx.post(f"{BASE_URL}{STATUS_URL}")
        route.side_effect = [
            Response(503),
            Response(502),
            Response(200, json={"ok": True}),
        ]

        result = await client.update_blob_status(BLOB_UUID, "processing", "parse")

        assert result == {"ok": True}
        assert route.call_count == 3

    @respx.mock
    async def test_returns_none_on_exhausted_retries(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(503)
        )

        result = await client.update_blob_status(BLOB_UUID, "processing")

        assert result is None

    @respx.mock
    async def test_returns_none_on_non_retryable_error(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(400, json={"error": "bad request"})
        )

        result = await client.update_blob_status(BLOB_UUID, "bogus")

        assert result is None

    @respx.mock
    async def test_handles_204_no_content(self, client):
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            return_value=Response(204)
        )

        result = await client.update_blob_status(BLOB_UUID, "finalized")

        assert result == {}

    @respx.mock
    async def test_never_raises_on_timeout(self, client):
        import httpx
        respx.post(f"{BASE_URL}{STATUS_URL}").mock(
            side_effect=httpx.TimeoutException("connection timeout")
        )

        result = await client.update_blob_status(BLOB_UUID, "processing")

        assert result is None
