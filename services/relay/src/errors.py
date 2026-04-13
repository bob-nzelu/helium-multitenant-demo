"""
Relay-API Error Hierarchy

Cherry-picked from old_src/services/errors/exceptions.py with cleaner signatures.
All error codes follow the RELAY_BULK_SPEC.md convention.

Hierarchy:
    RelayError (base)
    ├── ValidationFailedError (400)
    │   ├── NoFilesProvidedError
    │   ├── TooManyFilesError
    │   ├── InvalidFileExtensionError
    │   └── FileSizeExceededError
    ├── MalwareDetectedError (400)
    ├── AuthenticationFailedError (401)
    │   ├── InvalidAPIKeyError
    │   ├── SignatureVerificationFailedError
    │   ├── TimestampExpiredError
    │   └── JWTRejectedError
    ├── RateLimitExceededError (429)
    ├── QueueNotFoundError (404)
    ├── DuplicateFileError (409)
    ├── InternalError (500)
    ├── TransientError (500, retryable)
    │   ├── ConnectionTimeoutError
    │   └── ConnectionResetError
    └── ServiceUnavailableError (503)
        ├── CoreUnavailableError
        └── HeartBeatUnavailableError
"""

from typing import Any, Dict, List, Optional


# ── Base ──────────────────────────────────────────────────────────────────


class RelayError(Exception):
    """Base class for all Relay-API errors."""

    def __init__(
        self,
        error_code: str,
        message: str,
        details: Optional[List[Dict[str, Any]]] = None,
        status_code: int = 500,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or []
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response format."""
        result: Dict[str, Any] = {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


# ── Validation Errors (400) ───────────────────────────────────────────────


class ValidationFailedError(RelayError):
    """File or request validation failed."""

    def __init__(
        self,
        message: str = "Validation failed",
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__(
            error_code="VALIDATION_FAILED",
            message=message,
            details=details,
            status_code=400,
        )


class NoFilesProvidedError(ValidationFailedError):
    """No files in the request."""

    def __init__(self):
        super().__init__(message="No files provided. Upload at least 1 file.")


class TooManyFilesError(ValidationFailedError):
    """File count exceeds limit."""

    def __init__(self, count: int, limit: int):
        super().__init__(
            message=f"Too many files: {count} provided, max {limit}.",
            details=[{"count": str(count), "limit": str(limit)}],
        )


class InvalidFileExtensionError(ValidationFailedError):
    """File extension not in allowed list."""

    def __init__(self, filename: str, allowed: List[str]):
        super().__init__(
            message=f"File '{filename}' has invalid extension. Allowed: {', '.join(allowed)}",
            details=[{"filename": filename, "allowed": ", ".join(allowed)}],
        )


class FileSizeExceededError(ValidationFailedError):
    """Individual file or total batch size exceeds limit."""

    def __init__(self, filename: str, size_mb: float, limit_mb: float):
        super().__init__(
            message=f"File '{filename}' ({size_mb:.1f} MB) exceeds {limit_mb} MB limit.",
            details=[{
                "filename": filename,
                "size_mb": f"{size_mb:.2f}",
                "limit_mb": f"{limit_mb:.1f}",
            }],
        )


class TotalSizeExceededError(ValidationFailedError):
    """Total upload size exceeds limit."""

    def __init__(self, total_mb: float, limit_mb: float):
        super().__init__(
            message=f"Total upload size ({total_mb:.1f} MB) exceeds {limit_mb} MB limit.",
            details=[{
                "total_mb": f"{total_mb:.2f}",
                "limit_mb": f"{limit_mb:.1f}",
            }],
        )


# ── Malware (400) ─────────────────────────────────────────────────────────


class MalwareDetectedError(RelayError):
    """Malware detected in uploaded file."""

    def __init__(self, filename: str, virus_name: str = "unknown"):
        super().__init__(
            error_code="MALWARE_DETECTED",
            message=f"Malware detected in '{filename}': {virus_name}",
            details=[{"filename": filename, "virus_name": virus_name}],
            status_code=400,
        )


# ── Authentication Errors (401) ───────────────────────────────────────────


class AuthenticationFailedError(RelayError):
    """Authentication failed (generic)."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            error_code="AUTHENTICATION_FAILED",
            message=message,
            status_code=401,
        )


class InvalidAPIKeyError(AuthenticationFailedError):
    """API key not recognized."""

    def __init__(self):
        super().__init__(message="API key not recognized.")


class SignatureVerificationFailedError(AuthenticationFailedError):
    """HMAC signature mismatch."""

    def __init__(self):
        super().__init__(message="HMAC signature verification failed.")


class TimestampExpiredError(AuthenticationFailedError):
    """Request timestamp outside the 5-minute window."""

    def __init__(self, age_seconds: int):
        super().__init__(
            message=f"Timestamp is {age_seconds}s old. Must be within 300s."
        )


class JWTRejectedError(AuthenticationFailedError):
    """HeartBeat rejected the forwarded JWT (returned 401 on blob write)."""

    def __init__(self, message: str = "JWT rejected by HeartBeat"):
        super().__init__(message=message)


# ── Rate Limit (429) ──────────────────────────────────────────────────────


class RateLimitExceededError(RelayError):
    """Daily usage limit exceeded."""

    def __init__(
        self,
        message: str = "Daily rate limit exceeded",
        retry_after_seconds: int = 86400,
    ):
        super().__init__(
            error_code="RATE_LIMIT_EXCEEDED",
            message=message,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


# ── Not Found (404) ───────────────────────────────────────────────────────


class QueueNotFoundError(RelayError):
    """Queue entry not found."""

    def __init__(self, queue_id: str):
        super().__init__(
            error_code="QUEUE_NOT_FOUND",
            message=f"Queue entry '{queue_id}' not found.",
            status_code=404,
        )


# ── Duplicate (409) ───────────────────────────────────────────────────────


class DuplicateFileError(RelayError):
    """File already processed (duplicate hash)."""

    def __init__(self, file_hash: str, original_queue_id: Optional[str] = None):
        super().__init__(
            error_code="DUPLICATE_FILE",
            message="This file has already been processed.",
            details=[{
                "file_hash": file_hash,
                **({"original_queue_id": original_queue_id} if original_queue_id else {}),
            }],
            status_code=409,
        )
        self.file_hash = file_hash
        self.original_queue_id = original_queue_id


# ── Internal Error (500) ──────────────────────────────────────────────────


class InternalError(RelayError):
    """Internal server error."""

    def __init__(
        self,
        message: str = "Internal server error",
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            error_code="INTERNAL_ERROR",
            message=message,
            status_code=500,
        )
        self.original_error = original_error


# ── Transient Errors (500, retryable) ─────────────────────────────────────


class TransientError(RelayError):
    """Base for transient errors — clients should retry with backoff."""

    def __init__(
        self,
        error_code: str = "TRANSIENT_ERROR",
        message: str = "Transient error, please retry",
        status_code: int = 500,
    ):
        super().__init__(
            error_code=error_code,
            message=message,
            status_code=status_code,
        )


class ConnectionTimeoutError(TransientError):
    """Upstream connection timed out."""

    def __init__(self, message: str = "Connection timed out"):
        super().__init__(error_code="CONNECTION_TIMEOUT", message=message)


class ConnectionResetError(TransientError):
    """Upstream connection reset."""

    def __init__(self, message: str = "Connection reset by peer"):
        super().__init__(error_code="CONNECTION_RESET", message=message)


# ── Service Unavailable (503) ─────────────────────────────────────────────


class ServiceUnavailableError(RelayError):
    """Upstream service temporarily unavailable."""

    def __init__(self, service_name: str = "Service", message: Optional[str] = None):
        if message is None:
            message = f"{service_name} is temporarily unavailable"
        super().__init__(
            error_code="SERVICE_UNAVAILABLE",
            message=message,
            status_code=503,
        )
        self.service_name = service_name


class CoreUnavailableError(ServiceUnavailableError):
    """Core API is unavailable."""

    def __init__(self, message: str = "Core API is temporarily unavailable"):
        super().__init__(service_name="Core", message=message)


class HeartBeatUnavailableError(ServiceUnavailableError):
    """HeartBeat API is unavailable."""

    def __init__(self, message: str = "HeartBeat API is temporarily unavailable"):
        super().__init__(service_name="HeartBeat", message=message)


# ── Encryption Errors ─────────────────────────────────────────────────────


class EncryptionError(RelayError):
    """Encryption or decryption failed."""

    def __init__(self, message: str = "Encryption error"):
        super().__init__(
            error_code="ENCRYPTION_ERROR",
            message=message,
            status_code=400,
        )


class DecryptionError(EncryptionError):
    """Failed to decrypt incoming envelope."""

    def __init__(self, message: str = "Failed to decrypt request envelope"):
        super().__init__(message=message)


class EncryptionRequiredError(RelayError):
    """Remote request without encryption."""

    def __init__(self):
        super().__init__(
            error_code="ENCRYPTION_REQUIRED",
            message="Encryption required for remote requests. Set X-Encrypted: true.",
            status_code=403,
        )


# ── Module Cache Errors ──────────────────────────────────────────────────


class ModuleCacheError(RelayError):
    """Transforma module cache operation failed."""

    def __init__(self, message: str = "Module cache error"):
        super().__init__(
            error_code="MODULE_CACHE_ERROR",
            message=message,
            status_code=500,
        )


class ModuleNotLoadedError(ServiceUnavailableError):
    """Transforma module not yet loaded."""

    def __init__(self, module_name: str):
        super().__init__(
            service_name="TransformaCache",
            message=f"Module '{module_name}' not loaded. Try again shortly.",
        )
        self.module_name = module_name


# ── IRN/QR Errors ────────────────────────────────────────────────────────


class IRNGenerationError(RelayError):
    """IRN generation failed."""

    def __init__(self, message: str = "IRN generation failed"):
        super().__init__(
            error_code="IRN_GENERATION_ERROR",
            message=message,
            status_code=500,
        )


class QRGenerationError(RelayError):
    """QR code generation failed."""

    def __init__(self, message: str = "QR generation failed"):
        super().__init__(
            error_code="QR_GENERATION_ERROR",
            message=message,
            status_code=500,
        )
