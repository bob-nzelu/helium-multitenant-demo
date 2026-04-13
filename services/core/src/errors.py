"""
Core Service Error Hierarchy

All error codes follow DECISIONS_V2.md Decision 8.

Hierarchy:
    CoreError (base)
    ├── ValidationError (INV_001, 400)
    ├── NotFoundError (INV_002, 404)
    ├── DuplicateError (INV_003, 409)
    ├── TimeoutError (INV_004, 504)
    ├── ExternalServiceError (INV_005, 502)
    ├── DatabaseError (INV_006, 500)
    ├── PermissionDeniedError (INV_007, 403)
    ├── RateLimitedError (INV_008, 429)
    ├── QuotaExceededError (INV_009, 429)
    ├── SchemaMismatchError (INV_010, 422)
    ├── StaleDataError (INV_011, 409)
    ├── CircuitOpenError (INV_012, 503)
    └── InternalError (INV_013, 500)
"""

from enum import Enum
from typing import Any


class CoreErrorCode(str, Enum):
    """All Core service error codes."""

    # WS0 — Foundation
    VALIDATION_ERROR = "INV_001"
    NOT_FOUND = "INV_002"
    DUPLICATE = "INV_003"
    TIMEOUT = "INV_004"
    EXTERNAL_SERVICE = "INV_005"
    DATABASE_ERROR = "INV_006"
    PERMISSION_DENIED = "INV_007"
    RATE_LIMITED = "INV_008"
    QUOTA_EXCEEDED = "INV_009"
    SCHEMA_MISMATCH = "INV_010"
    STALE_DATA = "INV_011"
    CIRCUIT_OPEN = "INV_012"
    INTERNAL_ERROR = "INV_013"

    # WS5 — Finalize + Edge
    QUEUE_NOT_FOUND = "QUEUE_NOT_FOUND"
    QUEUE_NOT_READY = "QUEUE_NOT_READY"
    DATA_UUID_MISMATCH = "DATA_UUID_MISMATCH"
    INVALID_EDITS = "INVALID_EDITS"
    INVALID_FIELD_VALUE_FIN = "INVALID_FIELD_VALUE_FIN"
    PREVIEW_NOT_FOUND = "PREVIEW_NOT_FOUND"
    FINALIZATION_FAILED = "FINALIZATION_FAILED"
    IRN_GENERATION_FAILED = "IRN_GENERATION_FAILED"
    QR_GENERATION_FAILED = "QR_GENERATION_FAILED"
    EDGE_UNAVAILABLE = "EDGE_UNAVAILABLE"
    EDGE_REJECTED = "EDGE_REJECTED"
    INVOICE_NOT_FOUND = "INVOICE_NOT_FOUND"
    INVOICE_NOT_RETRYABLE = "INVOICE_NOT_RETRYABLE"
    INVOICE_NOT_RETRANSMITTABLE = "INVOICE_NOT_RETRANSMITTABLE"
    INVOICE_NOT_INBOUND = "INVOICE_NOT_INBOUND"
    INVOICE_NOT_PENDING_REVIEW = "INVOICE_NOT_PENDING_REVIEW"
    ACTION_REASON_REQUIRED = "ACTION_REASON_REQUIRED"
    INVALID_UPDATE_TYPE = "INVALID_UPDATE_TYPE"

    # WS4 — Entity CRUD + Search
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    ENTITY_DELETED = "ENTITY_DELETED"
    FIELD_NOT_EDITABLE = "FIELD_NOT_EDITABLE"
    INVALID_FIELD_VALUE = "INVALID_FIELD_VALUE"
    FORBIDDEN = "FORBIDDEN"
    INVALID_ENTITY_TYPE = "INVALID_ENTITY_TYPE"
    RECOVERY_EXPIRED = "RECOVERY_EXPIRED"
    INVALID_PAGINATION = "INVALID_PAGINATION"
    SEARCH_QUERY_TOO_SHORT = "SEARCH_QUERY_TOO_SHORT"
    SEARCH_QUERY_TOO_LONG = "SEARCH_QUERY_TOO_LONG"

    # WS7 — Reports & Statistics
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    REPORT_NOT_READY = "REPORT_NOT_READY"
    REPORT_EXPIRED = "REPORT_EXPIRED"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"
    INVALID_REPORT_TYPE = "INVALID_REPORT_TYPE"
    INVALID_STATISTICS_SECTION = "INVALID_STATISTICS_SECTION"


# Map error codes to HTTP status codes
ERROR_STATUS_MAP: dict[CoreErrorCode, int] = {
    CoreErrorCode.VALIDATION_ERROR: 400,
    CoreErrorCode.NOT_FOUND: 404,
    CoreErrorCode.DUPLICATE: 409,
    CoreErrorCode.TIMEOUT: 504,
    CoreErrorCode.EXTERNAL_SERVICE: 502,
    CoreErrorCode.DATABASE_ERROR: 500,
    CoreErrorCode.PERMISSION_DENIED: 403,
    CoreErrorCode.RATE_LIMITED: 429,
    CoreErrorCode.QUOTA_EXCEEDED: 429,
    CoreErrorCode.SCHEMA_MISMATCH: 422,
    CoreErrorCode.STALE_DATA: 409,
    CoreErrorCode.CIRCUIT_OPEN: 503,
    CoreErrorCode.INTERNAL_ERROR: 500,
    # WS5
    CoreErrorCode.QUEUE_NOT_FOUND: 404,
    CoreErrorCode.QUEUE_NOT_READY: 409,
    CoreErrorCode.DATA_UUID_MISMATCH: 400,
    CoreErrorCode.INVALID_EDITS: 422,
    CoreErrorCode.INVALID_FIELD_VALUE_FIN: 422,
    CoreErrorCode.PREVIEW_NOT_FOUND: 404,
    CoreErrorCode.FINALIZATION_FAILED: 500,
    CoreErrorCode.IRN_GENERATION_FAILED: 500,
    CoreErrorCode.QR_GENERATION_FAILED: 500,
    CoreErrorCode.EDGE_UNAVAILABLE: 503,
    CoreErrorCode.EDGE_REJECTED: 502,
    CoreErrorCode.INVOICE_NOT_FOUND: 404,
    CoreErrorCode.INVOICE_NOT_RETRYABLE: 409,
    CoreErrorCode.INVOICE_NOT_RETRANSMITTABLE: 409,
    CoreErrorCode.INVOICE_NOT_INBOUND: 400,
    CoreErrorCode.INVOICE_NOT_PENDING_REVIEW: 409,
    CoreErrorCode.ACTION_REASON_REQUIRED: 422,
    CoreErrorCode.INVALID_UPDATE_TYPE: 400,
    # WS4
    CoreErrorCode.ENTITY_NOT_FOUND: 404,
    CoreErrorCode.ENTITY_DELETED: 410,
    CoreErrorCode.FIELD_NOT_EDITABLE: 422,
    CoreErrorCode.INVALID_FIELD_VALUE: 422,
    CoreErrorCode.FORBIDDEN: 403,
    CoreErrorCode.INVALID_ENTITY_TYPE: 400,
    CoreErrorCode.RECOVERY_EXPIRED: 410,
    CoreErrorCode.INVALID_PAGINATION: 400,
    CoreErrorCode.SEARCH_QUERY_TOO_SHORT: 400,
    CoreErrorCode.SEARCH_QUERY_TOO_LONG: 400,
    # WS7
    CoreErrorCode.REPORT_NOT_FOUND: 404,
    CoreErrorCode.REPORT_NOT_READY: 409,
    CoreErrorCode.REPORT_EXPIRED: 410,
    CoreErrorCode.REPORT_GENERATION_FAILED: 500,
    CoreErrorCode.INVALID_REPORT_TYPE: 400,
    CoreErrorCode.INVALID_STATISTICS_SECTION: 400,
}


# ── Base ──────────────────────────────────────────────────────────────────


class CoreError(Exception):
    """Base class for all Core service errors."""

    def __init__(
        self,
        error_code: CoreErrorCode,
        message: str,
        details: list[dict[str, Any]] | None = None,
        status_code: int | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or []
        self.status_code = status_code or ERROR_STATUS_MAP.get(error_code, 500)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to API response format."""
        result: dict[str, Any] = {
            "error": self.error_code.value,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# ── Validation (400) ─────────────────────────────────────────────────────


class ValidationError(CoreError):
    """Request validation failed."""

    def __init__(
        self,
        message: str = "Validation failed",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
        )


# ── Not Found (404) ──────────────────────────────────────────────────────


class NotFoundError(CoreError):
    """Resource not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.NOT_FOUND,
            message=message,
            details=details,
        )


# ── Duplicate (409) ──────────────────────────────────────────────────────


class DuplicateError(CoreError):
    """Duplicate resource detected."""

    def __init__(
        self,
        message: str = "Duplicate resource",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.DUPLICATE,
            message=message,
            details=details,
        )


# ── Timeout (504) ────────────────────────────────────────────────────────


class TimeoutError(CoreError):
    """Operation timed out."""

    def __init__(
        self,
        message: str = "Operation timed out",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.TIMEOUT,
            message=message,
            details=details,
        )


# ── External Service (502) ───────────────────────────────────────────────


class ExternalServiceError(CoreError):
    """External service call failed."""

    def __init__(
        self,
        message: str = "External service error",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.EXTERNAL_SERVICE,
            message=message,
            details=details,
        )


# ── Database (500) ───────────────────────────────────────────────────────


class DatabaseError(CoreError):
    """Database operation failed."""

    def __init__(
        self,
        message: str = "Database error",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.DATABASE_ERROR,
            message=message,
            details=details,
        )


# ── Permission Denied (403) ──────────────────────────────────────────────


class PermissionDeniedError(CoreError):
    """Insufficient permissions."""

    def __init__(
        self,
        message: str = "Permission denied",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.PERMISSION_DENIED,
            message=message,
            details=details,
        )


# ── Rate Limited (429) ───────────────────────────────────────────────────


class RateLimitedError(CoreError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.RATE_LIMITED,
            message=message,
            details=details,
        )


# ── Quota Exceeded (429) ─────────────────────────────────────────────────


class QuotaExceededError(CoreError):
    """Resource quota exceeded."""

    def __init__(
        self,
        message: str = "Quota exceeded",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.QUOTA_EXCEEDED,
            message=message,
            details=details,
        )


# ── Schema Mismatch (422) ────────────────────────────────────────────────


class SchemaMismatchError(CoreError):
    """Data does not match expected schema."""

    def __init__(
        self,
        message: str = "Schema mismatch",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.SCHEMA_MISMATCH,
            message=message,
            details=details,
        )


# ── Stale Data (409) ─────────────────────────────────────────────────────


class StaleDataError(CoreError):
    """Data has been modified since last read."""

    def __init__(
        self,
        message: str = "Stale data — resource was modified",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.STALE_DATA,
            message=message,
            details=details,
        )


# ── Circuit Open (503) ───────────────────────────────────────────────────


class CircuitOpenError(CoreError):
    """Circuit breaker is open — service degraded."""

    def __init__(
        self,
        message: str = "Circuit breaker open — service temporarily unavailable",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.CIRCUIT_OPEN,
            message=message,
            details=details,
        )


# ── Internal Error (500) ─────────────────────────────────────────────────


class InternalError(CoreError):
    """Unexpected internal error."""

    def __init__(
        self,
        message: str = "Internal server error",
        details: list[dict[str, Any]] | None = None,
    ):
        super().__init__(
            error_code=CoreErrorCode.INTERNAL_ERROR,
            message=message,
            details=details,
        )
