"""
Core API Client

Stub client for Helium Core service.
Phase 1: Returns mock responses. Phase 2: Real HTTP calls via httpx.

Core handles invoice processing (OCR, extraction, validation).
Relay enqueues files and optionally waits for preview results.

NOTE: Relay does NOT health-check Core. HeartBeat owns service health
monitoring. Relay discovers Core unavailability through actual request
failures, which trigger graceful degradation (status="queued").
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from uuid6 import uuid7

from .base import BaseClient
from ..errors import CoreUnavailableError

logger = logging.getLogger(__name__)


class CoreClient(BaseClient):
    """
    Client for Helium Core API.

    Endpoints (Phase 2):
        POST /api/enqueue           → Queue file for processing
        POST /api/process/preview   → Process and return preview (bulk flow)
        POST /api/process/immediate → Fire-and-forget (external API flow)
        POST /api/finalize          → Finalize with user edits

    No health_check — HeartBeat owns service health monitoring.
    """

    def __init__(
        self,
        core_api_url: str = "http://localhost:8080",
        timeout: float = 30.0,
        preview_timeout: float = 300.0,
        max_attempts: int = 5,
        trace_id: Optional[str] = None,
    ):
        super().__init__(
            max_attempts=max_attempts,
            timeout=timeout,
            trace_id=trace_id,
        )
        self.core_api_url = core_api_url
        self.preview_timeout = preview_timeout

    async def enqueue(
        self,
        blob_uuid: str,
        filename: str,
        file_size_bytes: int,
        batch_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enqueue a file for Core processing.

        Args:
            blob_uuid: Blob UUID from HeartBeat.
            filename: Original filename.
            file_size_bytes: File size in bytes.
            batch_id: Batch identifier.
            metadata: SDK identity/trace fields (for Core traceability).
            jwt_token: Bearer JWT for user identity verification.

        Returns:
            {"queue_id": str, "status": "queued", "batch_id": str}

        Raises:
            CoreUnavailableError: If Core is unreachable.
        """
        async def _enqueue():
            # Phase 1 stub — returns mock response
            queue_id = f"queue_{uuid7()}"
            logger.debug(
                f"Core enqueue (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "queued",
                "batch_id": batch_id,
                "blob_uuid": blob_uuid,
            }

        return await self.call_with_retries(_enqueue)

    async def process_preview(
        self,
        queue_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Request Core to process a file and return preview data.

        Used by Float bulk flow — waits for Core to finish (up to 5 min).

        Args:
            queue_id: Queue ID from enqueue().
            timeout: Override preview timeout (seconds).

        Returns:
            {"queue_id": str, "status": "processed", "preview_data": {...}}

        Raises:
            asyncio.TimeoutError: If Core takes too long.
            CoreUnavailableError: If Core is unreachable.
        """
        effective_timeout = timeout or self.preview_timeout

        async def _process():
            # Phase 1 stub — simulates fast processing
            await asyncio.sleep(0.01)  # Simulate processing time
            return {
                "queue_id": queue_id,
                "status": "processed",
                "preview_data": {
                    "invoice_count": 1,
                    "total_amount": 0.0,
                    "currency": "NGN",
                    "items": [],
                },
            }

        # Use wait_for with preview timeout (separate from per-request timeout)
        return await asyncio.wait_for(
            _process(),
            timeout=effective_timeout,
        )

    async def process_immediate(self, queue_id: str) -> Dict[str, Any]:
        """
        Process file immediately without preview (for external API flow).

        Core processes in background — this returns as soon as Core acknowledges.

        Args:
            queue_id: Queue ID from enqueue().

        Returns:
            {"queue_id": str, "status": "processed"}
        """
        async def _process():
            logger.debug(
                f"Core process_immediate (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "processed",
            }

        return await self.call_with_retries(_process)

    async def finalize(
        self,
        queue_id: str,
        user_edits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finalize a previewed invoice with optional user edits.

        Args:
            queue_id: Queue ID to finalize.
            user_edits: Optional dict of user corrections.

        Returns:
            {"queue_id": str, "status": "finalized", "invoices_created": int}
        """
        async def _finalize():
            logger.debug(
                f"Core finalize (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "finalized",
                "invoices_created": 1,
            }

        return await self.call_with_retries(_finalize)
