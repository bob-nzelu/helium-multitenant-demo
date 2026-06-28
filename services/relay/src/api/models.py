"""
API Pydantic Models

Request/response schemas for Relay-API endpoints.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Q37 External Batch Ingest (PRONALYTICS_MIDDLEWARE_API §3) ─────────────────


class BatchProcessedEntry(BaseModel):
    """One successfully fiscalized invoice in a batch ingest response.

    L31 (monetary precision): money is ``Decimal``, never ``float`` — it
    serializes to a JSON string (e.g. ``"7.50"``) so no float imprecision is
    reintroduced on the wire.

    VAT (Bob ratification 2026-06-19): production Relay does NO VAT math. A
    caller-supplied ``vat_amount`` is echoed verbatim; absent → ``null`` (VAT is
    computed by Core/Transforma downstream, surfaced via the status/invoice).
    """

    transaction_id: str = Field(description="ERP's unique reference, echoed")
    irn: str = Field(description="Invoice Reference Number (FIRS-recognised)")
    qr_code: str = Field(description="QR code for this invoice, Base64-encoded")
    data_uuid: str = Field(description="Relay internal storage handle")
    fee_amount: Decimal = Field(description="Echoed invoice amount in NGN (Decimal, JSON string)")
    vat_amount: Optional[Decimal] = Field(
        default=None,
        description="Caller-supplied VAT echoed verbatim (Decimal, JSON string); null if not supplied — Relay does no VAT math",
    )


class BatchDuplicateEntry(BaseModel):
    """A record already seen in a prior batch — not re-fiscalized."""

    transaction_id: str
    message: str = "Already received in a previous batch"
    duplicate_of: Optional[Dict[str, str]] = Field(
        default=None,
        description="Original IRN, data_uuid, and batch_id of the first submission",
    )


class BatchFailedEntry(BaseModel):
    """A record that could not be processed."""

    transaction_id: str
    error: str = Field(description="Human-readable failure reason")
    error_code: Optional[str] = Field(default=None, description="Machine-readable code")


class BatchSummary(BaseModel):
    total: int
    processed: int
    duplicates: int
    failed: int


class BatchIngestResponse(BaseModel):
    """Response from POST /api/ingest when processing a JSON array of ERP records.

    Replaces the single-document IngestResponse for the external batch flow.
    status: 'ok' (all processed) | 'partial' (some duplicates/failures) | 'rejected' (none processed).
    """

    status: str = Field(description="ok | partial | rejected")
    batch_id: str = Field(description="Echo of the caller-supplied batch_id")
    trace_id: str = Field(default="", description="Server correlation ID")
    summary: BatchSummary
    processed: List[BatchProcessedEntry] = Field(default_factory=list)
    duplicates: List[BatchDuplicateEntry] = Field(default_factory=list)
    failed: List[BatchFailedEntry] = Field(default_factory=list)


# ── Q37 Status (PRONALYTICS_MIDDLEWARE_API §4) ────────────────────────────────


class StatusRequest(BaseModel):
    """Request body for POST /api/status. Exactly ONE selector must be provided.

    L30 (DATA_MODEL_CANONICAL §6): the external status surface is queryable by
    ``transaction_id`` / ``batch_id`` / ``invoice_number`` / ``irn``. HeartBeat
    answers the pre-invoice phase (``transaction_id`` / ``batch_id`` against
    ``blob.file_transactions``); Core answers the invoice-level phase and is the
    ONLY resolver of ``invoice_number`` / ``irn`` (HB stays blind to invoice
    semantics, §8).
    """

    transaction_id: Optional[str] = Field(default=None, description="ERP transaction reference (HB + Core)")
    batch_id: Optional[str] = Field(default=None, description="Batch submission identifier (HB + Core)")
    invoice_number: Optional[str] = Field(default=None, description="Tenant invoice number (Core only)")
    irn: Optional[str] = Field(default=None, description="Invoice Reference Number (Core only)")


class StatusEntry(BaseModel):
    """One transaction/invoice in a status response (L30 / L29 shape).

    ``result`` is the merged external-surface outcome. It reconciles the
    "3-way file-status vocab collision" flagged on L29 by mapping the
    Tier-3 ``file_transactions.status`` (HB) and the Tier-4 invoice state
    (Core) onto a single ERP-facing vocabulary:

      | result             | source condition                                          |
      |--------------------|-----------------------------------------------------------|
      | ``pending``        | HB ``file_transactions.status='pending'`` (seeded, not yet extracted) |
      | ``processed``      | HB ``acknowledged`` AND a Core invoice exists (IRN minted) |
      | ``not_an_invoice`` | HB ``file_transactions.status='not_an_invoice'`` (classified out) |
      | ``duplicate``      | dedup hit at ingest                                       |
      | ``failed``         | HB file/txn error OR Core ``workflow_status='ERROR'``      |

    ``not_an_invoice`` is ADDITIVE to L29's original 4-value set — surfaced
    because it is a real terminal an ERP must see (no IRN will ever come).
    Flagged for ARCH/Bob ratification of the L29 vocab extension.
    """

    transaction_id: Optional[str] = None
    irn: Optional[str] = None
    batch_id: Optional[str] = None
    invoice_number: Optional[str] = Field(
        default=None,
        description="Tenant invoice number from Core invoices DB (null until Core lookup lands)",
    )
    result: str = Field(
        description="Merged outcome: pending | processed | not_an_invoice | duplicate | failed"
    )
    firs_status: Optional[str] = Field(
        default=None,
        description="Downstream FIRS state from invoices.transmission_status (null pre-transmit)",
    )
    received_at: Optional[str] = None
    processed_at: Optional[str] = None


class StatusResponse(BaseModel):
    """Response from POST /api/status."""

    results: List[StatusEntry] = Field(default_factory=list)


# ── Ingest Response ──────────────────────────────────────────────────────


class IngestResponse(BaseModel):
    """Response from POST /api/ingest.

    Identity model:
        data_uuid  — Per-request group identifier (always present, even single file)
        file_uuids — Per-file storage identifiers (one per uploaded file)
        trace_id   — x_trace_id for log correlation across services

    Internal mapping: data_uuid = HeartBeat batch_uuid, file_uuids = HeartBeat blob_uuids
    """

    status: str = Field(description="Result status: queued (bulk, accepted) | processed (external) | error")
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
    # doc_ref = the primary file's storage blob_uuid. The Reader threads this as
    # the finalize ``ref`` (scout backend_doc_ref), so the finalized invoice gets
    # blob_uuid==ref==this, exposing the ORIGINAL PDF for byte-fetch (Flow 04).
    doc_ref: str = Field(
        default="",
        description="Primary file storage ref (blob_uuid) — threaded to finalize",
    )
    file_hashes: Optional[List[str]] = Field(
        default=None,
        description="Per-file SHA256 hashes (one per uploaded file)",
    )

    # NOTE (Q4): the bulk flow no longer returns the Core preview inline. The
    # invoice preview is delivered asynchronously via the
    # ``core.preview.available`` lifecycle event on Core's stream (Scout
    # consumes it). The bulk response returns status="queued" promptly. The old
    # ``preview_data`` field was removed from this model accordingly.

    # External flow fields (present when status=processed, external)
    irn: Optional[str] = Field(
        default=None,
        description="Invoice Reference Number (external flow only)",
    )
    qr_code: Optional[str] = Field(
        default=None,
        description="QR code data, base64 (external flow only)",
    )


# ── Finalize (#3 reference-only call) ────────────────────────────────────


class FinalizeRequest(BaseModel):
    """Request body for POST /api/finalize — the #3 reference-only call.

    Reference-only: fiscalizes an ALREADY-ingested doc by reference. NO PDF
    bytes (§B-Submit). At least one of ``ref`` / ``trace_id`` must be present.

        ref       — file SHA-256 / doc_ref (the backend already holds the bytes)
        trace_id  — Scout UUIDv7; carried across the #2↔#3 switch (§3.3), echoed
                    on the resulting lifecycle SSE
        doc_ref   — optional explicit doc_ref (defaults to ``ref``)
    """

    ref: str = Field(default="", description="File SHA-256 / doc_ref of an already-ingested doc")
    trace_id: str = Field(default="", description="Scout UUIDv7 — echoed on the lifecycle SSE")
    doc_ref: str = Field(default="", description="Optional explicit doc_ref (defaults to ref)")


class FinalizeResponse(BaseModel):
    """Response from POST /api/finalize (HTTP 202 accepted).

    ``raw_bytes_sent`` is always False — the #3 call carries no bytes. A
    duplicate / already-finalized ``trace_id`` does NOT reach this shape; it
    returns 409 ALREADY_FINALIZED (client treats as success).
    """

    status: str = Field(default="accepted", description="accepted")
    call: str = Field(default="finalize", description="Always 'finalize'")
    finalize_by_reference: bool = Field(default=True)
    raw_bytes_sent: bool = Field(default=False)
    ref: str = Field(default="", description="Echoed reference")
    doc_ref: str = Field(default="", description="Resolved doc_ref")
    trace_id: str = Field(default="", description="Echoed trace_id")
    event_id: str = Field(default="", description="Lifecycle event id")
    event_family: str = Field(default="", description="Lifecycle event family (relay.finalize.accepted)")
    idempotent_replay: bool = Field(
        default=False,
        description="True if this was an idempotent replay of a prior finalize",
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
