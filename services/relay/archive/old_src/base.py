"""
Base Relay Service

Abstract base class that all relay service types inherit from.
Implements shared functionality:
- File deduplication (local cache + HeartBeat check)
- HeartBeat integration (blob write, status checks, usage limits)
- Error handling (retry logic, graceful degradation)
- Audit logging

Decision from RELAY_DECISIONS.md:
All relay types (Bulk, Queue, Watcher, DBC, API, Polling, Email) inherit
from BaseRelayService and implement ingest_file() for type-specific logic.
"""

import logging
import hashlib
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Set
from datetime import datetime

from .services.clients import (
    BaseClient,
    CoreAPIClient,
    HeartBeatClient,
    AuditAPIClient,
)
from .services.errors import (
    RelayError,
    DuplicateFileError,
    RateLimitExceededError,
)


logger = logging.getLogger(__name__)


class BaseRelayService(ABC):
    """
    Abstract base class for all relay service types.

    Shared functionality:
    - Deduplication (local session cache + HeartBeat persistent check)
    - Blob storage integration (via HeartBeatClient)
    - Usage limit enforcement (via HeartBeatClient)
    - Audit logging (via AuditAPIClient)
    - Error handling (retry logic, graceful degradation)

    Subclasses must implement:
    - ingest_file(file_data: bytes) -> Dict[str, Any]

    Session Cache:
    - Scoped to single HTTP request
    - Prevents duplicate files within same batch
    - Different browser sessions have different caches
    """

    def __init__(
        self,
        service_name: str,
        core_client: CoreAPIClient,
        heartbeat_client: HeartBeatClient,
        audit_client: AuditAPIClient,
        trace_id: Optional[str] = None,
    ):
        """
        Initialize BaseRelayService.

        Args:
            service_name: Name of relay service (e.g., 'relay-bulk', 'relay-watcher')
            core_client: Client for Core API
            heartbeat_client: Client for HeartBeat (blob, dedup, limits)
            audit_client: Client for audit logging
            trace_id: Optional trace ID for request tracking
        """

        self.service_name = service_name
        self.core_client = core_client
        self.heartbeat_client = heartbeat_client
        self.audit_client = audit_client
        self.trace_id = trace_id or BaseClient()._generate_trace_id()

        # Session-scoped deduplication cache (cleared after request)
        self.session_dedup_cache: Set[str] = set()

        logger.debug(
            f"Initialized {service_name} - trace_id={self.trace_id}",
            extra={"trace_id": self.trace_id},
        )

    @abstractmethod
    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Ingest a file for processing.

        Must be implemented by subclasses for type-specific logic:
        - RelayBulkService: HTTP multipart upload handling, ZIP creation
        - RelayWatcherService: File system monitoring, auto-dedup
        - RelayDBCService: Database query, transformation
        - etc.

        Args:
            file_data: File bytes
            filename: Original filename
            batch_id: Optional batch identifier
            **kwargs: Type-specific arguments

        Returns:
            Response dict with status and metadata

        Raises:
            RelayError: On validation, dedup, or processing errors
        """

        pass

    async def _check_duplicate(
        self,
        file_data: bytes,
    ) -> bool:
        """
        Check if file is duplicate (two-level defense).

        Level 1: Session cache (current batch) - fast, memory lookup
        Level 2: HeartBeat persistent cache (across all uploads) - thorough check

        Args:
            file_data: File bytes to check

        Returns:
            True if duplicate, False if new file

        Raises:
            DuplicateFileError: If duplicate detected
        """

        file_hash = self._compute_file_hash(file_data)

        # Level 1: Session cache (current batch)
        if file_hash in self.session_dedup_cache:
            logger.warning(
                f"Duplicate detected in session cache: hash={file_hash} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
            raise DuplicateFileError(file_hash)

        # Level 2: HeartBeat persistent cache (across all uploads)
        hb_result = await self.heartbeat_client.check_duplicate(file_hash)

        if hb_result.get("is_duplicate"):
            logger.warning(
                f"Duplicate detected in HeartBeat: hash={file_hash}, "
                f"original_queue_id={hb_result.get('original_queue_id')} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )

            error = DuplicateFileError(
                file_hash,
                original_queue_id=hb_result.get("original_queue_id"),
            )
            raise error

        # Not a duplicate - add to session cache
        self.session_dedup_cache.add(file_hash)
        logger.debug(
            f"File hash added to session cache: {file_hash}",
            extra={"trace_id": self.trace_id},
        )

        return False

    async def _store_deduplication_record(
        self,
        file_data: bytes,
        queue_id: str,
    ) -> None:
        """
        Store file hash for future deduplication.

        Called after file is successfully processed.

        Args:
            file_data: File bytes
            queue_id: Queue ID of processed file
        """

        file_hash = self._compute_file_hash(file_data)

        try:
            result = await self.heartbeat_client.record_duplicate(
                file_hash=file_hash,
                queue_id=queue_id,
            )

            logger.debug(
                f"Deduplication record stored: hash={file_hash}, "
                f"queue_id={queue_id} - trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )

        except Exception as e:
            # Log warning but don't fail - future dedup is less critical
            logger.warning(
                f"Failed to store deduplication record: {str(e)} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )

    @staticmethod
    def _compute_file_hash(file_data: bytes) -> str:
        """
        Compute SHA256 hash of file bytes.

        Args:
            file_data: File bytes

        Returns:
            Hexadecimal hash string
        """

        return hashlib.sha256(file_data).hexdigest()

    async def _check_rate_limit(
        self,
        api_key: str,
    ) -> bool:
        """
        Check if daily usage limit is reached.

        Args:
            api_key: Client API key

        Returns:
            True if limit reached, False otherwise

        Raises:
            RateLimitExceededError: If limit exceeded
        """

        try:
            limit_info = await self.heartbeat_client.check_daily_limit(api_key)

            if limit_info.get("limit_reached"):
                logger.warning(
                    f"Rate limit exceeded for api_key={api_key[:10]}... - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                )

                raise RateLimitExceededError(
                    message=f"Daily limit of {limit_info.get('daily_limit')} files reached",
                    retry_after_seconds=86400,  # 24 hours
                )

            return False

        except RateLimitExceededError:
            raise
        except Exception as e:
            # Log warning but don't fail - let request proceed
            logger.warning(
                f"Failed to check rate limit: {str(e)} - "
                f"trace_id={self.trace_id}",
                extra={"trace_id": self.trace_id},
            )
            return False

    def _generate_batch_id(self) -> str:
        """Generate unique batch identifier"""

        import uuid

        return f"batch_{uuid.uuid4()}"

    def clear_session_cache(self) -> None:
        """
        Clear session-scoped deduplication cache.

        Should be called after each HTTP request completes.
        """

        cache_size = len(self.session_dedup_cache)
        self.session_dedup_cache.clear()

        logger.debug(
            f"Session cache cleared ({cache_size} items) - "
            f"trace_id={self.trace_id}",
            extra={"trace_id": self.trace_id},
        )
