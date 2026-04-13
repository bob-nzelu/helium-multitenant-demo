"""
Tests for BulkService (Float flow)
"""

import asyncio
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
    return CoreClient()


@pytest.fixture
def ingestion(config, heartbeat, core):
    return IngestionService(config, heartbeat, core)


@pytest.fixture
def bulk_service(ingestion, core):
    return BulkService(ingestion, core, preview_timeout=300.0)


@pytest.fixture
def single_pdf():
    return [("invoice.pdf", b"%PDF-1.4 test invoice")]


# ── Happy Path ────────────────────────────────────────────────────────────


class TestBulkHappyPath:
    """Test successful bulk flow with preview."""

    @pytest.mark.asyncio
    async def test_process_returns_bulk_result(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-100")
        assert isinstance(result, BulkResult)

    @pytest.mark.asyncio
    async def test_preview_success_status(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-101")
        assert result.status == "processed"

    @pytest.mark.asyncio
    async def test_preview_data_present(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-102")
        assert result.preview_data is not None
        assert "invoice_count" in result.preview_data

    @pytest.mark.asyncio
    async def test_ingest_result_embedded(self, bulk_service, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-103")
        assert result.ingest.file_count == 1
        assert result.ingest.filenames == ["invoice.pdf"]


# ── Timeout ──────────────────────────────────────────────────────────────


class TestBulkTimeout:
    """Test bulk preview timeout → queued."""

    @pytest.mark.asyncio
    async def test_timeout_returns_queued(self, config, heartbeat):
        """When Core takes too long, result is 'queued'."""

        class SlowCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                await asyncio.sleep(5)  # Will be cancelled by timeout
                return {"queue_id": queue_id, "status": "processed", "preview_data": {}}

        slow_core = SlowCore()
        ingestion = IngestionService(config, heartbeat, slow_core)
        svc = BulkService(ingestion, slow_core, preview_timeout=0.05)

        result = await svc.process(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "queued"
        assert result.preview_data is None

    @pytest.mark.asyncio
    async def test_timeout_still_has_ingest_result(self, config, heartbeat):
        """Even on timeout, ingest result is populated."""

        class SlowCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                await asyncio.sleep(5)
                return {}

        slow_core = SlowCore()
        ingestion = IngestionService(config, heartbeat, slow_core)
        svc = BulkService(ingestion, slow_core, preview_timeout=0.05)

        result = await svc.process(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.ingest.status == "ingested"
        assert result.ingest.data_uuid is not None


# ── Core Unavailable ─────────────────────────────────────────────────────


class TestBulkCoreDown:
    """Test bulk flow when Core is unavailable."""

    @pytest.mark.asyncio
    async def test_core_down_returns_queued(self, config, heartbeat):
        """When Core raises, result is 'queued' (graceful degradation)."""

        class FailCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                raise ConnectionError("Core is unreachable")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = BulkService(ingestion, fail_core, preview_timeout=300.0)

        result = await svc.process(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "queued"
        assert result.preview_data is None

    @pytest.mark.asyncio
    async def test_core_down_ingest_still_works(self, config, heartbeat):
        """Even if Core is down, blob is written and ingestion succeeds."""

        class FailCore(CoreClient):
            async def enqueue(self, blob_uuid, filename, file_size_bytes, batch_id):
                raise ConnectionError("Core is down")

            async def process_preview(self, queue_id, timeout=None):
                raise ConnectionError("Core is down")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = BulkService(ingestion, fail_core, preview_timeout=300.0)

        result = await svc.process(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "queued"
        assert result.ingest.queue_id.startswith("orphan_")
