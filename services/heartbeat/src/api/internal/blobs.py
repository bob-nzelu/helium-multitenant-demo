"""
Blob API Endpoints (Internal Service)

POST /api/blobs/write     — Write file to blob storage (with optional JWT auth)
POST /api/blobs/register  — Register blob metadata (with optional JWT auth)

These match Relay HeartBeatClient's expected URLs exactly.

Auth model:
    - JWT in Authorization header is OPTIONAL (graceful degradation)
    - If present: HeartBeat validates JWT directly (in-process, no HTTP introspect)
      and extracts user_id, tenant_id, role from claims
    - If absent: proceeds without user identity (machine-to-machine HMAC-only flow)
    - SDK identity/trace metadata is passed as a JSON-encoded form field
"""

import json
import logging
from typing import Any, Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...auth.dependencies import get_optional_user_token
from ...handlers import blob_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/blobs", tags=["blobs"])


# ── Request/Response Models ─────────────────────────────────────────────

class BlobRegisterInternalRequest(BaseModel):
    """Register blob metadata (called by Relay after write).

    Canonical dual identity: file_display_id (SDK PK) + blob_uuid (HeartBeat).
    If file_display_id is not provided, HeartBeat generates 'HB-{blob_uuid}'.
    """
    blob_uuid: str = Field(..., min_length=36, max_length=36)
    filename: str = Field(..., min_length=1, max_length=500)
    file_size_bytes: int = Field(..., gt=0)
    file_hash: str = Field(..., min_length=64, max_length=64)
    api_key: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="SDK identity/trace fields: user_trace_id, x_trace_id, "
                    "helium_user_id, float_id, session_id, machine_guid, "
                    "mac_address, computer_name, file_display_id, "
                    "batch_display_id, connection_type, queue_mode",
    )


class BlobWriteResponse(BaseModel):
    blob_uuid: str
    blob_path: str
    file_size_bytes: int
    file_hash: str
    status: str


class BlobRegisterInternalResponse(BaseModel):
    blob_uuid: str
    file_display_id: str
    batch_display_id: str
    status: str
    tracking_id: str


# ── JWT Validation Helper ──────────────────────────────────────────────

async def _validate_jwt_if_present(
    raw_token: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Validate a user JWT and extract claims.

    HeartBeat validates directly (it owns the Ed25519 signing key).
    No HTTP introspect call needed.

    Args:
        raw_token: Raw JWT string from Authorization header, or None.

    Returns:
        JWT claims dict if valid, None if no token or validation fails.
    """
    if raw_token is None:
        return None

    try:
        from ...auth.jwt_manager import get_jwt_manager
        jwt_mgr = get_jwt_manager()
        claims = jwt_mgr.validate_token(raw_token)
        logger.debug(
            f"JWT validated for blob request: user={claims.get('sub')}, "
            f"role={claims.get('role')}"
        )
        return claims
    except Exception as e:
        # JWT validation failed — log but don't reject the request.
        # The request may still be valid (service-to-service with
        # a stale or malformed JWT forwarded from SDK).
        logger.warning(f"JWT validation failed on blob request: {e}")
        return None


def _merge_identity(
    user_claims: Optional[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Merge JWT-derived identity with SDK-provided metadata.

    JWT provides: helium_user_id (sub), session_id (jti), tenant_id, role
    Metadata provides: user_trace_id, x_trace_id, float_id,
                       machine_guid, mac_address, computer_name

    Returns merged identity dict, or None if both sources are empty.
    """
    identity: Dict[str, Any] = {}

    # SDK metadata fields (trace IDs, machine fingerprint, source tracking)
    if metadata:
        for key in (
            "user_trace_id", "x_trace_id", "float_id",
            "machine_guid", "mac_address", "computer_name",
            "helium_user_id", "session_id",
            "source_document_id", "connection_id",
        ):
            if key in metadata:
                identity[key] = metadata[key]

    # JWT-derived fields override metadata (JWT is authoritative for user identity)
    if user_claims:
        identity["helium_user_id"] = user_claims.get("sub")
        identity["session_id"] = user_claims.get("jti")
        # Also store tenant_id and role for audit (not in SDK schema
        # but useful for server-side queries)
        if "tenant_id" in user_claims:
            identity["tenant_id"] = user_claims["tenant_id"]
        if "role" in user_claims:
            identity["role"] = user_claims["role"]

    return identity if identity else None


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post(
    "/write",
    response_model=BlobWriteResponse,
    status_code=status.HTTP_200_OK,
)
async def write_blob(
    blob_uuid: str = Form(...),
    filename: str = Form(...),
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(
        None,
        description="JSON-encoded SDK identity/trace fields",
    ),
    raw_token: Optional[str] = Depends(get_optional_user_token),
):
    """
    Write file to blob storage (filesystem).

    Multipart form upload. Relay sends blob_uuid + filename as form fields
    and file data as the upload.

    Optional:
    - Authorization: Bearer {user_jwt} — HeartBeat validates directly
    - metadata: JSON string with SDK identity/trace fields

    Returns blob metadata including path and hash.
    """
    file_data = await file.read()

    # Validate JWT if present (in-process, no HTTP call)
    user_claims = await _validate_jwt_if_present(raw_token)

    # Parse metadata JSON
    parsed_metadata = None
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Invalid metadata JSON on blob write: {e}")

    # Merge JWT claims + SDK metadata into identity dict
    identity = _merge_identity(user_claims, parsed_metadata)

    try:
        result = await blob_handler.write_blob(
            blob_uuid=blob_uuid,
            filename=filename,
            file_data=file_data,
            identity=identity,
        )
        return result
    except Exception as e:
        logger.error(f"write_blob failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/register",
    response_model=BlobRegisterInternalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_blob(
    request: BlobRegisterInternalRequest,
    raw_token: Optional[str] = Depends(get_optional_user_token),
):
    """
    Register blob metadata in HeartBeat after write.

    Idempotent: returns status "already_registered" if blob_uuid exists.
    Also increments daily usage counter.

    Optional:
    - Authorization: Bearer {user_jwt} — HeartBeat validates directly
    - metadata field in request body with SDK identity/trace fields
    """
    # Validate JWT if present
    user_claims = await _validate_jwt_if_present(raw_token)

    # Merge JWT claims + SDK metadata
    identity = _merge_identity(user_claims, request.metadata)

    # Extract canonical display IDs from metadata (if SDK provided them)
    meta = request.metadata or {}

    try:
        result = await blob_handler.register_blob(
            blob_uuid=request.blob_uuid,
            filename=request.filename,
            file_size_bytes=request.file_size_bytes,
            file_hash=request.file_hash,
            api_key=request.api_key,
            identity=identity,
            file_display_id=meta.get("file_display_id"),
            batch_display_id=meta.get("batch_display_id"),
            connection_type=meta.get("connection_type"),
            queue_mode=meta.get("queue_mode"),
        )

        # If already registered, return 200 instead of 201
        if result.get("status") == "already_registered":
            return result  # FastAPI will still use 201 but that's fine for idempotency

        return result

    except Exception as e:
        logger.error(f"register_blob failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get("/{blob_uuid}/download")
async def download_blob(
    blob_uuid: str,
    raw_token: Optional[str] = Depends(get_optional_user_token),
):
    """
    Download blob file bytes by UUID.

    Returns raw bytes with Content-Type, Content-Disposition, and hash headers.
    Used by Core service to fetch files for processing.
    Also records the download in blob_downloads audit table.
    """
    import time as _time

    from ...clients.filesystem_client import get_filesystem_client
    from ...database import get_blob_database

    db = get_blob_database()
    blob = db.get_blob(blob_uuid)
    if not blob:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "blob_uuid": blob_uuid, "message": "Blob not found"},
        )

    if blob.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"status": "error", "blob_uuid": blob_uuid, "message": "Blob is in error state"},
        )

    blob_path = blob.get("blob_path", "")
    if not blob_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "blob_uuid": blob_uuid, "message": "Blob has no storage path"},
        )

    fs = get_filesystem_client()
    start_ms = int(_time.time() * 1000)
    try:
        file_bytes = await fs.get_blob(blob_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "blob_uuid": blob_uuid, "message": "Blob file not found on disk"},
        )
    duration_ms = int(_time.time() * 1000) - start_ms

    content_type = blob.get("content_type", "application/octet-stream")
    original_filename = blob.get("original_filename", blob.get("filename", "download"))
    file_hash = blob.get("file_hash", "")
    file_display_id = blob.get("file_display_id", f"HB-{blob_uuid}")

    # Record download in blob_downloads audit table (non-blocking)
    user_claims = await _validate_jwt_if_present(raw_token)
    downloaded_by = (user_claims or {}).get("sub", "anonymous")
    try:
        db.record_download(
            blob_uuid=blob_uuid,
            file_display_id=file_display_id,
            downloaded_by=downloaded_by,
            download_source="heartbeat_api",
            file_size_bytes=len(file_bytes),
            download_duration_ms=duration_ms,
            session_id=(user_claims or {}).get("jti"),
            float_id=None,  # Not available on server-side download
        )
    except Exception as e:
        logger.warning(f"Failed to record download (non-critical): {e}")

    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{original_filename}"',
            "Content-Length": str(len(file_bytes)),
            "X-Blob-UUID": blob_uuid,
            "X-File-Display-ID": file_display_id,
            "X-Blob-Hash": file_hash,
        },
    )
