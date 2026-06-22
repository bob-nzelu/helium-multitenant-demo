"""
Tests for BulkService (Float flow) — Q4 async-preview + Q24 ingress-only.

The bulk path no longer blocks on / returns Core's preview inline (Q4), and it
no longer emits the preview event itself (Q24, ARCH tick56 — Relay is
ingress-only; Core emits ``core.preview.available`` on its own stream). Bulk now:
    - ingests the bytes (forwarding to Core via IngestionService → enqueue);
    - returns BulkResult(status="queued") promptly (accepted), with NO
      preview_data on the result shape;
    - never publishes a lifecycle event (that was an SBS-mock artifact).
"""

import pytest

from src.services.bulk import BulkService, BulkResult
from src.services.ingestion import IngestionService
from src.config import RelayConfig
from src.clients.core import CoreClient
from tests.stub_heartbeat import StubHeartBeatClient


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
    )


@pytest.fixture
def heartbeat():
    return StubHeartBeatClient()


@pytest.fixture
def core():
    # CoreClient is stubbed (no network); Redis is not used by BulkService.
    return CoreClient()


@pytest.fixture
def ingestion(config, heartbeat, core):
    return IngestionService(config, heartbeat, core)


@pytest.fixture
def bulk_service(ingestion, core):
    return BulkService(ingestion, core)


@pytest.fixture
def single_pdf():
    return [("invoice.pdf", b"%PDF-1.4 test invoice")]


# ── Prompt return (no block, no inline preview, no emit) ──────────────────


class TestBulkPromptReturn:
    """Bulk returns promptly as 'queued' — no inline preview_data, no emit."""

    @pytest.mark.asyncio
    async def test_process_returns_bulk_result(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-100")
        assert isinstance(result, BulkResult)

    @pytest.mark.asyncio
    async def test_status_is_queued(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-101")
        assert result.status == "queued"

    @pytest.mark.asyncio
    async def test_result_has_no_preview_data_attr(self, bulk_service, single_pdf):
        """The Q4 contract removed preview_data from the BulkResult shape."""
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-102")
        assert not hasattr(result, "preview_data")

    @pytest.mark.asyncio
    async def test_ingest_result_embedded(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-103")
        assert result.ingest.file_count == 1
        assert result.ingest.filenames == ["invoice.pdf"]


# ── Core down: ingest still forwards + returns (best-effort) ──────────────


class TestCoreDownResilience:
    """Even if Core is down, the blob is written and ingestion succeeds; bulk
    still returns queued (the preview is Core's concern, emitted on its stream)."""

    @pytest.mark.asyncio
    async def test_core_down_ingest_still_works(self, config, heartbeat):
        class FailCore(CoreClient):
            async def enqueue(self, blob_uuid, filename, file_size_bytes, batch_id):
                raise ConnectionError("Core is down")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = BulkService(ingestion, fail_core)

        result = await svc.process([("test.pdf", b"data")], api_key="k", trace_id="t")

        assert result.status == "queued"
        assert result.ingest.queue_id.startswith("orphan_")
