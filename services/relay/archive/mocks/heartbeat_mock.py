"""
Mock HeartBeat API Server for Local Development

Simulates HeartBeat API responses for Relay testing.
Used by docker-compose for local development.

Endpoints:
- POST /api/blob/write
- POST /api/blob/register
- GET /api/daily_usage/check
- POST /api/duplicate/check
- POST /api/duplicate/record
- GET /health
"""

import uuid
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any, Set

app = FastAPI(title="HeartBeat API Mock", version="1.0.0")

# In-memory stores
blob_store: Dict[str, Dict[str, Any]] = {}
duplicate_store: Set[str] = set()
daily_usage: Dict[str, int] = {}  # company_id -> file_count


class BlobWriteRequest(BaseModel):
    file_uuid: str
    filename: str
    data: str  # Base64 encoded


class BlobRegisterRequest(BaseModel):
    file_uuid: str
    filename: str
    blob_path: str
    file_size_bytes: int
    file_hash: str
    batch_id: Optional[str] = None
    company_id: str
    uploaded_by: Optional[str] = None


class DuplicateCheckRequest(BaseModel):
    file_hash: str


class DuplicateRecordRequest(BaseModel):
    file_hash: str
    queue_id: str
    file_uuid: str
    filename: str
    company_id: str


@app.post("/api/blob/write")
async def write_blob(request: BlobWriteRequest):
    """Write blob to storage."""
    blob_path = f"/files_blob/{request.filename}"

    blob_store[request.file_uuid] = {
        "file_uuid": request.file_uuid,
        "blob_path": blob_path,
        "created_at": datetime.utcnow().isoformat(),
    }

    return {
        "blob_path": blob_path,
        "file_uuid": request.file_uuid,
        "created_at": datetime.utcnow().isoformat(),
    }


@app.post("/api/blob/register")
async def register_blob(request: BlobRegisterRequest):
    """Register blob metadata."""
    if request.file_uuid in blob_store:
        return {
            "status": "already_exists",
            "file_uuid": request.file_uuid,
            "message": "Blob already registered (idempotent)",
        }

    retention_until = datetime.utcnow() + timedelta(days=7*365)

    blob_store[request.file_uuid] = {
        "file_uuid": request.file_uuid,
        "filename": request.filename,
        "blob_path": request.blob_path,
        "file_size_bytes": request.file_size_bytes,
        "file_hash": request.file_hash,
        "company_id": request.company_id,
        "retention_until": retention_until.isoformat(),
        "status": "uploaded",
    }

    return {
        "status": "registered",
        "file_uuid": request.file_uuid,
        "retention_until": retention_until.isoformat(),
    }


@app.get("/api/daily_usage/check")
async def check_daily_usage(
    company_id: str = Query(...),
    file_count: int = Query(...),
):
    """Check daily usage limit."""
    daily_limit = 500
    current_usage = daily_usage.get(company_id, 0)

    if current_usage + file_count > daily_limit:
        return {
            "status": "limit_exceeded",
            "company_id": company_id,
            "current_usage": current_usage,
            "daily_limit": daily_limit,
            "remaining": max(0, daily_limit - current_usage),
            "resets_at": (datetime.utcnow() + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat() + "Z",
        }

    return {
        "status": "allowed",
        "company_id": company_id,
        "current_usage": current_usage,
        "daily_limit": daily_limit,
        "remaining": daily_limit - current_usage - file_count,
        "resets_at": (datetime.utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat() + "Z",
    }


@app.post("/api/duplicate/check")
async def check_duplicate(request: DuplicateCheckRequest):
    """Check if file hash is a duplicate."""
    if request.file_hash in duplicate_store:
        return {
            "is_duplicate": True,
            "file_hash": request.file_hash,
            "queue_id": f"queue_original_{request.file_hash[:8]}",
            "original_upload_date": datetime.utcnow().isoformat() + "Z",
        }

    return {
        "is_duplicate": False,
        "file_hash": request.file_hash,
    }


@app.post("/api/duplicate/record")
async def record_duplicate(request: DuplicateRecordRequest):
    """Record file hash for future deduplication."""
    duplicate_store.add(request.file_hash)

    return {
        "status": "recorded",
        "file_hash": request.file_hash,
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "heartbeat-mock",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/v1/heartbeat/blob/health")
async def blob_health():
    """Blob service health check."""
    return {
        "status": "healthy",
        "service": "heartbeat-blob",
        "blobs_count": len(blob_store),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
