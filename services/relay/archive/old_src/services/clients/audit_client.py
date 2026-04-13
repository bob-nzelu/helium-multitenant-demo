"""
Audit API Client

Handles logging audit events to the HeartBeat audit.db.
Inherits retry logic from BaseClient.

API Methods:
- log_batch_ingestion_started(): Log batch ingestion start
- log_file_ingested(): Log individual file ingestion
- log_batch_ingestion_completed(): Log batch completion
- log_error(): Log error event
- log_authentication_failure(): Log authentication failure
- log_rate_limit_exceeded(): Log rate limit event
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from .base_client import BaseClient
from ..errors import ServiceUnavailableError


logger = logging.getLogger(__name__)


class AuditAPIClient(BaseClient):
    """
    Client for audit logging.

    Logs all significant events to audit trail (via HeartBeat API).
    Uses structured JSON format with timestamp, service, and event_type.

    Note: Audit logging failures should not block request processing.
    Retry logic handles transient failures gracefully.
    """

    def __init__(
        self,
        heartbeat_api_url: str,
        service_name: str = "relay-bulk",
        timeout: float = 30.0,
        max_attempts: int = 3,  # Fewer retries for audit (don't block requests)
        trace_id: Optional[str] = None,
    ):
        """
        Initialize audit client.

        Args:
            heartbeat_api_url: Base URL for HeartBeat (where audit.db lives)
            service_name: Name of the service logging events
            timeout: Timeout for audit API calls (seconds)
            max_attempts: Max retry attempts (fewer than main clients)
            trace_id: Optional trace ID for request tracking
        """
        super().__init__(
            max_attempts=max_attempts,
            initial_delay=0.5,  # Shorter initial delay for audit
            timeout=timeout,
            trace_id=trace_id,
        )

        self.heartbeat_api_url = heartbeat_api_url.rstrip("/")
        self.service_name = service_name

    async def log_batch_ingestion_started(
        self,
        batch_id: str,
        api_key: str,
        total_files: int,
        total_size_mb: float,
    ) -> None:
        """
        Log batch ingestion started event.

        Args:
            batch_id: Unique batch identifier
            api_key: Client API key (partial for privacy)
            total_files: Number of files in batch
            total_size_mb: Total size of batch in MB
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "batch.ingestion.started",
            "batch_id": batch_id,
            "api_key": api_key[:10] + "..." if len(api_key) > 10 else api_key,
            "total_files": total_files,
            "total_size_mb": round(total_size_mb, 2),
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def log_file_ingested(
        self,
        batch_id: str,
        file_uuid: str,
        filename: str,
        file_size_mb: float,
        queue_id: str,
    ) -> None:
        """
        Log individual file ingestion event.

        Args:
            batch_id: Parent batch identifier
            file_uuid: UUID of file in blob storage
            filename: Original filename
            file_size_mb: File size in MB
            queue_id: Queue ID assigned to file
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "file.ingested",
            "batch_id": batch_id,
            "file_uuid": file_uuid,
            "filename": filename,
            "file_size_mb": round(file_size_mb, 2),
            "queue_id": queue_id,
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def log_batch_ingestion_completed(
        self,
        batch_id: str,
        successful_count: int,
        duplicate_count: int,
        failed_count: int,
    ) -> None:
        """
        Log batch ingestion completed event.

        Args:
            batch_id: Batch identifier
            successful_count: Number of successfully processed files
            duplicate_count: Number of duplicate files detected
            failed_count: Number of failed files
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "batch.ingestion.completed",
            "batch_id": batch_id,
            "successful_count": successful_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "total_count": successful_count + duplicate_count + failed_count,
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def log_error(
        self,
        error_code: str,
        filename: Optional[str],
        details: str,
    ) -> None:
        """
        Log error event.

        Args:
            error_code: Error code (e.g., VALIDATION_FAILED)
            filename: Filename associated with error (if applicable)
            details: Error details
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "error.occurred",
            "error_code": error_code,
            "filename": filename,
            "details": details,
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def log_authentication_failure(
        self,
        api_key: str,
        error: str,
    ) -> None:
        """
        Log authentication failure event.

        Args:
            api_key: API key that failed (partial for privacy)
            error: Error message
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "authentication.failed",
            "api_key": api_key[:10] + "..." if len(api_key) > 10 else api_key,
            "error": error,
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def log_rate_limit_exceeded(
        self,
        api_key: str,
        current_usage: int,
        limit: int,
    ) -> None:
        """
        Log rate limit exceeded event.

        Args:
            api_key: API key that hit limit (partial for privacy)
            current_usage: Current usage count
            limit: Daily limit
        """

        event_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "event_type": "rate_limit.exceeded",
            "api_key": api_key[:10] + "..." if len(api_key) > 10 else api_key,
            "current_usage": current_usage,
            "limit": limit,
            "trace_id": self.trace_id,
        }

        await self._log_event(event_data)

    async def _log_event(self, event_data: Dict[str, Any]) -> None:
        """
        Internal method to log event to audit trail.

        Logs are fire-and-forget - failures don't block request processing.

        Args:
            event_data: Structured event data in JSON format
        """

        async def _call():
            # In Phase 1A, this is a stub
            # Phase 1B will implement actual HTTP calls to HeartBeat
            logger.debug(
                f"Log audit event: {event_data['event_type']}",
                extra={"trace_id": self.trace_id},
            )
            return {"status": "logged"}

        try:
            await self.call_with_retries(_call)
        except Exception as e:
            # Don't raise - audit logging is non-critical
            # But do log the failure
            logger.warning(
                f"Failed to log audit event: {str(e)} - "
                f"event_type={event_data.get('event_type')} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
