"""Tests for HeartBeat blob client (using respx mocks)."""

import pytest
import httpx
import respx

from src.errors import ExternalServiceError, NotFoundError, TimeoutError
from src.ingestion.heartbeat_client import HeartBeatBlobClient


@pytest.fixture
def client():
    return HeartBeatBlobClient(base_url="http://heartbeat:9000", api_key="test-key")


class TestFetchBlob:
    @respx.mock
    @pytest.mark.asyncio
    async def test_success(self, client):
        respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(
                200,
                content=b"file content",
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": 'attachment; filename="invoice.pdf"',
                    "x-blob-hash": "abc123",
                },
            )
        )
        resp = await client.fetch_blob("uuid-1")
        assert resp.content == b"file content"
        assert resp.content_type == "application/pdf"
        assert resp.filename == "invoice.pdf"
        assert resp.blob_hash == "abc123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_not_found(self, client):
        respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(NotFoundError):
            await client.fetch_blob("uuid-1")

    @respx.mock
    @pytest.mark.asyncio
    async def test_410_error_state(self, client):
        respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(410)
        )
        with pytest.raises(NotFoundError, match="error state"):
            await client.fetch_blob("uuid-1")

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_retries_then_fails(self, client):
        route = respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(ExternalServiceError):
            await client.fetch_blob("uuid-1")
        assert route.call_count == 3  # 3 retries

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_header_sent(self, client):
        respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(200, content=b"data", headers={"content-type": "text/plain"})
        )
        await client.fetch_blob("uuid-1")
        request = respx.calls[0].request
        assert request.headers["authorization"] == "Bearer test-key"

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_filename_fallback(self, client):
        respx.get("http://heartbeat:9000/api/blobs/uuid-1/download").mock(
            return_value=httpx.Response(200, content=b"data", headers={"content-type": "text/plain"})
        )
        resp = await client.fetch_blob("uuid-1")
        assert resp.filename == ""


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, client):
        await client.close()
