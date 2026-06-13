"""
Bulk Service (Float Flow)

Float desktop tool uploads files via Relay. **Bulk no longer blocks on Core's
preview** (Q4): the ``/api/ingest`` bulk call returns promptly once the bytes
are safely ingested (``status="queued"`` — accepted, Core still working). When
Core's preview becomes available, Relay emits a ``core.preview.available``
lifecycle event (carrying the client ``trace_id`` + the batch/blob identity) so
**Scout** reacts asynchronously off Core's SSE stream. This removes the up-to-
5-minute request-thread block the old inline ``preview_data`` path imposed.

Topology (Q15 two-stream / §B-EventLog): Relay hosts no SSE server. The
``core.preview.available`` frame is *published* through the lifecycle seam
(``LifecyclePublisher`` → ``CoreClient.publish_lifecycle_event``) onto Core's
stream — the same path ``relay.finalize.accepted`` already uses.

Flow:
    1. IngestionService.ingest() → IngestResult                    (awaited)
    2. Return BulkResult(status="queued") immediately.             (no block)
    3. Background: CoreClient.process_preview(queue_id)
       ├─ Success → emit core.preview.available {preview_data, trace_id, ...}
       ├─ Timeout → no event (Core still processing; Scout polls/reconciles)
       └─ Error   → no event (best-effort; bytes are already committed)
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ingestion import IngestionService, IngestResult
from .lifecycle import FAMILY_PREVIEW_AVAILABLE, LifecycleEvent, LifecyclePublisher

logger = logging.getLogger(__name__)


@dataclass
class BulkResult:
    """Result of bulk (Float) flow.

    ``status`` is always ``"queued"`` now — the bytes are accepted and Core
    processes the preview asynchronously. ``preview_data`` is intentionally
    gone from this shape (Q4): the preview rides the ``core.preview.available``
    lifecycle event, not the bulk HTTP response.
    """

    ingest: IngestResult
    status: str  # "queued" (accepted; preview arrives via core.preview.available)


class BulkService:
    """
    Bulk upload flow for Float desktop tool.

    Usage:
        service = BulkService(ingestion, core_client, lifecycle_publisher)
        result = await service.process(files, api_key, trace_id)
        # result.status == "queued"; preview arrives later as a lifecycle event.

    ``lifecycle_publisher`` is the swappable sink (§B-EventLog seam). It is
    optional for back-compat with call sites that construct BulkService without
    a publisher (e.g. older unit fixtures); when absent, the preview event is
    simply not emitted (ingestion still succeeds).
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any,
        lifecycle_publisher: Optional[LifecyclePublisher] = None,
        preview_timeout: float = 300.0,
    ):
        self._ingestion = ingestion_service
        self._core = core_client
        self._lifecycle = lifecycle_publisher
        self._preview_timeout = preview_timeout
        # Track in-flight preview tasks so they aren't garbage-collected before
        # they finish (asyncio keeps only weak refs to bare tasks).
        self._preview_tasks: "set[asyncio.Task]" = set()

    async def process(
        self,
        files: List[Tuple[str, bytes]],
        api_key: str,
        trace_id: str = "",
        metadata: Optional[Dict] = None,
        jwt_token: Optional[str] = None,
    ) -> BulkResult:
        """
        Run bulk flow: ingest → return promptly; preview is emitted async.

        Args:
            files: List of (filename, file_data) tuples.
            api_key: Authenticated API key.
            trace_id: Request trace ID (echoed on the preview lifecycle event).
            metadata: SDK identity/trace fields (forwarded through pipeline).
            jwt_token: Bearer JWT (forwarded to HeartBeat/Core).

        Returns:
            BulkResult(status="queued") — the bytes are accepted. The invoice
            preview is delivered later via the ``core.preview.available``
            lifecycle event (Scout consumes it off Core's stream).
        """
        # Step 1: Run ingestion pipeline (metadata + JWT forwarded to HeartBeat)
        ingest_result = await self._ingestion.ingest(
            files, api_key, trace_id,
            metadata=metadata, jwt_token=jwt_token,
        )

        # Step 2: Kick off the Core preview WITHOUT blocking the response.
        # The request returns immediately; when (if) the preview arrives, the
        # background task emits core.preview.available via the lifecycle seam.
        self._spawn_preview(ingest_result, trace_id)

        logger.info(
            f"[{trace_id}] Bulk ingested — queued for async preview "
            f"(queue_id={ingest_result.queue_id}, "
            f"data_uuid={ingest_result.data_uuid})"
        )
        return BulkResult(ingest=ingest_result, status="queued")

    # ── Async preview → core.preview.available ───────────────────────────────

    def _spawn_preview(self, ingest_result: IngestResult, trace_id: str) -> None:
        """Schedule the background preview-fetch+emit, tracking the task ref."""
        task = asyncio.ensure_future(
            self._await_preview_and_emit(ingest_result, trace_id)
        )
        self._preview_tasks.add(task)
        task.add_done_callback(self._preview_tasks.discard)

    async def _await_preview_and_emit(
        self, ingest_result: IngestResult, trace_id: str
    ) -> None:
        """Wait for Core's preview, then emit ``core.preview.available``.

        Best-effort: a timeout or Core error emits **nothing** — the bytes are
        already committed in HeartBeat and Scout reconciles via polling / Core's
        own status. Never raises (background task).
        """
        try:
            preview = await asyncio.wait_for(
                self._core.process_preview(
                    queue_id=ingest_result.queue_id,
                    timeout=self._preview_timeout,
                ),
                timeout=self._preview_timeout,
            )
        except asyncio.TimeoutError:
            logger.info(
                f"[{trace_id}] Bulk preview timed out — no event emitted "
                f"(queue_id={ingest_result.queue_id}, "
                f"timeout={self._preview_timeout}s); Scout reconciles."
            )
            return
        except Exception as e:  # Core unreachable / transient — swallow
            logger.warning(
                f"[{trace_id}] Core preview failed — no event emitted "
                f"(queue_id={ingest_result.queue_id}): {e}"
            )
            return

        await self._emit_preview_available(ingest_result, trace_id, preview)

    async def _emit_preview_available(
        self,
        ingest_result: IngestResult,
        trace_id: str,
        preview: Dict[str, Any],
    ) -> None:
        """Publish the ``core.preview.available`` lifecycle event.

        Carries the client ``trace_id`` (echoed top-level for Scout's reducer)
        plus the batch/blob identity so Scout can match the preview to the
        optimistic row, and the ``preview_data`` payload Core produced.
        """
        if self._lifecycle is None:
            logger.debug(
                f"[{trace_id}] Preview ready but no lifecycle publisher wired "
                f"— skipping core.preview.available (queue_id={ingest_result.queue_id})"
            )
            return

        event = LifecycleEvent(
            family=FAMILY_PREVIEW_AVAILABLE,
            trace_id=trace_id,
            data={
                # Batch/blob identity so Scout matches the preview to its row.
                "data_uuid": ingest_result.data_uuid,
                "queue_id": ingest_result.queue_id,
                "file_uuids": list(ingest_result.blob_uuids),
                "file_hashes": list(ingest_result.file_hashes),
                "filenames": list(ingest_result.filenames),
                "file_count": ingest_result.file_count,
                # The invoice preview Core produced (was the inline field).
                "preview_data": preview.get("preview_data"),
            },
        )
        await self._lifecycle.publish(event)
        logger.info(
            f"[{trace_id}] Emitted {FAMILY_PREVIEW_AVAILABLE} — "
            f"queue_id={ingest_result.queue_id}, data_uuid={ingest_result.data_uuid}"
        )
