"""
Blob Status API Endpoints

GET  /api/v1/heartbeat/blob/{uuid}/status — Get blob status (Float SDK)
POST /api/v1/heartbeat/blob/{uuid}/status — Update blob status (Core)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...handlers import status_handler
from ...errors import BlobNotFoundError, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/heartbeat/blob", tags=["blob-status"])


class BlobStatusResponse(BaseModel):
    """Canonical blob status response (file_entries table)."""
    blob_uuid: str
    file_display_id: Optional[str] = None
    batch_display_id: Optional[str] = None
    status: str  # staged, uploading, uploaded, processing, preview_pending, finalized, error
    processing_stage: Optional[str] = None
    error_message: Optional[str] = None
    # Processing statistics (Core-populated)
    extracted_invoice_count: Optional[int] = None
    rejected_invoice_count: Optional[int] = None
    submitted_invoice_count: Optional[int] = None
    duplicate_count: Optional[int] = None
    # Timestamps
    uploaded_at_iso: Optional[str] = None
    processed_at_iso: Optional[str] = None
    finalized_at_iso: Optional[str] = None


class BlobStatusUpdateRequest(BaseModel):
    """Status update from Core. Includes optional processing stats."""
    status: str = Field(..., min_length=1)
    processing_stage: Optional[str] = None
    error_message: Optional[str] = None
    # Processing statistics (Core sends these after pipeline completion)
    extracted_invoice_count: Optional[int] = None
    rejected_invoice_count: Optional[int] = None
    submitted_invoice_count: Optional[int] = None
    duplicate_count: Optional[int] = None


class BlobStatusUpdateResponse(BaseModel):
    blob_uuid: str
    file_display_id: Optional[str] = None
    status: str
    updated_at: str


@router.get(
    "/{blob_uuid}/status",
    response_model=BlobStatusResponse,
)
async def get_blob_status(blob_uuid: str):
    """
    Get blob processing status.

    Called by Float SDK to show processing progress.
    """
    try:
        return await status_handler.get_blob_status(blob_uuid)
    except BlobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "blob_uuid": blob_uuid},
        )


@router.post(
    "/{blob_uuid}/status",
    response_model=BlobStatusUpdateResponse,
)
async def update_blob_status(
    blob_uuid: str,
    request: BlobStatusUpdateRequest,
):
    """
    Update blob processing status.

    Called by Core when processing state changes.
    """
    # Build processing stats dict if any stats provided
    processing_stats = {}
    for key in ("extracted_invoice_count", "rejected_invoice_count",
                "submitted_invoice_count", "duplicate_count"):
        val = getattr(request, key, None)
        if val is not None:
            processing_stats[key] = val

    try:
        return await status_handler.update_blob_status(
            blob_uuid=blob_uuid,
            status=request.status,
            processing_stage=request.processing_stage,
            error_message=request.error_message,
            processing_stats=processing_stats or None,
        )
    except BlobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "blob_uuid": blob_uuid},
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.to_dict(),
        )
