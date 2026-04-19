"""
HeartBeat Service Error Hierarchy

Follows the Helium {Service}Error pattern:
    HeartBeatError (base)
    ├── ValidationError (400)
    ├── AuthenticationError (401)
    ├── NotFoundError (404)
    ├── ConflictError (409)
    ├── RateLimitError (429)
    ├── InternalError (500)
    ├── TransientError (500, retryable)
    ├── DatabaseError (500)
    ├── StorageError (500)
    └── ServiceUnavailableError (503)

All errors serialize to:
    {"status": "error", "error_code": "...", "message": "...", "details": [...]}
"""

from typing import Any, Dict, List, Optional


class HeartBeatError(Exception):
    """Base error for all HeartBeat service errors."""

    def __init__(
        self,
        error_code: str,
        message: str,
        details: Optional[List[Dict[str, Any]]] = None,
        status_code: int = 500,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# ── 400 Bad Request ─────────────────────────────────────────────────────

class ValidationError(HeartBeatError):
    def __init__(self, message: str = "Validation failed", details=None):
        super().__init__(
            error_code="VALIDATION_FAILED",
            message=message,
            details=details,
            status_code=400,
        )


class InvalidBlobUUIDError(ValidationError):
    def __init__(self, blob_uuid: str):
        super().__init__(
            message=f"Invalid blob UUID: {blob_uuid}",
            details=[{"blob_uuid": blob_uuid}],
        )


class InvalidFileHashError(ValidationError):
    def __init__(self, file_hash: str):
        super().__init__(
            message=f"Invalid file hash (expected SHA256 hex): {file_hash[:16]}...",
            details=[{"file_hash_prefix": file_hash[:16]}],
        )


# ── 401 Unauthorized ───────────────────────────────────────────────────

class AuthenticationError(HeartBeatError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            error_code="AUTHENTICATION_FAILED",
            message=message,
            status_code=401,
        )


# ── 404 Not Found ──────────────────────────────────────────────────────

class NotFoundError(HeartBeatError):
    def __init__(self, message: str = "Resource not found", details=None):
        super().__init__(
            error_code="NOT_FOUND",
            message=message,
            details=details,
            status_code=404,
        )


class BlobNotFoundError(NotFoundError):
    def __init__(self, blob_uuid: str):
        super().__init__(
            message=f"Blob not found: {blob_uuid}",
            details=[{"blob_uuid": blob_uuid}],
        )


# ── 409 Conflict ────────────────────────────────────────────────────────

class ConflictError(HeartBeatError):
    def __init__(self, message: str = "Resource conflict", details=None):
        super().__init__(
            error_code="CONFLICT",
            message=message,
            details=details,
            status_code=409,
        )


class DuplicateBlobError(ConflictError):
    def __init__(self, blob_uuid: str):
        super().__init__(
            message=f"Blob already registered: {blob_uuid}",
            details=[{"blob_uuid": blob_uuid}],
        )


# ── 429 Rate Limit ─────────────────────────────────────────────────────

class DailyLimitExceededError(HeartBeatError):
    def __init__(self, company_id: str, files_today: int, daily_limit: int):
        super().__init__(
            error_code="DAILY_LIMIT_EXCEEDED",
            message=f"Daily upload limit reached for {company_id}: {files_today}/{daily_limit}",
            details=[{
                "company_id": company_id,
                "files_today": str(files_today),
                "daily_limit": str(daily_limit),
            }],
            status_code=429,
        )


# ── 500 Internal ────────────────────────────────────────────────────────

class InternalError(HeartBeatError):
    def __init__(self, message: str = "Internal server error", details=None):
        super().__init__(
            error_code="INTERNAL_ERROR",
            message=message,
            details=details,
            status_code=500,
        )


class TransientError(HeartBeatError):
    """Retryable error — caller should retry with backoff."""
    def __init__(self, message: str = "Transient error, please retry", details=None):
        super().__init__(
            error_code="TRANSIENT_ERROR",
            message=message,
            details=details,
            status_code=500,
        )


class DatabaseError(HeartBeatError):
    def __init__(self, message: str = "Database error", details=None):
        super().__init__(
            error_code="DATABASE_ERROR",
            message=message,
            details=details,
            status_code=500,
        )


class StorageError(HeartBeatError):
    def __init__(self, message: str = "Blob storage error", details=None):
        super().__init__(
            error_code="STORAGE_ERROR",
            message=message,
            details=details,
            status_code=500,
        )


# ── 503 Service Unavailable ────────────────────────────────────────────

class ServiceUnavailableError(HeartBeatError):
    def __init__(self, message: str = "Service unavailable", details=None):
        super().__init__(
            error_code="SERVICE_UNAVAILABLE",
            message=message,
            details=details,
            status_code=503,
        )


class StorageUnavailableError(ServiceUnavailableError):
    def __init__(self):
        super().__init__(message="Blob storage is unavailable")


class DatabaseUnavailableError(ServiceUnavailableError):
    def __init__(self):
        super().__init__(message="Database is unavailable")
