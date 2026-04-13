"""
Bulk Service (Float Flow)

Float desktop tool uploads files via Relay, then waits for Core to return
a preview. If Core times out (5 min) or is unavailable, the response is
"queued" — Float can check back via Core later.

Flow:
    1. IngestionService.ingest() → IngestResult
    2. CoreClient.process_preview(queue_id, timeout=300s)
       ├─ Success → {"status": "processed", "preview_data": {...}}
       ├─ Timeout → {"status": "queued"} (Core still processing)
       └─ Error   → {"status": "queued"} (Core unavailable)
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ingestion import IngestionService, IngestResult

logger = logging.getLogger(__name__)


@dataclass
class BulkResult:
    """Result of bulk (Float) flow."""

    ingest: IngestResult
    status: str           # "processed" | "queued"
    preview_data: Optional[Dict[str, Any]] = None


class BulkService:
    """
    Bulk upload flow for Float desktop tool.

    Usage:
        service = BulkService(ingestion, core_client)
        result = await service.process(files, api_key, trace_id)
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any,
        preview_timeout: float = 300.0,
    ):
        self._ingestion = ingestion_service
        self._core = core_client
        self._preview_timeout = preview_timeout

    async def process(
        self,
        files: List[Tuple[str, bytes]],
        api_key: str,
        trace_id: str = "",
        metadata: Optional[Dict] = None,
        jwt_token: Optional[str] = None,
    ) -> BulkResult:
        """
        Run bulk flow: ingest → wait for Core preview.

        Args:
            files: List of (filename, file_data) tuples.
            api_key: Authenticated API key.
            trace_id: Request trace ID.
            metadata: SDK identity/trace fields (forwarded through pipeline).
            jwt_token: Bearer JWT (forwarded to HeartBeat/Core).

        Returns:
            BulkResult with IngestResult + preview_data or "queued" status.
        """
        # Step 1: Run ingestion pipeline (metadata + JWT forwarded to HeartBeat)
        ingest_result = await self._ingestion.ingest(
            files, api_key, trace_id,
            metadata=metadata, jwt_token=jwt_token,
        )

        # Step 2: Wait for Core preview (enforce our own timeout)
        try:
            preview = await asyncio.wait_for(
                self._core.process_preview(
                    queue_id=ingest_result.queue_id,
                    timeout=self._preview_timeout,
                ),
                timeout=self._preview_timeout,
            )
            logger.info(
                f"[{trace_id}] Bulk preview received — "
                f"queue_id={ingest_result.queue_id}"
            )
            return BulkResult(
                ingest=ingest_result,
                status="processed",
                preview_data=preview.get("preview_data"),
            )

        except asyncio.TimeoutError:
            logger.info(
                f"[{trace_id}] Bulk preview timed out — "
                f"queue_id={ingest_result.queue_id}, "
                f"timeout={self._preview_timeout}s"
            )
            return BulkResult(
                ingest=ingest_result,
                status="queued",
            )

        except Exception as e:
            logger.warning(
                f"[{trace_id}] Core preview failed — "
                f"queue_id={ingest_result.queue_id}: {e}"
            )
            return BulkResult(
                ingest=ingest_result,
                status="queued",
            )
