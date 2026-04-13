"""
WS5 Error Hierarchy.

All WS5-specific exceptions inherit from FinalizeError.
Each maps to an error code for structured API responses.
"""

from __future__ import annotations


class FinalizeError(Exception):
    """Base error for all WS5 finalize operations."""

    code: str = "FINALIZE_ERROR"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error": self.code,
            "message": str(self),
            "details": self.details,
        }


class EditValidationError(FinalizeError):
    """Edit validation failed — illegal field modifications detected."""

    code = "EDIT_VALIDATION_FAILED"


class IRNGenerationError(FinalizeError):
    """IRN generation failed — invalid invoice data for FIRS format."""

    code = "IRN_GENERATION_FAILED"


class QRGenerationError(FinalizeError):
    """QR code generation failed."""

    code = "QR_GENERATION_FAILED"


class RecordCommitError(FinalizeError):
    """Database commit failed during record creation."""

    code = "RECORD_COMMIT_FAILED"


class EdgeSubmitError(FinalizeError):
    """Edge FIRS submission failed."""

    code = "EDGE_SUBMIT_FAILED"


class PreviewNotFoundError(FinalizeError):
    """Preview .hlx not found in HeartBeat for the given batch."""

    code = "PREVIEW_NOT_FOUND"


class BatchMismatchError(FinalizeError):
    """Submitted .hlm does not match the preview .hlx structure."""

    code = "BATCH_MISMATCH"
