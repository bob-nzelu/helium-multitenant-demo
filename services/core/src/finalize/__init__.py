"""WS5 — Finalize + Edge Integration.

Commit and dispatch layer: receive user-confirmed .hlm, validate edits
against preview .hlx provenance, generate IRN/QR, commit to DB, queue to Edge.

Modules:
    provenance   — Provenance constants and editability rules
    errors       — Error hierarchy for WS5 operations
    edit_validator — Diff engine (.hlm vs .hlx)
    irn_generator — FIRS IRN generation
    qr_generator  — QR code generation (200x200 PNG, base64)
    record_creator — DB committal (invoices, customers, inventory)
    edge_client   — Edge FIRS submission client
    pipeline      — Orchestrator (validate → IRN → QR → commit → Edge)
    sse_events    — SSE event definitions for Float
    router        — HTTP endpoints
"""

from src.finalize.edit_validator import EditValidator, EditValidationResult
from src.finalize.errors import (
    FinalizeError,
    EditValidationError,
    IRNGenerationError,
    RecordCommitError,
    EdgeSubmitError,
)
from src.finalize.irn_generator import generate_irn, validate_irn
from src.finalize.pipeline import FinalizePipeline, FinalizeResult
from src.finalize.record_creator import RecordCreator, CommitResult
from src.finalize.router import finalize_routes

__all__ = [
    "EditValidator",
    "EditValidationResult",
    "FinalizePipeline",
    "FinalizeResult",
    "RecordCreator",
    "CommitResult",
    "generate_irn",
    "validate_irn",
    "finalize_routes",
    "FinalizeError",
    "EditValidationError",
    "IRNGenerationError",
    "RecordCommitError",
    "EdgeSubmitError",
]
