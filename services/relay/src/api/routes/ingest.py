"""
POST /api/ingest

Accepts a batch of fee transactions as a JSON array uploaded as a single file.
One call per 10-minute cycle. Returns per-record IRN + QR results.

Multipart payload:
    files        — one .json file containing a JSON array of transaction records
    batch_id     — identifier for this batch (one per 10-minute cycle)
    call_type    — always "external" for AB MFB

Headers:
    X-API-Key    — API key
    X-Timestamp  — UTC timestamp (ISO 8601)
    X-Signature  — HMAC-SHA256 signature
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ..deps import authenticate_request
from ...core.tenant import TenantConfig
from ...services.external import ExternalService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/ingest",
    summary="Submit a batch of fee transactions",
    responses={
        200: {"description": "All records processed successfully — no duplicates, no failures"},
        207: {"description": "Batch processed — contains duplicates or failures; see arrays"},
        422: {"description": "All records rejected — nothing processed; see duplicates + failed arrays"},
        400: {"description": "Invalid JSON, missing batch_id, or malformed request"},
        401: {"description": "Authentication failed"},
        429: {"description": "Demo daily quota reached"},
    },
)
async def ingest(
    request: Request,
    files: list[UploadFile] = File(..., description="One .json file containing a JSON array of records"),
    batch_id: str = Form(..., description="Batch identifier for this 10-minute cycle"),
    call_type: str = Form(default="external", description="Always 'external'"),
    source: str = Form(default="", description="Source identifier: 'dashboard' for test console, omit for API"),
    tenant: TenantConfig = Depends(authenticate_request),
):
    """
    Submit a batch of fee transactions.

    Upload one JSON file containing an array of records (can be empty array, a
    single object, or multiple objects). Include a batch_id that groups all
    records from the same processing cycle.

    Returns HTTP 200 when all records processed cleanly.
    Returns HTTP 207 when the batch contains any duplicates or failures.
    Either way, three arrays are present: processed (IRN + QR), duplicates, failed.
    """
    trace_id = getattr(request.state, "trace_id", "")

    if not files:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error_code": "VALIDATION_FAILED", "message": "No file provided"},
        )

    filename  = files[0].filename or "batch.json"
    file_data = await files[0].read()

    logger.info(
        f"[{trace_id}] POST /api/ingest — batch_id={batch_id} "
        f"file={filename} size={len(file_data)}b"
    )

    external_svc: ExternalService = request.app.state.external_service

    # Determine source
    if source.lower() == "dashboard":
        src_name = "Dashboard"
        src_id   = "internal-test-console"
    else:
        src_name = "Demo API"
        src_id   = tenant.api_key

    try:
        result = await external_svc.process_batch(
            batch_file=(filename, file_data),
            batch_id=batch_id,
            tenant=tenant,
            trace_id=trace_id,
            source=src_name,
            source_id=src_id,
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error_code": "VALIDATION_FAILED", "message": str(e)},
        )

    return JSONResponse(status_code=result.http_status, content=result.to_dict())
