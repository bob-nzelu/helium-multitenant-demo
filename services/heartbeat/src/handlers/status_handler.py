"""
Blob Status Handler — Business logic for blob status get/update.

Called by api/internal/blob_status.py router.
Used by Float SDK (GET) and Core (POST) to track processing lifecycle.

Canonical blob schema v1.4.0: file_entries table with 7-status enum.
"""

import logging
from typing import Any, Dict, Optional

from ..database import get_blob_database
from ..errors import BlobNotFoundError

logger = logging.getLogger(__name__)

# Canonical file status enum (7 values per canonical blob schema v1.4.0)
VALID_STATUSES = {
    "staged", "uploading", "uploaded",
    "processing", "preview_pending",
    "finalized", "error",
}


async def get_blob_status(blob_uuid: str) -> Dict[str, Any]:
    """
    Get blob processing status from file_entries.

    Returns canonical status response with dual identity + processing stats.
    """
    db = get_blob_database()
    blob = db.get_blob(blob_uuid)

    if not blob:
        raise BlobNotFoundError(blob_uuid)

    return {
        "blob_uuid": blob["blob_uuid"],
        "file_display_id": blob.get("file_display_id"),
        "batch_display_id": blob.get("batch_display_id"),
        "status": blob["status"],
        "processing_stage": blob.get("processing_stage"),
        "error_message": blob.get("error_message"),
        # Processing statistics (Core-populated)
        "extracted_invoice_count": blob.get("extracted_invoice_count"),
        "rejected_invoice_count": blob.get("rejected_invoice_count"),
        "submitted_invoice_count": blob.get("submitted_invoice_count"),
        "duplicate_count": blob.get("duplicate_count"),
        # Timestamps
        "uploaded_at_iso": blob.get("uploaded_at_iso"),
        "processed_at_iso": blob.get("processed_at_iso"),
        "finalized_at_iso": blob.get("finalized_at_iso"),
    }


async def update_blob_status(
    blob_uuid: str,
    status: str,
    processing_stage: Optional[str] = None,
    error_message: Optional[str] = None,
    processing_stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Update file processing status in file_entries.

    Called by Core when processing state changes. Supports error_message
    and processing statistics (invoice counts).

    Returns: {blob_uuid, file_display_id, status, updated_at}
    """
    if status not in VALID_STATUSES:
        from ..errors import ValidationError
        raise ValidationError(
            message=f"Invalid status: {status}. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )

    db = get_blob_database()

    # Verify blob exists
    blob = db.get_blob(blob_uuid)
    if not blob:
        raise BlobNotFoundError(blob_uuid)

    rows_affected = db.update_blob_status(
        blob_uuid=blob_uuid,
        status=status,
        processing_stage=processing_stage,
        error_message=error_message,
        processing_stats=processing_stats,
    )

    if rows_affected == 0:
        raise BlobNotFoundError(blob_uuid)

    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    file_display_id = blob.get("file_display_id")
    batch_display_id = blob.get("batch_display_id")

    logger.info(f"Blob status updated: {blob_uuid} → {status}")

    # Publish blob.status_changed SSE event with canonical dual identity
    event_data = {
        "blob_uuid": blob_uuid,
        "file_display_id": file_display_id,
        "batch_display_id": batch_display_id,
        "status": status,
        "processing_stage": processing_stage,
    }
    if processing_stats:
        event_data.update(processing_stats)
    if error_message:
        event_data["error_message"] = error_message

    try:
        from ..events import get_event_bus
        bus = get_event_bus()
        await bus.publish("blob.status_changed", event_data)
    except Exception as e:
        logger.warning(f"P2-D publish blob.status_changed failed (non-critical): {e}")

    # SSE Spec: publish to authenticated stream + event ledger
    try:
        from ..sse.publish import publish_event
        await publish_event("blob.status_changed", event_data, data_uuid=blob_uuid)
    except Exception as e:
        logger.warning(f"SSE ledger publish blob.status_changed failed (non-critical): {e}")

    return {
        "blob_uuid": blob_uuid,
        "file_display_id": file_display_id,
        "status": status,
        "updated_at": now_iso,
    }
