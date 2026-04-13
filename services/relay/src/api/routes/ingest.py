"""
POST /api/ingest — The ONE business endpoint.

Accepts file uploads from both Float (bulk) and external API callers.
Routes to BulkService or ExternalService based on call_type.

Multipart payload:
    files         — 1-3 invoice files (binary)
    call_type     — "bulk" (default) or "external"
    metadata      — JSON string with SDK identity/trace fields (optional):
                    {user_trace_id, helium_user_id, float_id, session_id,
                     machine_guid, mac_address, computer_name}
    invoice_data_json — invoice metadata (external flow only)

Headers:
    X-API-Key     — HMAC client key (Relay auth)
    X-Timestamp   — request timestamp
    X-Signature   — HMAC-SHA256 signature
    Authorization — Bearer JWT (optional, forwarded to HeartBeat/Core)
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from ..deps import authenticate_request, decrypt_body_if_needed
from ..models import IngestResponse
from ...services.bulk import BulkService
from ...services.external import ExternalService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/api/ingest",
    response_model=IngestResponse,
    summary="Ingest files for processing",
    responses={
        400: {"description": "Validation failed / Malware detected"},
        401: {"description": "Authentication failed"},
        403: {"description": "Encryption required"},
        409: {"description": "Duplicate file"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Module not loaded (external flow)"},
    },
)
async def ingest(
    request: Request,
    files: list[UploadFile] = File(..., description="Invoice files (max 3, allowed: .pdf .xml .json .csv .xlsx)"),
    call_type: str = Form(default="bulk", description="Flow type: 'bulk' (Float UI) or 'external' (API callers)"),
    metadata: Optional[str] = Form(default=None, description="JSON string with SDK identity/trace fields"),
    invoice_data_json: Optional[str] = Form(default=None, description="JSON string with invoice metadata (external flow only)"),
    api_key: str = Depends(authenticate_request),
):
    """
    Ingest files for invoice processing.

    **call_type=bulk** (Float desktop):
        Runs ingestion → waits for Core preview (up to 5 min).
        Returns preview_data if processed, or status="queued".

    **call_type=external** (API callers):
        Runs ingestion → fire-and-forget Core → generates IRN+QR.
        Returns irn + qr_code immediately.

    **metadata** (optional, JSON string):
        SDK identity/trace fields forwarded to HeartBeat for file_entries records.
        Identity keys: user_trace_id, helium_user_id, float_id, session_id,
                       machine_guid, mac_address, computer_name.
        Canonical display IDs: batch_display_id, file_display_ids[] (per-file).
        Relay auto-injects: queue_mode, connection_type, source_document_id.

    **Authorization header** (optional):
        Bearer JWT forwarded to HeartBeat/Core for user identity verification.
        Relay authenticates via HMAC (X-API-Key/X-Signature), not JWT.
    """
    trace_id = getattr(request.state, "trace_id", "")

    # Parse SDK metadata (identity + trace fields)
    meta: dict = {}
    if metadata:
        try:
            meta = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[{trace_id}] Invalid metadata JSON — ignoring")

    # Extract JWT from Authorization header (Relay forwards, does NOT validate)
    jwt_token: Optional[str] = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        jwt_token = auth_header[7:].strip()

    # Map call_type to canonical queue_mode for HeartBeat blob schema
    # bulk → "bulk", external → "api"
    if "queue_mode" not in meta:
        meta["queue_mode"] = "api" if call_type == "external" else "bulk"
    if "connection_type" not in meta:
        meta["connection_type"] = "api" if call_type == "external" else "manual"

    if meta:
        logger.info(
            f"[{trace_id}] POST /api/ingest — "
            f"call_type={call_type}, files={len(files)}, "
            f"user_trace_id={meta.get('user_trace_id', 'none')}, "
            f"helium_user_id={meta.get('helium_user_id', 'none')}, "
            f"jwt={'yes' if jwt_token else 'no'}"
        )
    else:
        logger.info(
            f"[{trace_id}] POST /api/ingest — "
            f"call_type={call_type}, files={len(files)}"
        )

    # Read uploaded files into (filename, bytes) tuples
    file_tuples = []
    for f in files:
        data = await f.read()
        file_tuples.append((f.filename or "unknown", data))

    if call_type == "external":
        # External flow: ingest → IRN/QR
        external_svc: ExternalService = request.app.state.external_service
        inv_data = {}
        if invoice_data_json:
            try:
                inv_data = json.loads(invoice_data_json)
            except json.JSONDecodeError:
                pass

        result = await external_svc.process(
            files=file_tuples,
            api_key=api_key,
            trace_id=trace_id,
            invoice_data=inv_data,
            metadata=meta,
            jwt_token=jwt_token,
        )
        return IngestResponse(
            status=result.status,
            data_uuid=result.ingest.data_uuid,
            queue_id=result.ingest.queue_id,
            filenames=result.ingest.filenames,
            file_count=result.ingest.file_count,
            file_hash=result.ingest.file_hash,
            file_uuids=result.ingest.blob_uuids,
            file_hashes=result.ingest.file_hashes,
            trace_id=trace_id,
            irn=result.irn,
            qr_code=result.qr_code,
        )

    else:
        # Bulk flow (default): ingest → preview
        bulk_svc: BulkService = request.app.state.bulk_service
        result = await bulk_svc.process(
            files=file_tuples,
            api_key=api_key,
            trace_id=trace_id,
            metadata=meta,
            jwt_token=jwt_token,
        )
        return IngestResponse(
            status=result.status,
            data_uuid=result.ingest.data_uuid,
            queue_id=result.ingest.queue_id,
            filenames=result.ingest.filenames,
            file_count=result.ingest.file_count,
            file_hash=result.ingest.file_hash,
            file_uuids=result.ingest.blob_uuids,
            file_hashes=result.ingest.file_hashes,
            trace_id=trace_id,
            preview_data=result.preview_data,
        )
