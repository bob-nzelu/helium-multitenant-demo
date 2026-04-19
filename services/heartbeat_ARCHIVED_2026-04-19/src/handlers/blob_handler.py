"""
Blob Handler — Business logic for blob write and registration.

Called by api/internal/blobs.py router.
Writes to filesystem blob storage and records in blob.db (Primary mode).

Canonical blob schema v1.4.0 alignment:
    - file_entries table (was blob_entries)
    - Dual identity: file_display_id (SDK PK) + blob_uuid (HeartBeat canonical)
    - batch_display_id (SDK PK) + batch_uuid (HeartBeat canonical)
    - Full identity/trace columns per HELIUM_SECURITY_SPEC
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from uuid6 import uuid7

from ..config import get_config
from ..database import get_blob_database
from ..errors import StorageError

logger = logging.getLogger(__name__)


# ── Identity field names (SDK schema v5.1 harmonization) ────────────────

IDENTITY_FIELDS = (
    "user_trace_id",
    "x_trace_id",
    "helium_user_id",
    "float_id",
    "session_id",
    "machine_guid",
    "mac_address",
    "computer_name",
)


async def write_blob(
    blob_uuid: str,
    filename: str,
    file_data: bytes,
    identity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Write file data to filesystem blob storage.

    Matches Relay HeartBeatClient.write_blob() contract:
        Returns: {blob_uuid, blob_path, file_size_bytes, file_hash, status: "uploaded"}

    Args:
        blob_uuid: Unique blob identifier.
        filename: Original filename.
        file_data: Raw file bytes.
        identity: Merged JWT + SDK identity fields (optional).
    """
    from ..clients.filesystem_client import get_filesystem_client

    file_hash = hashlib.sha256(file_data).hexdigest()
    object_name = f"files_blob/{blob_uuid}-{filename}"
    blob_path = f"/{object_name}"

    # Write to filesystem blob storage
    try:
        fs = get_filesystem_client()
        if fs is None:
            raise RuntimeError("Filesystem blob client not initialized")
        await fs.put_blob(
            object_name=object_name,
            data=file_data,
            content_type=_guess_content_type(filename),
        )
    except Exception as e:
        logger.error(f"Blob storage write failed for {blob_uuid}: {e}")
        raise StorageError(
            message=f"Failed to write blob to storage: {e}",
            details=[{"blob_uuid": blob_uuid, "filename": filename}],
        )

    # Log with identity info if available
    user_id = identity.get("helium_user_id", "anonymous") if identity else "anonymous"
    logger.info(
        f"Blob written: uuid={blob_uuid}, path={blob_path}, "
        f"size={len(file_data)}, hash={file_hash[:12]}..., user={user_id}"
    )

    return {
        "blob_uuid": blob_uuid,
        "blob_path": blob_path,
        "file_size_bytes": len(file_data),
        "file_hash": file_hash,
        "status": "uploaded",
    }


async def register_blob(
    blob_uuid: str,
    filename: str,
    file_size_bytes: int,
    file_hash: str,
    api_key: str,
    identity: Optional[Dict[str, Any]] = None,
    file_display_id: Optional[str] = None,
    batch_display_id: Optional[str] = None,
    connection_type: Optional[str] = None,
    queue_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Register blob metadata in file_entries + increment daily usage.

    Canonical dual identity: file_display_id (SDK PK) + blob_uuid (HeartBeat).
    If file_display_id is not provided (e.g. server-originated registration),
    a synthetic 'HB-{blob_uuid}' is generated.

    Matches Relay HeartBeatClient.register_blob() contract:
        Returns: {blob_uuid, file_display_id, status: "registered", tracking_id: str}
        On conflict: {blob_uuid, file_display_id, status: "already_registered", tracking_id: str}

    Args:
        blob_uuid: HeartBeat-generated blob UUID.
        filename: Original filename.
        file_size_bytes: File size in bytes.
        file_hash: SHA256 hash of file content.
        api_key: API key used for the upload.
        identity: Merged JWT + SDK identity fields (optional).
        file_display_id: SDK-generated display ID (optional).
        batch_display_id: SDK-generated batch display ID (optional).
        connection_type: Connection type (manual, nas, erp, api, email).
        queue_mode: Queue mode (bulk, api, polling, watcher, dbc, email).
    """
    db = get_blob_database()
    config = get_config()

    now = datetime.now(timezone.utc)
    uploaded_at_unix = int(now.timestamp())
    uploaded_at_iso = now.isoformat()
    retention_until = now + timedelta(days=365 * config.retention_years)
    retention_until_unix = int(retention_until.timestamp())
    retention_until_iso = retention_until.isoformat()

    blob_path = f"/files_blob/{blob_uuid}-{filename}"
    tracking_id = f"track_{uuid7()}"

    # Canonical display ID fallback
    effective_display_id = file_display_id or f"HB-{blob_uuid}"

    # Check if already registered (idempotent)
    existing = db.get_blob(blob_uuid)
    if existing:
        logger.info(f"Blob already registered: {blob_uuid}")
        return {
            "blob_uuid": blob_uuid,
            "file_display_id": existing.get("file_display_id", effective_display_id),
            "batch_display_id": existing.get("batch_display_id", batch_display_id or f"HBB-{blob_uuid}"),
            "status": "already_registered",
            "tracking_id": tracking_id,
        }

    # Determine source from api_key (simplified)
    source = api_key if api_key else "unknown"

    # Register in file_entries (canonical schema)
    try:
        db.register_blob(
            blob_uuid=blob_uuid,
            blob_path=blob_path,
            file_size_bytes=file_size_bytes,
            file_hash=file_hash,
            content_type=_guess_content_type(filename),
            source=source,
            uploaded_at_unix=uploaded_at_unix,
            uploaded_at_iso=uploaded_at_iso,
            retention_until_unix=retention_until_unix,
            retention_until_iso=retention_until_iso,
            identity=identity,
            file_display_id=file_display_id,
            batch_display_id=batch_display_id,
            original_filename=filename,
            connection_type=connection_type,
            queue_mode=queue_mode,
        )
    except Exception as e:
        # IntegrityError = already registered (race condition — still idempotent)
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError):
            logger.info(f"Blob already registered (race): {blob_uuid}")
            return {
                "blob_uuid": blob_uuid,
                "file_display_id": effective_display_id,
                "batch_display_id": batch_display_id or f"HBB-{blob_uuid}",
                "status": "already_registered",
                "tracking_id": tracking_id,
            }
        raise

    # Increment daily usage (non-critical — don't fail registration)
    try:
        db.increment_daily_usage(
            company_id=source,
            file_count=1,
            size_bytes=file_size_bytes,
            daily_limit=config.default_daily_limit,
        )
    except Exception as e:
        logger.warning(f"Failed to increment daily usage (non-critical): {e}")

    user_id = identity.get("helium_user_id", "anonymous") if identity else "anonymous"
    logger.info(
        f"Blob registered: uuid={blob_uuid}, display_id={effective_display_id}, "
        f"tracking={tracking_id}, user={user_id}"
    )

    result = {
        "blob_uuid": blob_uuid,
        "file_display_id": effective_display_id,
        "batch_display_id": batch_display_id or f"HBB-{blob_uuid}",
        "status": "registered",
        "tracking_id": tracking_id,
    }

    # Publish blob.uploaded SSE event with canonical dual identity
    sse_data = {
        "blob_uuid": blob_uuid,
        "file_display_id": effective_display_id,
        "batch_display_id": batch_display_id or f"HBB-{blob_uuid}",
        "file_hash": file_hash,
        "original_filename": filename,
        "file_size_bytes": file_size_bytes,
        "status": "uploaded",
        "source": source,
    }
    try:
        from ..events import get_event_bus
        bus = get_event_bus()
        await bus.publish("blob.uploaded", sse_data)
    except Exception as e:
        logger.warning(f"P2-D publish blob.uploaded failed (non-critical): {e}")

    # SSE Spec: publish to authenticated stream + event ledger
    try:
        from ..sse.publish import publish_event
        await publish_event("blob.uploaded", sse_data, data_uuid=blob_uuid)
    except Exception as e:
        logger.warning(f"SSE ledger publish blob.uploaded failed (non-critical): {e}")

    return result


def _guess_content_type(filename: str) -> str:
    """Guess MIME type from filename extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "json": "application/json",
        "xml": "application/xml",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "zip": "application/zip",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")
