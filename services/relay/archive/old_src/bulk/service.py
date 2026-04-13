"""
Relay Bulk Upload Service

Main service class for handling bulk file uploads from Float UI.

Responsibilities:
- Inherit from BaseRelayService
- Implement ingest_file() for bulk-specific logic
- Handle ZIP creation for multiple files
- Integrate with Core API (process_preview, finalize)
- Session-scoped deduplication cache

Decision from RELAY_DECISIONS.md:
- Preview mode by default (immediate_processing=false)
- Multiple files get zipped together
- Graceful degradation when Core unavailable
"""

import logging
import uuid
import zipfile
import io
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

from ..base import BaseRelayService
from ..services.clients import CoreAPIClient, HeartBeatClient, AuditAPIClient
from ..services.errors import (
    CoreUnavailableError,
    RelayError,
)
from .validation import BulkValidationPipeline


logger = logging.getLogger(__name__)


class RelayBulkService(BaseRelayService):
    """
    Bulk Upload Relay Service.

    Handles 1-3 file uploads from Float UI with preview mode.

    Flow:
    1. Validate files (via BulkValidationPipeline)
    2. Check deduplication (session cache + HeartBeat)
    3. Create ZIP if multiple files
    4. Write to blob storage (via HeartBeatClient)
    5. Enqueue to Core (via CoreAPIClient)
    6. Call Core processing API (with timeout handling)
    7. Return preview data or "queued" status
    """

    def __init__(
        self,
        core_client: CoreAPIClient,
        heartbeat_client: HeartBeatClient,
        audit_client: AuditAPIClient,
        validation_pipeline: BulkValidationPipeline,
        config: Dict[str, Any],
        trace_id: Optional[str] = None,
    ):
        """
        Initialize Relay Bulk Service.

        Args:
            core_client: Client for Core API
            heartbeat_client: Client for HeartBeat (blob, dedup, limits)
            audit_client: Client for audit logging
            validation_pipeline: File validation pipeline
            config: Bulk service configuration
            trace_id: Optional trace ID for request tracking
        """
        super().__init__(
            service_name="relay-bulk",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
            trace_id=trace_id,
        )

        self.validation_pipeline = validation_pipeline
        self.config = config

        # Extract config values
        self.preview_timeout = config.get("request_timeout_seconds", 300)  # 5 minutes default

        logger.info(
            f"Initialized RelayBulkService - preview_timeout={self.preview_timeout}s",
            extra={"trace_id": self.trace_id},
        )

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Ingest a single file (called by ingest_batch for each file).

        This method is required by BaseRelayService but not directly used for bulk upload.
        Instead, use ingest_batch() which handles multiple files.

        Args:
            file_data: Raw file bytes
            filename: Original filename
            batch_id: Optional batch identifier
            **kwargs: Additional context

        Returns:
            Result dict with status and metadata
        """
        # For bulk upload, always use ingest_batch() instead
        raise NotImplementedError(
            "RelayBulkService uses ingest_batch() for processing multiple files. "
            "Do not call ingest_file() directly."
        )

    async def ingest_batch(
        self,
        files: List[Tuple[str, bytes]],
        company_id: str,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ingest a batch of 1-3 files for preview processing.

        Args:
            files: List of (filename, file_data) tuples
            company_id: Company identifier
            user_id: Optional user identifier
            api_key: API key used for authentication

        Returns:
            Batch result dict with per-file results and preview data
        """
        batch_id = f"batch_{uuid.uuid4()}"
        start_time = datetime.utcnow()

        logger.info(
            f"Starting batch ingestion - batch_id={batch_id}, files={len(files)}",
            extra={"trace_id": self.trace_id, "batch_id": batch_id},
        )

        # Log audit event: batch started
        await self.audit_client.log(
            service=self.service_name,
            event_type="batch.ingestion.started",
            user_id=user_id,
            details={
                "batch_id": batch_id,
                "total_files": len(files),
                "total_size_mb": sum(len(data) for _, data in files) / (1024 * 1024),
                "api_key": api_key,
                "company_id": company_id,
            },
        )

        results = []
        successful_count = 0
        duplicate_count = 0
        failed_count = 0

        for filename, file_data in files:
            try:
                result = await self._process_single_file(
                    filename=filename,
                    file_data=file_data,
                    batch_id=batch_id,
                    company_id=company_id,
                    user_id=user_id,
                )

                results.append(result)

                if result["status"] == "success":
                    successful_count += 1
                elif result["status"] == "duplicate":
                    duplicate_count += 1

            except Exception as e:
                logger.error(
                    f"File processing failed - filename={filename}: {e}",
                    extra={"trace_id": self.trace_id, "batch_id": batch_id},
                    exc_info=True,
                )

                results.append({
                    "filename": filename,
                    "status": "error",
                    "error": str(e),
                })
                failed_count += 1

                # Log audit event: file failed
                await self.audit_client.log(
                    service=self.service_name,
                    event_type="file.processing.failed",
                    user_id=user_id,
                    details={
                        "batch_id": batch_id,
                        "filename": filename,
                        "error": str(e),
                    },
                )

        # Calculate processing time
        processing_time = (datetime.utcnow() - start_time).total_seconds()

        # Log audit event: batch completed
        await self.audit_client.log(
            service=self.service_name,
            event_type="batch.ingestion.completed",
            user_id=user_id,
            details={
                "batch_id": batch_id,
                "successful_files": successful_count,
                "duplicate_files": duplicate_count,
                "failed_files": failed_count,
                "processing_time_seconds": processing_time,
            },
        )

        logger.info(
            f"Batch ingestion completed - batch_id={batch_id}, "
            f"success={successful_count}, duplicate={duplicate_count}, failed={failed_count}",
            extra={"trace_id": self.trace_id, "batch_id": batch_id},
        )

        return {
            "status": "processed",
            "batch_id": batch_id,
            "total_files": len(files),
            "successful_count": successful_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "processing_time_seconds": processing_time,
            "results": results,
        }

    async def _process_single_file(
        self,
        filename: str,
        file_data: bytes,
        batch_id: str,
        company_id: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a single file within a batch.

        Args:
            filename: Original filename
            file_data: Raw file bytes
            batch_id: Batch identifier
            company_id: Company identifier
            user_id: Optional user identifier

        Returns:
            File result dict with status and metadata
        """
        file_uuid = str(uuid.uuid4())

        # 1. Deduplication check
        is_duplicate, duplicate_info = await self._check_duplicate(file_data)

        if is_duplicate:
            logger.info(
                f"Duplicate file detected - filename={filename}",
                extra={"trace_id": self.trace_id, "file_uuid": file_uuid},
            )

            return {
                "filename": filename,
                "status": "duplicate",
                "message": "File already processed",
                "file_hash": duplicate_info.get("file_hash"),
                "original_queue_id": duplicate_info.get("original_queue_id"),
            }

        # 2. Write to blob storage (via HeartBeat)
        blob_filename = f"{file_uuid}-{filename}"

        try:
            blob_path = await self.heartbeat_client.write_blob(
                file_uuid=file_uuid,
                filename=blob_filename,
                data=file_data,
            )

            logger.debug(
                f"Blob written successfully - blob_path={blob_path}",
                extra={"trace_id": self.trace_id, "file_uuid": file_uuid},
            )

        except Exception as e:
            logger.error(
                f"Blob write failed - filename={filename}: {e}",
                extra={"trace_id": self.trace_id, "file_uuid": file_uuid},
                exc_info=True,
            )
            raise RelayError("BLOB_WRITE_FAILED", f"Failed to write blob: {e}") from e

        # 3. Enqueue to Core
        try:
            queue_id = await self.core_client.enqueue(
                file_uuid=file_uuid,
                blob_path=blob_path,
                original_filename=filename,
                source="relay-bulk",
                immediate_processing=False,  # Preview mode for bulk upload
            )

            logger.debug(
                f"File enqueued to Core - queue_id={queue_id}",
                extra={"trace_id": self.trace_id, "file_uuid": file_uuid},
            )

        except Exception as e:
            logger.error(
                f"Core enqueue failed - filename={filename}: {e}",
                extra={"trace_id": self.trace_id, "file_uuid": file_uuid},
                exc_info=True,
            )
            # File is in blob, but not queued - trigger HeartBeat reconciliation
            raise RelayError("CORE_ENQUEUE_FAILED", f"Failed to enqueue to Core: {e}") from e

        # 4. Call Core processing API (with timeout handling)
        try:
            core_response = await asyncio.wait_for(
                self.core_client.process_preview(queue_id),
                timeout=self.preview_timeout,
            )

            logger.info(
                f"Core processing completed - queue_id={queue_id}",
                extra={"trace_id": self.trace_id, "queue_id": queue_id},
            )

            # Log audit event: file ingested successfully
            await self.audit_client.log(
                service=self.service_name,
                event_type="file.ingested",
                user_id=user_id,
                details={
                    "file_uuid": file_uuid,
                    "filename": filename,
                    "file_size_mb": len(file_data) / (1024 * 1024),
                    "blob_path": blob_path,
                    "queue_id": queue_id,
                    "batch_id": batch_id,
                },
            )

            return {
                "filename": filename,
                "status": "success",
                "file_uuid": file_uuid,
                "queue_id": queue_id,
                "blob_path": blob_path,
                "core_response": core_response,
            }

        except asyncio.TimeoutError:
            # Core is taking too long (large batch)
            logger.warning(
                f"Core processing timeout - queue_id={queue_id}",
                extra={"trace_id": self.trace_id, "queue_id": queue_id},
            )

            return {
                "filename": filename,
                "status": "queued",
                "message": "Processing in progress. Large batch may take several minutes.",
                "file_uuid": file_uuid,
                "queue_id": queue_id,
                "blob_path": blob_path,
                "core_response": {
                    "status": "queued",
                    "message": "Preview processing timeout. Check status later via /api/status/{queue_id}",
                },
            }

        except CoreUnavailableError as e:
            # Core is down - graceful degradation
            logger.warning(
                f"Core unavailable - queue_id={queue_id}: {e}",
                extra={"trace_id": self.trace_id, "queue_id": queue_id},
            )

            return {
                "filename": filename,
                "status": "queued",
                "message": "Core service unavailable. Files queued for processing.",
                "file_uuid": file_uuid,
                "queue_id": queue_id,
                "blob_path": blob_path,
                "core_response": {
                    "status": "queued",
                    "message": "Core service unavailable. Processing will resume when service is available.",
                },
            }

    async def _check_duplicate(
        self, file_data: bytes
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if file is a duplicate.

        Uses two-level check:
        1. Session cache (in-memory, current batch)
        2. HeartBeat persistent check (across all uploads)

        Args:
            file_data: Raw file bytes

        Returns:
            Tuple of (is_duplicate, duplicate_info)
        """
        import hashlib

        file_hash = hashlib.sha256(file_data).hexdigest()

        # Level 1: Session cache
        if file_hash in self.session_dedup_cache:
            logger.debug(
                f"Duplicate found in session cache - hash={file_hash[:8]}...",
                extra={"trace_id": self.trace_id},
            )
            return True, {"file_hash": file_hash, "source": "session_cache"}

        # Level 2: HeartBeat persistent check
        try:
            response = await self.heartbeat_client.check_duplicate(file_hash)

            if response.get("is_duplicate"):
                logger.info(
                    f"Duplicate found in HeartBeat - hash={file_hash[:8]}...",
                    extra={"trace_id": self.trace_id},
                )
                return True, {
                    "file_hash": file_hash,
                    "source": "heartbeat",
                    "original_queue_id": response.get("queue_id"),
                }

        except Exception as e:
            # Graceful degradation: If HeartBeat unavailable, allow upload
            logger.warning(
                f"HeartBeat dedup check failed - allowing upload: {e}",
                extra={"trace_id": self.trace_id},
            )

        # Not a duplicate - add to session cache
        self.session_dedup_cache.add(file_hash)

        return False, None

    async def finalize_batch(
        self,
        batch_id: str,
        queue_ids: List[str],
        edits: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Finalize previewed invoices with optional user edits.

        Args:
            batch_id: Batch identifier
            queue_ids: List of queue IDs to finalize
            edits: Optional user edits (dict mapping queue_id -> edits)
            user_id: Optional user identifier

        Returns:
            Finalization result dict
        """
        logger.info(
            f"Finalizing batch - batch_id={batch_id}, queue_ids={queue_ids}",
            extra={"trace_id": self.trace_id, "batch_id": batch_id},
        )

        results = []

        for queue_id in queue_ids:
            queue_edits = edits.get(queue_id) if edits else None

            try:
                core_response = await self.core_client.finalize(
                    queue_id=queue_id,
                    edits=queue_edits,
                )

                results.append({
                    "queue_id": queue_id,
                    "status": "finalized",
                    "core_response": core_response,
                })

                logger.info(
                    f"Queue finalized successfully - queue_id={queue_id}",
                    extra={"trace_id": self.trace_id, "queue_id": queue_id},
                )

            except Exception as e:
                logger.error(
                    f"Queue finalization failed - queue_id={queue_id}: {e}",
                    extra={"trace_id": self.trace_id, "queue_id": queue_id},
                    exc_info=True,
                )

                results.append({
                    "queue_id": queue_id,
                    "status": "error",
                    "error": str(e),
                })

        return {
            "status": "finalized",
            "batch_id": batch_id,
            "results": results,
        }

    def create_zip_from_files(
        self, files: List[Tuple[str, bytes]]
    ) -> bytes:
        """
        Create ZIP archive from multiple files.

        Args:
            files: List of (filename, file_data) tuples

        Returns:
            ZIP file bytes
        """
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for filename, file_data in files:
                zip_file.writestr(filename, file_data)

        zip_bytes = zip_buffer.getvalue()

        logger.debug(
            f"Created ZIP archive - files={len(files)}, size={len(zip_bytes) / (1024 * 1024):.2f}MB",
            extra={"trace_id": self.trace_id},
        )

        return zip_bytes
