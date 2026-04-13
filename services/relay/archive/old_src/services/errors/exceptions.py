"""
Relay Service Error Definitions

All error codes from RELAY_BULK_SPEC.md are defined here as exception classes.
These are used throughout the Relay services for consistent error handling.
"""

from typing import Dict, Any, Optional, List


class RelayError(Exception):
    """Base class for all Relay service errors"""

    def __init__(
        self,
        error_code: str,
        message: str,
        details: Optional[List[Dict[str, str]]] = None,
        status_code: int = 500,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or []
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to API response format"""
        return {
            "status": "error",
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


# Validation Errors (400)


class ValidationFailedError(RelayError):
    """Validation failed for one or more files"""

    def __init__(
        self, message: str = "Validation failed", details: Optional[List[Dict[str, str]]] = None
    ):
        super().__init__(
            error_code="VALIDATION_FAILED",
            message=message,
            details=details,
            status_code=400,
        )


class NoFilesProvidedError(RelayError):
    """No files were provided in the request"""

    def __init__(self, message: str = "No files provided"):
        super().__init__(
            error_code="NO_FILES_PROVIDED",
            message=message,
            status_code=400,
        )


class TooManyFilesError(RelayError):
    """Too many files were provided"""

    def __init__(self, message: str = "Too many files provided"):
        super().__init__(
            error_code="TOO_MANY_FILES",
            message=message,
            status_code=400,
        )


class InvalidFileExtensionError(RelayError):
    """File extension not allowed"""

    def __init__(self, filename: str, allowed_extensions: List[str]):
        message = f"File '{filename}' has invalid extension. Allowed: {', '.join(allowed_extensions)}"
        super().__init__(
            error_code="INVALID_FILE_EXTENSION",
            message=message,
            details=[{"field": "filename", "error": "Invalid file extension"}],
            status_code=400,
        )


class FileSizeExceededError(RelayError):
    """File size exceeds limit"""

    def __init__(self, filename: str, size_mb: float, limit_mb: float):
        message = f"File '{filename}' size ({size_mb:.1f} MB) exceeds limit ({limit_mb} MB)"
        super().__init__(
            error_code="FILE_SIZE_EXCEEDED",
            message=message,
            details=[{"field": "filename", "error": "File size exceeds limit"}],
            status_code=400,
        )


class MalwareDetectedError(RelayError):
    """Malware detected in file"""

    def __init__(self, filename: str):
        message = f"Malware detected in '{filename}'"
        super().__init__(
            error_code="MALWARE_DETECTED",
            message=message,
            details=[{"field": "filename", "error": "Malware detected"}],
            status_code=400,
        )


# Authentication Errors (401)


class AuthenticationFailedError(RelayError):
    """Authentication failed"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            error_code="AUTHENTICATION_FAILED",
            message=message,
            status_code=401,
        )


class InvalidAPIKeyError(RelayError):
    """Invalid API key"""

    def __init__(self, message: str = "Invalid API key"):
        super().__init__(
            error_code="INVALID_API_KEY",
            message=message,
            status_code=401,
        )


class SignatureVerificationFailedError(RelayError):
    """HMAC signature verification failed"""

    def __init__(self, message: str = "Signature verification failed"):
        super().__init__(
            error_code="SIGNATURE_VERIFICATION_FAILED",
            message=message,
            status_code=401,
        )


class TimestampExpiredError(RelayError):
    """Timestamp is outside the 5-minute window"""

    def __init__(self, message: str = "Request timestamp expired"):
        super().__init__(
            error_code="TIMESTAMP_EXPIRED",
            message=message,
            status_code=401,
        )


# Rate Limit Errors (429)


class RateLimitExceededError(RelayError):
    """Daily usage limit exceeded"""

    def __init__(self, message: str = "Rate limit exceeded", retry_after_seconds: int = 86400):
        super().__init__(
            error_code="RATE_LIMIT_EXCEEDED",
            message=message,
            status_code=429,
        )
        self.retry_after_seconds = retry_after_seconds


# Server Errors (500)


class InternalErrorError(RelayError):
    """Internal server error"""

    def __init__(self, message: str = "Internal server error", original_error: Optional[Exception] = None):
        super().__init__(
            error_code="INTERNAL_ERROR",
            message=message,
            status_code=500,
        )
        self.original_error = original_error


# Service Unavailable Errors (503)


class ServiceUnavailableError(RelayError):
    """Service is temporarily unavailable"""

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
    """Core API is unavailable"""

    def __init__(self, message: str = "Core API is temporarily unavailable"):
        super().__init__(service_name="Core", message=message)


class HeartBeatUnavailableError(ServiceUnavailableError):
    """HeartBeat API is unavailable"""

    def __init__(self, message: str = "HeartBeat API is temporarily unavailable"):
        super().__init__(service_name="HeartBeat", message=message)


# Transient Errors (for retry logic)


class TransientError(RelayError):
    """Base class for transient errors that should be retried"""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = 500,
    ):
        super().__init__(
            error_code=error_code,
            message=message,
            status_code=status_code,
        )


class ConnectionTimeoutError(TransientError):
    """Connection timeout (should retry)"""

    def __init__(self, message: str = "Connection timeout"):
        super().__init__(
            error_code="CONNECTION_TIMEOUT",
            message=message,
            status_code=500,
        )


class ConnectionResetError(TransientError):
    """Connection reset by peer (should retry)"""

    def __init__(self, message: str = "Connection reset by peer"):
        super().__init__(
            error_code="CONNECTION_RESET",
            message=message,
            status_code=500,
        )


# Not Found Errors (404)


class QueueNotFoundError(RelayError):
    """Queue entry not found"""

    def __init__(self, queue_id: str):
        message = f"Queue entry '{queue_id}' not found"
        super().__init__(
            error_code="QUEUE_NOT_FOUND",
            message=message,
            status_code=404,
        )


# Duplicate File Errors


class DuplicateFileError(RelayError):
    """File is a duplicate (already processed)"""

    def __init__(self, file_hash: str, original_queue_id: Optional[str] = None):
        message = "This file has already been processed"
        super().__init__(
            error_code="DUPLICATE_FILE",
            message=message,
            details=[{"field": "file", "error": "Duplicate file"}],
            status_code=400,
        )
        self.file_hash = file_hash
        self.original_queue_id = original_queue_id
