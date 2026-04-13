"""
HeartBeat API Client

Handles all communication with the HeartBeat service.
Inherits retry logic from BaseClient.

API Methods:
- write_blob(): Write file to blob storage
- check_duplicate(): Check if file is duplicate
- record_duplicate(): Record file hash for future dedup
- check_daily_limit(): Check if daily usage limit reached
- register_blob(): Register blob with HeartBeat
- reconcile(): Request reconciliation when Core unavailable
"""

import logging
import hashlib
from typing import Optional, Dict, Any

from .base_client import BaseClient
from ..errors import HeartBeatUnavailableError


logger = logging.getLogger(__name__)


class HeartBeatClient(BaseClient):
    """
    Client for HeartBeat service.

    Handles blob storage operations, deduplication checks, and usage limits.
    All blob operations must go through HeartBeat (not direct MinIO access).
    """

    def __init__(
        self,
        heartbeat_api_url: str,
        timeout: float = 30.0,
        max_attempts: int = 5,
        trace_id: Optional[str] = None,
    ):
        """
        Initialize HeartBeat client.

        Args:
            heartbeat_api_url: Base URL for HeartBeat API (e.g., http://localhost:9000)
            timeout: Timeout for API calls (seconds)
            max_attempts: Max retry attempts (inherited from BaseClient)
            trace_id: Optional trace ID for request tracking
        """
        super().__init__(
            max_attempts=max_attempts,
            initial_delay=1.0,
            timeout=timeout,
            trace_id=trace_id,
        )

        self.heartbeat_api_url = heartbeat_api_url.rstrip("/")

    async def write_blob(
        self,
        blob_uuid: str,
        filename: str,
        file_data: bytes,
    ) -> Dict[str, Any]:
        """
        Write file to blob storage via HeartBeat.

        Args:
            blob_uuid: UUID for the blob
            filename: Original filename
            file_data: File bytes

        Returns:
            Response with blob_path and metadata

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            # Phase 1B will implement actual HTTP calls to HeartBeat
            file_hash = hashlib.sha256(file_data).hexdigest()
            logger.debug(
                f"Write blob: uuid={blob_uuid}, size={len(file_data)}, hash={file_hash}",
                extra={"trace_id": self.trace_id},
            )

            return {
                "blob_uuid": blob_uuid,
                "blob_path": f"/files_blob/{blob_uuid}-{filename}",
                "file_size_bytes": len(file_data),
                "file_hash": file_hash,
                "status": "uploaded",
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise HeartBeatUnavailableError(
                f"Failed to write blob: {str(e)}"
            ) from e

    async def check_duplicate(
        self,
        file_hash: str,
    ) -> Dict[str, Any]:
        """
        Check if file (by hash) is duplicate.

        Args:
            file_hash: SHA256 hash of file

        Returns:
            Dictionary with is_duplicate flag and optional original_queue_id

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(
                f"Check duplicate: hash={file_hash}",
                extra={"trace_id": self.trace_id},
            )

            return {
                "is_duplicate": False,
                "file_hash": file_hash,
                "original_queue_id": None,
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise HeartBeatUnavailableError(
                f"Failed to check duplicate: {str(e)}"
            ) from e

    async def record_duplicate(
        self,
        file_hash: str,
        queue_id: str,
    ) -> Dict[str, Any]:
        """
        Record file hash for future deduplication.

        Args:
            file_hash: SHA256 hash of file
            queue_id: Queue ID of processed file

        Returns:
            Confirmation response

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(
                f"Record duplicate: hash={file_hash}, queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )

            return {
                "file_hash": file_hash,
                "queue_id": queue_id,
                "status": "recorded",
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise HeartBeatUnavailableError(
                f"Failed to record duplicate: {str(e)}"
            ) from e

    async def check_daily_limit(
        self,
        api_key: str,
    ) -> Dict[str, Any]:
        """
        Check if daily usage limit is reached.

        Args:
            api_key: Client API key

        Returns:
            Dictionary with limit info

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(
                f"Check daily limit: api_key={api_key[:10]}...",
                extra={"trace_id": self.trace_id},
            )

            return {
                "api_key": api_key,
                "files_uploaded_today": 0,
                "daily_limit": 500,
                "limit_reached": False,
                "remaining": 500,
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            raise HeartBeatUnavailableError(
                f"Failed to check daily limit: {str(e)}"
            ) from e

    async def register_blob(
        self,
        blob_uuid: str,
        filename: str,
        file_size_bytes: int,
        file_hash: str,
        api_key: str,
    ) -> Dict[str, Any]:
        """
        Register blob with HeartBeat (event-driven synchronization).

        Called immediately after blob is written to MinIO.
        Allows HeartBeat to track blob metadata and manage lifecycle.

        Args:
            blob_uuid: UUID of blob
            filename: Original filename
            file_size_bytes: File size in bytes
            file_hash: SHA256 hash of file
            api_key: Client API key

        Returns:
            Registration confirmation

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        payload = {
            "blob_uuid": blob_uuid,
            "filename": filename,
            "file_size_bytes": file_size_bytes,
            "file_hash": file_hash,
            "api_key": api_key,
            "trace_id": self.trace_id,
        }

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(
                f"Register blob: uuid={blob_uuid}",
                extra={"trace_id": self.trace_id},
            )

            return {
                "blob_uuid": blob_uuid,
                "status": "registered",
                "tracking_id": f"track_{blob_uuid}",
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            # Log warning but don't fail - blob is already written
            logger.warning(
                f"Failed to register blob: {str(e)} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "blob_uuid": blob_uuid,
                "status": "written_but_not_registered",
            }

    async def reconcile(
        self,
        blob_uuid: str,
    ) -> Dict[str, Any]:
        """
        Request reconciliation when Core is unavailable.

        Asks HeartBeat to verify that blob is safe and processing will continue
        when Core comes back online.

        Args:
            blob_uuid: UUID of blob that needs reconciliation

        Returns:
            Reconciliation confirmation

        Raises:
            HeartBeatUnavailableError: If HeartBeat API is unavailable
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug(
                f"Request reconciliation: uuid={blob_uuid}",
                extra={"trace_id": self.trace_id},
            )

            return {
                "blob_uuid": blob_uuid,
                "status": "reconciled",
                "blob_safe": True,
            }

        try:
            return await self.call_with_retries(_call)
        except Exception as e:
            logger.error(
                f"Reconciliation failed: {str(e)} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
            raise HeartBeatUnavailableError(
                f"Failed to reconcile blob: {str(e)}"
            ) from e

    async def health_check(self) -> bool:
        """
        Check if HeartBeat API is healthy.

        Returns:
            True if healthy, False otherwise
        """

        async def _call():
            # In Phase 1A, this is a stub
            logger.debug("HeartBeat health check")
            return True

        try:
            import asyncio

            result = await asyncio.wait_for(
                self.call_with_retries(_call),
                timeout=5.0,
            )
            return result
        except Exception:
            return False
