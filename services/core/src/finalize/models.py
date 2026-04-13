"""
WS5 Pydantic v2 models — Finalize, Retry, B2B, Edge callback.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Corrections (HIS Intelligence Feedback) ─────────────────────────────


class CorrectionEntry(BaseModel):
    """A single human-corrected field to feed back to HIS.

    SDK populates this array when the user edits fields in HLX preview.
    Core forwards these to HIS to improve future enrichment.
    """

    entity_type: str = Field(
        ..., description="invoice, customer, or inventory"
    )
    entity_id: str | None = Field(
        default=None, description="Entity ID (may be None for provisional records)"
    )
    field: str = Field(
        ..., description="Field that was corrected (e.g. hsn_code, postal_code, lga)"
    )
    old_value: str | None = Field(
        default=None, description="Value before correction"
    )
    new_value: str | None = Field(
        default=None, description="Corrected value"
    )
    source: str = Field(
        default="hlx_review",
        description="hlx_review (human edit), api_finalize (auto), hlx_no_change (implicit validation)",
    )


# ── Confidence weights for HIS feedback ──────────────────────────────────

CONFIDENCE_WEIGHTS: dict[str, float] = {
    "hlx_review": 0.95,      # Human actively changed a field via HLX
    "hlx_no_change": 0.70,   # Human reviewed but accepted as-is (implicit validation)
    "api_finalize": 0.40,    # API submission, no human review
}


# ── Finalize ─────────────────────────────────────────────────────────────


class FinalizeRequest(BaseModel):
    """POST /api/v1/finalize — receive finalized .hlm from Float SDK."""

    queue_id: str = Field(..., min_length=1)
    data_uuid: str = Field(..., min_length=1)
    hlx_id: str = Field(..., min_length=1, description="Stable HLX document ID")
    hlm_data: dict = Field(..., description="Complete .hlm file as parsed JSON")
    is_refinalize: bool = Field(
        default=False,
        description="True if re-finalizing failed invoices from an existing HLX",
    )
    corrections: list[CorrectionEntry] | None = Field(
        default=None,
        description="Human corrections from HLX review to feed back to HIS",
    )


class FinalizeStatistics(BaseModel):
    """Statistics returned after finalization."""

    invoices_created: int = 0
    customers_created: int = 0
    customers_updated: int = 0
    inventory_created: int = 0
    inventory_updated: int = 0
    queued_to_edge: int = 0
    finalization_time_ms: int = 0


class FinalizeResponse(BaseModel):
    """POST /api/v1/finalize response."""

    queue_id: str
    data_uuid: str
    hlx_id: str
    status: str = "finalized"
    statistics: FinalizeStatistics
    irn_list: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── Retry / Retransmit ───────────────────────────────────────────────────


class RetryRequest(BaseModel):
    """POST /api/v1/retry — full-cycle retry (SIGN_AND_TRANSMIT)."""

    invoice_id: str = Field(..., min_length=1)


class RetransmitRequest(BaseModel):
    """POST /api/v1/retransmit — exchange-only retry (TRANSMIT)."""

    invoice_id: str = Field(..., min_length=1)


class RetryResponse(BaseModel):
    """Response for retry and retransmit."""

    invoice_id: str
    status: str  # "queued"
    task_type: str  # "SIGN_AND_TRANSMIT" or "TRANSMIT"
    retry_count: int
    last_retry_at: str | None = None


# ── B2B Accept / Reject ──────────────────────────────────────────────────


class AcceptRequest(BaseModel):
    """POST /api/v1/invoice/{id}/accept."""

    action_reason: str | None = Field(
        default=None, description="Optional reason for acceptance"
    )


class AcceptResponse(BaseModel):
    """Accept response."""

    invoice_id: str
    inbound_status: str = "ACCEPTED"
    inbound_action_at: str
    inbound_action_reason: str | None = None
    within_72h_window: bool


class RejectRequest(BaseModel):
    """POST /api/v1/invoice/{id}/reject."""

    action_reason: str = Field(
        ..., min_length=10, description="Required reason (min 10 chars)"
    )


class RejectResponse(BaseModel):
    """Reject response."""

    invoice_id: str
    inbound_status: str = "REJECTED"
    inbound_action_at: str
    inbound_action_reason: str
    within_72h_window: bool


# ── Edge Callback ────────────────────────────────────────────────────────


class TransmissionResultData(BaseModel):
    """Data for update_type='transmission_result'."""

    transmission_status: str
    firs_confirmation: str | None = None
    firs_response_data: str | None = None
    acknowledgement_date: str | None = None
    error_message: str | None = None


class SigningResultData(BaseModel):
    """Data for update_type='signing_result'."""

    csid: str
    csid_status: str
    sign_date: str


class PrecheckResultData(BaseModel):
    """Data for update_type='precheck_result'."""

    transmission_status: str  # PRECHECK_PASSED or BLOCKED_COUNTERPARTY


class StatusUpdateData(BaseModel):
    """Data for update_type='status_update'."""

    workflow_status: str | None = None
    transmission_status: str | None = None
    transmission_status_error: str | None = None


class EdgeUpdateRequest(BaseModel):
    """POST /api/v1/update — callback from Edge service."""

    invoice_id: str = Field(..., min_length=1)
    update_type: str = Field(
        ...,
        description=(
            "One of: transmission_result, signing_result, "
            "precheck_result, status_update"
        ),
    )
    data: dict = Field(..., description="Update-type-specific payload")


class EdgeUpdateResponse(BaseModel):
    """POST /api/v1/update response."""

    invoice_id: str
    status: str = "updated"
    fields_updated: list[str] = Field(default_factory=list)
