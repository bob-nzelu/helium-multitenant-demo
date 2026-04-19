"""
Blob Outputs API (P2-F)

CRUD endpoints for processed output tracking. Core registers its
processing results here after extracting/validating/enriching a blob.

Endpoints:
    POST /api/outputs/register           — Register a processed output
    GET  /api/outputs/{blob_uuid}        — List outputs for a blob
    GET  /api/outputs/{blob_uuid}/{type} — Get specific output
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...database import get_blob_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outputs", tags=["Blob Outputs"])


class RegisterOutputRequest(BaseModel):
    """Request body for registering a processed output."""
    blob_uuid: str = Field(..., description="Source blob UUID")
    output_type: str = Field(..., description="Output type: firs_invoices, report, customers, etc.")
    object_path: str = Field(..., description="Storage path of the output file")
    content_type: str = Field(..., description="MIME type of the output")
    size_bytes: Optional[int] = Field(None, description="File size in bytes")
    file_hash: Optional[str] = Field(None, description="SHA256 hash of output")
    core_version: Optional[str] = Field(None, description="Core version that created this output")


@router.post("/register")
async def register_output(req: RegisterOutputRequest):
    """
    Register a processed output for a blob.

    Called by Core after processing completes. Upserts — if the same
    (blob_uuid, output_type) already exists, it's updated.
    """
    db = get_blob_database()

    # Verify blob exists
    rows = db.execute_query(
        "SELECT blob_uuid FROM file_entries WHERE blob_uuid = ?",
        (req.blob_uuid,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Blob not found: {req.blob_uuid}")

    now_unix = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat()

    # Upsert: INSERT OR REPLACE on unique(blob_uuid, output_type)
    db.execute_insert(
        """INSERT OR REPLACE INTO blob_outputs
           (blob_uuid, output_type, object_path, content_type,
            size_bytes, file_hash, created_at_unix, created_at_iso,
            created_by_core_version, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            req.blob_uuid, req.output_type, req.object_path,
            req.content_type, req.size_bytes, req.file_hash,
            now_unix, now_iso, req.core_version, now_iso, now_iso,
        ),
    )

    # Update has_processed_outputs flag on the blob entry
    db.execute_insert(
        "UPDATE file_entries SET has_processed_outputs = 1, updated_at = ? WHERE blob_uuid = ?",
        (now_iso, req.blob_uuid),
    )

    return {
        "status": "registered",
        "blob_uuid": req.blob_uuid,
        "output_type": req.output_type,
        "object_path": req.object_path,
    }


@router.get("/{blob_uuid}")
async def list_outputs(blob_uuid: str):
    """
    List all processed outputs for a blob.

    Returns all output types registered for the given blob_uuid.
    """
    db = get_blob_database()

    rows = db.execute_query(
        """SELECT output_type, object_path, content_type, size_bytes,
                  file_hash, created_at_iso, created_by_core_version,
                  accessed_count, last_accessed_unix
           FROM blob_outputs
           WHERE blob_uuid = ?
           ORDER BY created_at_unix""",
        (blob_uuid,),
    )

    return {"blob_uuid": blob_uuid, "outputs": rows, "count": len(rows)}


@router.get("/{blob_uuid}/{output_type}")
async def get_output(blob_uuid: str, output_type: str):
    """
    Get a specific output for a blob by type.

    Also increments the access counter for analytics.
    """
    db = get_blob_database()

    # Check existence
    rows = db.execute_query(
        "SELECT 1 FROM blob_outputs WHERE blob_uuid = ? AND output_type = ?",
        (blob_uuid, output_type),
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No '{output_type}' output for blob {blob_uuid}",
        )

    # Increment access counter first
    now_unix = int(time.time())
    db.execute_insert(
        """UPDATE blob_outputs
           SET accessed_count = accessed_count + 1,
               last_accessed_unix = ?,
               updated_at = ?
           WHERE blob_uuid = ? AND output_type = ?""",
        (now_unix, datetime.now(timezone.utc).isoformat(), blob_uuid, output_type),
    )

    # Then read updated data
    rows = db.execute_query(
        """SELECT output_type, object_path, content_type, size_bytes,
                  file_hash, created_at_iso, created_by_core_version,
                  accessed_count, last_accessed_unix
           FROM blob_outputs
           WHERE blob_uuid = ? AND output_type = ?""",
        (blob_uuid, output_type),
    )

    return {"blob_uuid": blob_uuid, **rows[0]}
