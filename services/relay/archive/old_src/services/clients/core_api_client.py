"""
Core API Client

Handles all communication with the Core API service.
Inherits retry logic from BaseClient.

API Methods:
- enqueue(): Queue a file for processing
- process_preview(): Process file for preview (blocking, with timeout)
- process_immediate(): Process file immediately (auto-process)
- finalize(): Finalize processing after user review
- get_invoice(): Get invoice details
"""

import logging
from typing import Optional, Dict, Any
import asyncio

from .base_client import BaseClient
from ..errors import CoreUnavailableError


logger = logging.getLogger(__name__)


class CoreAPIClient(BaseClient):
    """
    Client for Core API service.

    Handles enqueue, preview, finalize, and status operations.
    All operations support timeout and retry logic via BaseClient.
    """

    def __init__(
        self,
        core_api_url: str,
        timeout: float = 30.0,
        preview_timeout: float = 300.0,  # 5 minutes for preview processing
        max_attempts: int = 5,
        trace_id: Optional[str] = None,
    ):
        """
        Initialize Core API client.

        Args:
            core_api_url: Base URL for Core API (e.g., http://localhost:8080)
            timeout: Default timeout for API calls (seconds)
            preview_timeout: Specific timeout for preview processing (seconds)
            max_attempts: Max retry attempts (inherited from BaseClient)
            trace_id: Optional trace ID for request tracking
        """
        super().__init__(
            max_attempts=max_attempts,
            initial_delay=1.0,
            timeout=timeout,
            trace_id=trace_id,
        )

        self.core_api_url = core_api_url.rstrip("/")
        self.preview_timeout = preview_timeout

    async def enqueue(
        self,
        blob_uuid: str,
        filename: str,
        file_size_bytes: int,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enqueue a file for processing in Core.

        Args:
            blob_uuid: UUID of blob in storage
            filename: Original filename
            file_size_bytes: File size in bytes
            batch_id: Optional batch identifier

        Returns:
            Response with queue_id and metadata

        Raises:
            CoreUnavailableError: If Core API is unavailable
        """

        payload = {
            "blob_uuid": blob_uuid,
            "filename": filename,
            "file_size_bytes": file_size_bytes,
            "batch_id": batch_id or blob_uuid,
            "trace_id": self.trace_id,
        }

        async def _call():
            # In Phase 1A, this is a stub
            # Phase 1B will implement actual HTTP calls using httpx or aiohttp
            logger.debug(f"Enqueue request: {payload}")
            return {
                "queue_id": f"queue_{blob_uuid}",
                "status": "queued",
                "batch_id": batch_id or blob_uuid,
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise CoreUnavailableError(
                f"Failed to enqueue file: {str(e)}"
            ) from e

    async def process_preview(
        self,
        queue_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Process file for preview (with timeout).

        This is the main async operation that waits for Core to process a file
        and return preview data. Uses a longer timeout (5 minutes by default).

        Args:
            queue_id: Queue ID from enqueue()
            timeout: Override default preview timeout (seconds)

        Returns:
            Preview data from Core

        Raises:
            asyncio.TimeoutError: If Core takes longer than timeout
            CoreUnavailableError: If Core API is unavailable
        """

        use_timeout = timeout or self.preview_timeout

        async def _call():
            # In Phase 1A, this is a stub
            # Phase 1B will implement actual HTTP calls
            logger.debug(f"Process preview request: queue_id={queue_id}")
            return {
                "queue_id": queue_id,
                "status": "processed",
                "preview_data": {
                    "invoices_count": 0,
                    "duplicates_detected": 0,
                    "errors": [],
                },
            }

        try:
            # Use custom timeout for preview processing
            return await asyncio.wait_for(
                self.call_with_retries(_call),
                timeout=use_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Preview processing timeout ({use_timeout}s) for queue_id={queue_id} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
            # Re-raise timeout - caller decides whether to wait async or return "queued"
            raise
        except Exception as e:
            raise CoreUnavailableError(
                f"Failed to process preview: {str(e)}"
            ) from e

    async def process_immediate(
        self,
        queue_id: str,
    ) -> Dict[str, Any]:
        """
        Process file immediately (auto-process mode).

        Used for auto-processing from NAS/Watcher sources.

        Args:
            queue_id: Queue ID from enqueue()

        Returns:
            Processing result with invoice data

        Raises:
            CoreUnavailableError: If Core API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(f"Process immediate request: queue_id={queue_id}")
            return {
                "queue_id": queue_id,
                "status": "processed",
                "invoices": [],
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise CoreUnavailableError(
                f"Failed to process immediately: {str(e)}"
            ) from e

    async def finalize(
        self,
        queue_id: str,
        user_edits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finalize processing after user review and edits.

        Args:
            queue_id: Queue ID from preview
            user_edits: Optional edits made by user during preview review

        Returns:
            Finalized invoice data

        Raises:
            CoreUnavailableError: If Core API is unavailable
        """

        payload = {
            "queue_id": queue_id,
            "user_edits": user_edits or {},
            "trace_id": self.trace_id,
        }

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(f"Finalize request: {payload}")
            return {
                "queue_id": queue_id,
                "status": "finalized",
                "invoices_created": 0,
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise CoreUnavailableError(
                f"Failed to finalize: {str(e)}"
            ) from e

    async def health_check(self) -> bool:
        """
        Check if Core API is healthy.

        Returns:
            True if healthy, False otherwise
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug("Core health check")
            return True

        try:
            result = await asyncio.wait_for(
                self.call_with_retries(_call),
                timeout=5.0,
            )
            return result
        except Exception:
            return False
