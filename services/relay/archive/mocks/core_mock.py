"""
Mock Core API Server for Local Development

Simulates Core API responses for Relay testing.
Used by docker-compose for local development.

Endpoints:
- POST /api/v1/core/enqueue
- POST /api/v1/core/process_preview
- POST /api/v1/core/finalize
- GET /api/v1/core/status/{queue_id}
- GET /health
"""

import uuid
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

app = FastAPI(title="Core API Mock", version="1.0.0")

# In-memory store for queue entries
queue_store: Dict[str, Dict[str, Any]] = {}


class EnqueueRequest(BaseModel):
    file_uuid: str
    blob_path: str
    original_filename: str
    source: str
    immediate_processing: bool = False


class FinalizeRequest(BaseModel):
    queue_id: str
    edits: Optional[Dict[str, Any]] = None


@app.post("/api/v1/core/enqueue")
async def enqueue(request: EnqueueRequest):
    """Enqueue a file for processing."""
    queue_id = f"queue_{uuid.uuid4().hex[:8]}"

    queue_store[queue_id] = {
        "queue_id": queue_id,
        "file_uuid": request.file_uuid,
        "blob_path": request.blob_path,
        "original_filename": request.original_filename,
        "source": request.source,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
    }

    return {"queue_id": queue_id, "status": "queued"}


@app.post("/api/v1/core/process_preview")
async def process_preview(queue_id: str):
    """Process file and return preview data."""
    # Simulate processing delay
    time.sleep(0.5)

    if queue_id not in queue_store:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    queue_store[queue_id]["status"] = "completed"

    return {
        "status": "completed",
        "queue_id": queue_id,
        "statistics": {
            "invoices_processed": 1,
            "duplicates_detected": 0,
            "invoices_failed": 0,
            "red_flags": [],
        },
        "preview_data": {
            "invoices": [
                {
                    "invoice_number": "INV-001",
                    "vendor": "Mock Vendor",
                    "amount": 1000.00,
                    "currency": "NGN",
                    "date": "2026-01-31",
                }
            ]
        }
    }


@app.post("/api/v1/core/finalize")
async def finalize(request: FinalizeRequest):
    """Finalize processed invoice with optional edits."""
    if request.queue_id not in queue_store:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    queue_store[request.queue_id]["status"] = "finalized"

    return {
        "status": "finalized",
        "queue_id": request.queue_id,
        "invoice_ids": [f"inv_{uuid.uuid4().hex[:8]}"],
        "message": "Invoice finalized successfully",
    }


@app.get("/api/v1/core/status/{queue_id}")
async def get_status(queue_id: str):
    """Get processing status for a queue entry."""
    if queue_id not in queue_store:
        return {
            "status": "not_found",
            "queue_id": queue_id,
            "message": "Queue entry not found",
        }

    entry = queue_store[queue_id]
    return {
        "status": entry["status"],
        "queue_id": queue_id,
        "preview_available": entry["status"] == "completed",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "core-mock",
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
