"""
Bulk Service (Float Flow)

Float desktop tool uploads files via Relay. **Bulk no longer blocks on Core's
preview** (Q4): the ``/api/ingest`` bulk call returns promptly once the bytes
are safely ingested (``status="queued"`` — accepted, Core still working).

Relay is ingress-only (Q24, ARCH tick56): it ingests the bytes, forwards them to
Core (via ``IngestionService`` → ``CoreClient.enqueue``) and returns. The preview
WORK is Core's; **Core** emits ``core.preview.available`` on its own lifecycle
stream when the preview becomes available, and **Scout** reacts off Core's SSE
stream. Relay does NOT publish lifecycle events (that was an SBS-mock artifact).

Flow:
    1. IngestionService.ingest() → IngestResult  (awaited; forwards to Core)
    2. Return BulkResult(status="queued") immediately.  (no block, no emit)
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ingestion import IngestionService, IngestResult

logger = logging.getLogger(__name__)


@dataclass
class BulkResult:
    """Result of bulk (Float) flow.

    ``status`` is always ``"queued"`` now — the bytes are accepted and Core
    processes the preview asynchronously. ``preview_data`` is intentionally
    gone from this shape (Q4): the preview rides Core's ``core.preview.available``
    lifecycle event, not the bulk HTTP response.
    """

    ingest: IngestResult
    status: str  # "queued" (accepted; preview arrives via Core's stream)


class BulkService:
    """
    Bulk upload flow for Float desktop tool.

    Usage:
        service = BulkService(ingestion, core_client)
        result = await service.process(files, api_key, trace_id)
        # result.status == "queued"; Core emits the preview event off its stream.
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any,
    ):
        self._ingestion = ingestion_service
        self._core = core_client

    async def process(
        self,
        files: List[Tuple[str, bytes]],
        api_key: str,
        trace_id: str = "",
        metadata: Optional[Dict] = None,
        jwt_token: Optional[str] = None,
    ) -> BulkResult:
        """
        Run bulk flow: ingest (forward to Core) → return promptly.

        Args:
            files: List of (filename, file_data) tuples.
            api_key: Authenticated API key.
            trace_id: Request trace ID (carried through the pipeline).
            metadata: SDK identity/trace fields (forwarded through pipeline).
            jwt_token: Bearer JWT (forwarded to HeartBeat/Core).

        Returns:
            BulkResult(status="queued") — the bytes are accepted and forwarded
            to Core. The invoice preview is delivered later via Core's
            ``core.preview.available`` lifecycle event (Scout consumes it off
            Core's stream); Relay does not emit it.
        """
        # Run ingestion pipeline (metadata + JWT forwarded to HeartBeat + Core).
        ingest_result = await self._ingestion.ingest(
            files, api_key, trace_id,
            metadata=metadata, jwt_token=jwt_token,
        )

        logger.info(
            f"[{trace_id}] Bulk ingested — queued (Core emits preview) "
            f"(queue_id={ingest_result.queue_id}, "
            f"data_uuid={ingest_result.data_uuid})"
        )
        return BulkResult(ingest=ingest_result, status="queued")
