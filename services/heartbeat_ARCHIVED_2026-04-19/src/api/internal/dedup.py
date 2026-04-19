"""
Deduplication API Endpoints (Internal Service)

GET  /api/dedup/check   — Check for duplicate hash
POST /api/dedup/record  — Record processed hash

These match Relay HeartBeatClient's expected URLs exactly.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ...handlers import dedup_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dedup", tags=["dedup"])


# ── Request/Response Models ─────────────────────────────────────────────

class DedupCheckResponse(BaseModel):
    is_duplicate: bool
    file_hash: str
    original_queue_id: Optional[str] = None


class DedupRecordRequest(BaseModel):
    file_hash: str = Field(..., min_length=64, max_length=64)
    queue_id: str = Field(..., min_length=1)


class DedupRecordResponse(BaseModel):
    file_hash: str
    queue_id: str
    status: str


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get(
    "/check",
    response_model=DedupCheckResponse,
)
async def check_duplicate(
    file_hash: str = Query(..., min_length=64, max_length=64),
):
    """
    Check if a file hash has been seen before.

    Query parameter: ?file_hash=<sha256hex>
    Returns is_duplicate flag and original queue ID if found.
    """
    try:
        result = await dedup_handler.check_duplicate(file_hash)
        return result
    except Exception as e:
        logger.error(f"check_duplicate failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/record",
    response_model=DedupRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_duplicate(request: DedupRecordRequest):
    """
    Record a file hash after successful processing.

    Idempotent: safe to retry if hash already recorded.
    """
    try:
        result = await dedup_handler.record_duplicate(
            file_hash=request.file_hash,
            queue_id=request.queue_id,
        )
        return result
    except Exception as e:
        logger.error(f"record_duplicate failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )
