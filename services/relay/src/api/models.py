"""
API Pydantic Models

Request/response schemas for Relay-API endpoints.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Ingest Response ──────────────────────────────────────────────────────


class IngestResponse(BaseModel):
    """Response from POST /api/ingest.

    Identity model:
        data_uuid  — Per-request group identifier (always present, even single file)
        file_uuids — Per-file storage identifiers (one per uploaded file)
        trace_id   — x_trace_id for log correlation across services

    Internal mapping: data_uuid = HeartBeat batch_uuid, file_uuids = HeartBeat blob_uuids
    """

    status: str = Field(description="Result status: processed | queued | error")
    data_uuid: str = Field(description="Per-request group identifier (always present)")
    queue_id: str = Field(description="Core processing queue ID")
    filenames: List[str] = Field(description="Uploaded filenames")
    file_count: int = Field(description="Number of files in request")
    file_hash: str = Field(description="SHA256 hash of primary file (backward compat)")
    trace_id: str = Field(default="", description="x_trace_id for log correlation")

    # Per-file identifiers and hashes
    file_uuids: List[str] = Field(
        description="Per-file storage identifiers (one per uploaded file)",
    )
    file_hashes: Optional[List[str]] = Field(
        default=None,
        description="Per-file SHA256 hashes (one per uploaded file)",
    )

    # Bulk flow fields (present when status=processed)
    preview_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Invoice preview from Core (bulk flow only)",
    )

    # External flow fields (present when status=processed, external)
    irn: Optional[str] = Field(
        default=None,
        description="Invoice Reference Number (external flow only)",
    )
    qr_code: Optional[str] = Field(
        default=None,
        description="QR code data, base64 (external flow only)",
    )


# ── Error Response ───────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response shape."""

    status: str = "error"
    error_code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable error message")
    details: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Additional error details",
    )


# ── Internal Endpoints ───────────────────────────────────────────────────


class RefreshCacheResponse(BaseModel):
    """Response from POST /internal/refresh-cache."""

    status: str = "ok"
    modules_updated: List[str] = Field(
        default_factory=list,
        description="Module names that were updated",
    )
    keys_updated: bool = Field(
        default=False,
        description="Whether FIRS service keys were updated",
    )


# ── Health Check ────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str = Field(description="Service health: healthy | degraded")
    instance_id: str = Field(description="Relay instance identifier")
    relay_type: str = Field(default="bulk", description="Relay service type")
    version: str = Field(description="Relay API version")
    services: Dict[str, str] = Field(
        description="Downstream service statuses",
    )
    timestamp: str = Field(description="ISO 8601 UTC timestamp")
    message: Optional[str] = Field(
        default=None,
        description="Degradation reason (only when status=degraded)",
    )
