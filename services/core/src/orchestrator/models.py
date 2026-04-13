"""WS3: Orchestrator Pydantic models — request/response for POST /api/v1/process_preview."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class ProcessPreviewRequest(BaseModel):
    """Request body for POST /api/v1/process_preview."""

    queue_id: str = Field(..., description="Core queue entry ID from /enqueue response")
    data_uuid: str = Field(..., description="Relay per-request group identifier (UUIDv7)")


# ---------------------------------------------------------------------------
# Nested response models
# ---------------------------------------------------------------------------

class StatisticsModel(BaseModel):
    """Processing statistics block."""

    total_invoices: int = 0
    valid_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    processing_time_ms: int = 0
    confidence: float = 0.0
    batch_count: int = 0
    worker_count: int = 0
    customer_count: int = 0
    new_customer_count: int = 0
    product_count: int = 0
    new_product_count: int = 0


class RedFlagModel(BaseModel):
    """A single red flag entry."""

    type: str
    severity: str  # "error" | "warning" | "info"
    message: str
    invoice_index: int | None = None
    invoice_number: str | None = None
    field: str | None = None
    phase: str | None = None
    suggestion: str | None = None


class ProgressModel(BaseModel):
    """Live progress counters."""

    invoices_ready: int = 0
    invoices_total: int = 0


# ---------------------------------------------------------------------------
# 200 OK response
# ---------------------------------------------------------------------------

class ProcessPreviewResponse200(BaseModel):
    """Response when pipeline completes within timeout (200 OK)."""

    queue_id: str
    data_uuid: str
    status: str  # "preview_ready" | "finalized"
    statistics: StatisticsModel
    red_flags: list[RedFlagModel] = []
    hlx_blob_uuid: str | None = None  # None on immediate-finalize path


# ---------------------------------------------------------------------------
# 202 Accepted response
# ---------------------------------------------------------------------------

class ProcessPreviewResponse202(BaseModel):
    """Response when soft timeout reached (202 Accepted)."""

    queue_id: str
    data_uuid: str
    status: str = "processing"
    message: str = "Processing in progress. Monitor via SSE or poll status endpoint."
    estimated_completion_seconds: int = 300
    phases_completed: int = 0
    phases_total: int = 7
    current_phase: str = ""
    progress: ProgressModel = Field(default_factory=ProgressModel)


# ---------------------------------------------------------------------------
# Error response
# ---------------------------------------------------------------------------

class OrchestratorErrorResponse(BaseModel):
    """Standard error response for orchestrator errors."""

    error_code: str  # ORCH_001 through ORCH_008
    message: str
    details: dict | None = None
