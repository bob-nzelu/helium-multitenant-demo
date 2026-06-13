"""
Tests for BulkService (Float flow) — Q4 async-preview contract.

The bulk path no longer blocks on / returns Core's preview inline. It now:
    - returns BulkResult(status="queued") promptly (accepted), with NO
      preview_data on the result shape;
    - emits a ``core.preview.available`` lifecycle event (via the swappable
      publisher seam) once Core's preview arrives, carrying the client
      ``trace_id`` + the batch/blob identity + the preview_data payload;
    - emits NOTHING when the preview times out or Core is unreachable (the
      bytes are already committed; Scout reconciles).
"""

import asyncio
import pytest

from src.services.bulk import BulkService, BulkResult
from src.services.ingestion import IngestionService
from src.services.lifecycle import (
    FAMILY_PREVIEW_AVAILABLE,
    RecordingLifecyclePublisher,
)
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
def publisher():
    return RecordingLifecyclePublisher()


@pytest.fixture
def bulk_service(ingestion, core, publisher):
    return BulkService(ingestion, core, publisher, preview_timeout=300.0)


@pytest.fixture
def single_pdf():
    return [("invoice.pdf", b"%PDF-1.4 test invoice")]


async def _drain_preview_tasks(svc: BulkService) -> None:
    """Let the background preview task(s) run to completion + emit."""
    pending = list(svc._preview_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── Prompt return (no block, no inline preview) ──────────────────────────


class TestBulkPromptReturn:
    """Bulk returns promptly as 'queued' — no inline preview_data."""

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

    @pytest.mark.asyncio
    async def test_does_not_block_on_slow_preview(self, config, heartbeat, publisher):
        """A 5s Core preview must NOT delay the bulk response."""

        class SlowCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                await asyncio.sleep(5)  # would block if awaited inline
                return {"queue_id": queue_id, "status": "processed", "preview_data": {}}

        slow_core = SlowCore()
        ingestion = IngestionService(config, heartbeat, slow_core)
        svc = BulkService(ingestion, slow_core, publisher, preview_timeout=5.0)

        result = await asyncio.wait_for(
            svc.process([("t.pdf", b"data")], api_key="k", trace_id="t"),
            timeout=1.0,  # response must come back well under the 5s preview
        )
        assert result.status == "queued"
        # Cancel the lingering background task so the loop closes cleanly.
        for task in list(svc._preview_tasks):
            task.cancel()


# ── core.preview.available emission ──────────────────────────────────────


class TestPreviewEvent:
    """When the preview arrives, a core.preview.available event is published."""

    @pytest.mark.asyncio
    async def test_event_emitted_after_preview(self, bulk_service, publisher, single_pdf):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-200")
        await _drain_preview_tasks(bulk_service)

        assert len(publisher.events) == 1
        evt = publisher.events[0]
        assert evt.family == FAMILY_PREVIEW_AVAILABLE  # "core.preview.available"
        assert evt.trace_id == "t-200"

    @pytest.mark.asyncio
    async def test_event_payload_carries_identity_and_preview(
        self, bulk_service, publisher, single_pdf
    ):
        result = await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-201")
        await _drain_preview_tasks(bulk_service)

        evt = publisher.events[0]
        data = evt.data
        # Batch/blob identity so Scout matches the preview to its row.
        assert data["data_uuid"] == result.ingest.data_uuid
        assert data["queue_id"] == result.ingest.queue_id
        assert data["filenames"] == ["invoice.pdf"]
        assert data["file_count"] == 1
        # The preview_data payload Core produced (was the inline field).
        assert data["preview_data"] is not None
        assert "invoice_count" in data["preview_data"]

    @pytest.mark.asyncio
    async def test_frame_lifts_trace_id_top_level(self, bulk_service, publisher, single_pdf):
        """The published frame echoes trace_id at top level for Scout's reducer."""
        await bulk_service.process(single_pdf, api_key="test-key", trace_id="t-202")
        await _drain_preview_tasks(bulk_service)

        frame = publisher.frames[0]
        assert frame["trace_id"] == "t-202"
        assert frame["family"] == FAMILY_PREVIEW_AVAILABLE
        assert frame["data"]["raw_bytes_in_event"] is False


# ── No event on timeout / Core down (best-effort) ────────────────────────


class TestNoEventOnFailure:
    """Timeout or Core error emits nothing; ingest still succeeds."""

    @pytest.mark.asyncio
    async def test_timeout_emits_no_event(self, config, heartbeat, publisher):
        class SlowCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                await asyncio.sleep(5)
                return {"queue_id": queue_id, "status": "processed", "preview_data": {}}

        slow_core = SlowCore()
        ingestion = IngestionService(config, heartbeat, slow_core)
        svc = BulkService(ingestion, slow_core, publisher, preview_timeout=0.05)

        result = await svc.process([("test.pdf", b"data")], api_key="k", trace_id="t")
        await _drain_preview_tasks(svc)

        assert result.status == "queued"
        assert publisher.events == []  # nothing emitted on timeout

    @pytest.mark.asyncio
    async def test_core_down_emits_no_event(self, config, heartbeat, publisher):
        class FailCore(CoreClient):
            async def process_preview(self, queue_id, timeout=None):
                raise ConnectionError("Core is unreachable")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = BulkService(ingestion, fail_core, publisher, preview_timeout=300.0)

        result = await svc.process([("test.pdf", b"data")], api_key="k", trace_id="t")
        await _drain_preview_tasks(svc)

        assert result.status == "queued"
        assert publisher.events == []

    @pytest.mark.asyncio
    async def test_core_down_ingest_still_works(self, config, heartbeat, publisher):
        """Even if Core is down, blob is written and ingestion succeeds."""

        class FailCore(CoreClient):
            async def enqueue(self, blob_uuid, filename, file_size_bytes, batch_id):
                raise ConnectionError("Core is down")

            async def process_preview(self, queue_id, timeout=None):
                raise ConnectionError("Core is down")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = BulkService(ingestion, fail_core, publisher, preview_timeout=300.0)

        result = await svc.process([("test.pdf", b"data")], api_key="k", trace_id="t")
        await _drain_preview_tasks(svc)

        assert result.status == "queued"
        assert result.ingest.queue_id.startswith("orphan_")
        assert publisher.events == []


# ── Back-compat: publisher optional ──────────────────────────────────────


class TestPublisherOptional:
    """BulkService without a publisher still ingests (no event emitted)."""

    @pytest.mark.asyncio
    async def test_no_publisher_no_crash(self, ingestion, core, single_pdf):
        svc = BulkService(ingestion, core)  # no publisher
        result = await svc.process(single_pdf, api_key="k", trace_id="t-300")
        await _drain_preview_tasks(svc)
        assert result.status == "queued"
