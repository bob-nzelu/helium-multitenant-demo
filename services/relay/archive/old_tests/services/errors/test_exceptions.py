"""
Unit Tests for Relay Service Exceptions

Tests all error classes defined in exceptions.py:
- Error code and message handling
- Status code mapping
- to_dict() serialization
- Custom attributes

Target Coverage: 100%
"""

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.errors.exceptions import (
    # Base
    RelayError,
    # Validation (400)
    ValidationFailedError,
    NoFilesProvidedError,
    TooManyFilesError,
    InvalidFileExtensionError,
    FileSizeExceededError,
    MalwareDetectedError,
    # Authentication (401)
    AuthenticationFailedError,
    InvalidAPIKeyError,
    SignatureVerificationFailedError,
    TimestampExpiredError,
    # Rate Limit (429)
    RateLimitExceededError,
    # Server Errors (500)
    InternalErrorError,
    # Service Unavailable (503)
    ServiceUnavailableError,
    CoreUnavailableError,
    HeartBeatUnavailableError,
    # Transient
    TransientError,
    ConnectionTimeoutError,
    ConnectionResetError,
    # Not Found (404)
    QueueNotFoundError,
    # Duplicate
    DuplicateFileError,
)


# =============================================================================
# Base RelayError Tests
# =============================================================================

class TestRelayError:
    """Tests for base RelayError class."""

    def test_initialization(self):
        """Should initialize with all attributes."""
        error = RelayError(
            error_code="TEST_ERROR",
            message="Test message",
            details=[{"field": "test", "error": "test error"}],
            status_code=400,
        )

        assert error.error_code == "TEST_ERROR"
        assert error.message == "Test message"
        assert error.details == [{"field": "test", "error": "test error"}]
        assert error.status_code == 400

    def test_default_details(self):
        """Should default to empty details list."""
        error = RelayError(
            error_code="TEST",
            message="Test",
            status_code=500,
        )

        assert error.details == []

    def test_to_dict(self):
        """Should serialize to dict correctly."""
        error = RelayError(
            error_code="TEST_ERROR",
            message="Test message",
            details=[{"field": "test", "error": "detail"}],
            status_code=400,
        )

        result = error.to_dict()

        assert result["status"] == "error"
        assert result["error_code"] == "TEST_ERROR"
        assert result["message"] == "Test message"
        assert result["details"] == [{"field": "test", "error": "detail"}]

    def test_exception_message(self):
        """Should use message as exception string."""
        error = RelayError(
            error_code="TEST",
            message="Custom error message",
            status_code=500,
        )

        assert str(error) == "Custom error message"


# =============================================================================
# Validation Errors (400) Tests
# =============================================================================

class TestValidationFailedError:
    """Tests for ValidationFailedError."""

    def test_default_message(self):
        """Should have default message."""
        error = ValidationFailedError()

        assert error.error_code == "VALIDATION_FAILED"
        assert error.message == "Validation failed"
        assert error.status_code == 400

    def test_custom_message(self):
        """Should accept custom message."""
        error = ValidationFailedError(
            message="Custom validation error",
            details=[{"field": "email", "error": "Invalid format"}],
        )

        assert error.message == "Custom validation error"
        assert error.details[0]["field"] == "email"


class TestNoFilesProvidedError:
    """Tests for NoFilesProvidedError."""

    def test_default_message(self):
        """Should have default message."""
        error = NoFilesProvidedError()

        assert error.error_code == "NO_FILES_PROVIDED"
        assert error.message == "No files provided"
        assert error.status_code == 400


class TestTooManyFilesError:
    """Tests for TooManyFilesError."""

    def test_default_message(self):
        """Should have default message."""
        error = TooManyFilesError()

        assert error.error_code == "TOO_MANY_FILES"
        assert error.message == "Too many files provided"
        assert error.status_code == 400


class TestInvalidFileExtensionError:
    """Tests for InvalidFileExtensionError."""

    def test_message_includes_filename(self):
        """Should include filename in message."""
        error = InvalidFileExtensionError(
            filename="test.exe",
            allowed_extensions=[".pdf", ".csv", ".xlsx"],
        )

        assert error.error_code == "INVALID_FILE_EXTENSION"
        assert "test.exe" in error.message
        assert ".pdf" in error.message
        assert error.status_code == 400


class TestFileSizeExceededError:
    """Tests for FileSizeExceededError."""

    def test_message_includes_sizes(self):
        """Should include file size and limit."""
        error = FileSizeExceededError(
            filename="large.pdf",
            size_mb=15.5,
            limit_mb=10.0,
        )

        assert error.error_code == "FILE_SIZE_EXCEEDED"
        assert "large.pdf" in error.message
        assert "15.5" in error.message
        assert "10" in error.message
        assert error.status_code == 400


class TestMalwareDetectedError:
    """Tests for MalwareDetectedError."""

    def test_message_includes_filename(self):
        """Should include filename in message."""
        error = MalwareDetectedError(filename="virus.exe")

        assert error.error_code == "MALWARE_DETECTED"
        assert "virus.exe" in error.message
        assert error.status_code == 400


# =============================================================================
# Authentication Errors (401) Tests
# =============================================================================

class TestAuthenticationFailedError:
    """Tests for AuthenticationFailedError."""

    def test_default_message(self):
        """Should have default message."""
        error = AuthenticationFailedError()

        assert error.error_code == "AUTHENTICATION_FAILED"
        assert error.message == "Authentication failed"
        assert error.status_code == 401


class TestInvalidAPIKeyError:
    """Tests for InvalidAPIKeyError."""

    def test_default_message(self):
        """Should have default message."""
        error = InvalidAPIKeyError()

        assert error.error_code == "INVALID_API_KEY"
        assert error.message == "Invalid API key"
        assert error.status_code == 401


class TestSignatureVerificationFailedError:
    """Tests for SignatureVerificationFailedError."""

    def test_default_message(self):
        """Should have default message."""
        error = SignatureVerificationFailedError()

        assert error.error_code == "SIGNATURE_VERIFICATION_FAILED"
        assert error.message == "Signature verification failed"
        assert error.status_code == 401


class TestTimestampExpiredError:
    """Tests for TimestampExpiredError."""

    def test_default_message(self):
        """Should have default message."""
        error = TimestampExpiredError()

        assert error.error_code == "TIMESTAMP_EXPIRED"
        assert error.message == "Request timestamp expired"
        assert error.status_code == 401


# =============================================================================
# Rate Limit Errors (429) Tests
# =============================================================================

class TestRateLimitExceededError:
    """Tests for RateLimitExceededError."""

    def test_default_values(self):
        """Should have default retry after value."""
        error = RateLimitExceededError()

        assert error.error_code == "RATE_LIMIT_EXCEEDED"
        assert error.message == "Rate limit exceeded"
        assert error.status_code == 429
        assert error.retry_after_seconds == 86400  # 24 hours

    def test_custom_retry_after(self):
        """Should accept custom retry after value."""
        error = RateLimitExceededError(
            message="Custom limit message",
            retry_after_seconds=3600,
        )

        assert error.retry_after_seconds == 3600


# =============================================================================
# Server Errors (500) Tests
# =============================================================================

class TestInternalErrorError:
    """Tests for InternalErrorError."""

    def test_default_message(self):
        """Should have default message."""
        error = InternalErrorError()

        assert error.error_code == "INTERNAL_ERROR"
        assert error.message == "Internal server error"
        assert error.status_code == 500

    def test_stores_original_error(self):
        """Should store original exception."""
        original = ValueError("Original error")
        error = InternalErrorError(
            message="Wrapped error",
            original_error=original,
        )

        assert error.original_error == original


# =============================================================================
# Service Unavailable Errors (503) Tests
# =============================================================================

class TestServiceUnavailableError:
    """Tests for ServiceUnavailableError."""

    def test_default_message(self):
        """Should generate message from service name."""
        error = ServiceUnavailableError(service_name="TestService")

        assert error.error_code == "SERVICE_UNAVAILABLE"
        assert error.message == "TestService is temporarily unavailable"
        assert error.status_code == 503
        assert error.service_name == "TestService"

    def test_custom_message(self):
        """Should accept custom message."""
        error = ServiceUnavailableError(
            service_name="Core",
            message="Core API is down for maintenance",
        )

        assert error.message == "Core API is down for maintenance"


class TestCoreUnavailableError:
    """Tests for CoreUnavailableError."""

    def test_default_message(self):
        """Should have Core-specific default message."""
        error = CoreUnavailableError()

        assert error.message == "Core API is temporarily unavailable"
        assert error.service_name == "Core"
        assert error.status_code == 503


class TestHeartBeatUnavailableError:
    """Tests for HeartBeatUnavailableError."""

    def test_default_message(self):
        """Should have HeartBeat-specific default message."""
        error = HeartBeatUnavailableError()

        assert error.message == "HeartBeat API is temporarily unavailable"
        assert error.service_name == "HeartBeat"
        assert error.status_code == 503


# =============================================================================
# Transient Errors Tests
# =============================================================================

class TestTransientError:
    """Tests for TransientError base class."""

    def test_initialization(self):
        """Should initialize correctly."""
        error = TransientError(
            error_code="TRANSIENT_TEST",
            message="Transient error",
            status_code=500,
        )

        assert error.error_code == "TRANSIENT_TEST"
        assert error.message == "Transient error"
        assert error.status_code == 500


class TestConnectionTimeoutError:
    """Tests for ConnectionTimeoutError."""

    def test_default_message(self):
        """Should have default message."""
        error = ConnectionTimeoutError()

        assert error.error_code == "CONNECTION_TIMEOUT"
        assert error.message == "Connection timeout"
        assert error.status_code == 500


class TestConnectionResetError:
    """Tests for ConnectionResetError."""

    def test_default_message(self):
        """Should have default message."""
        error = ConnectionResetError()

        assert error.error_code == "CONNECTION_RESET"
        assert error.message == "Connection reset by peer"
        assert error.status_code == 500


# =============================================================================
# Not Found Errors (404) Tests
# =============================================================================

class TestQueueNotFoundError:
    """Tests for QueueNotFoundError."""

    def test_message_includes_queue_id(self):
        """Should include queue ID in message."""
        error = QueueNotFoundError(queue_id="queue_123")

        assert error.error_code == "QUEUE_NOT_FOUND"
        assert "queue_123" in error.message
        assert error.status_code == 404


# =============================================================================
# Duplicate File Errors Tests
# =============================================================================

class TestDuplicateFileError:
    """Tests for DuplicateFileError."""

    def test_stores_file_hash(self):
        """Should store file hash."""
        error = DuplicateFileError(file_hash="abc123def456")

        assert error.error_code == "DUPLICATE_FILE"
        assert error.file_hash == "abc123def456"
        assert error.status_code == 400

    def test_stores_original_queue_id(self):
        """Should store original queue ID when provided."""
        error = DuplicateFileError(
            file_hash="abc123",
            original_queue_id="queue_789",
        )

        assert error.original_queue_id == "queue_789"

    def test_original_queue_id_optional(self):
        """Original queue ID should be optional."""
        error = DuplicateFileError(file_hash="abc123")

        assert error.original_queue_id is None

    def test_message_is_user_friendly(self):
        """Message should be user-friendly."""
        error = DuplicateFileError(file_hash="abc123")

        assert "already been processed" in error.message


# =============================================================================
# Exception Hierarchy Tests
# =============================================================================

class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_all_errors_inherit_from_relay_error(self):
        """All custom errors should inherit from RelayError."""
        errors = [
            ValidationFailedError(),
            NoFilesProvidedError(),
            TooManyFilesError(),
            InvalidFileExtensionError("test.exe", [".pdf"]),
            FileSizeExceededError("test.pdf", 15, 10),
            MalwareDetectedError("virus.exe"),
            AuthenticationFailedError(),
            InvalidAPIKeyError(),
            SignatureVerificationFailedError(),
            TimestampExpiredError(),
            RateLimitExceededError(),
            InternalErrorError(),
            ServiceUnavailableError("Test"),
            CoreUnavailableError(),
            HeartBeatUnavailableError(),
            TransientError("TEST", "test"),
            ConnectionTimeoutError(),
            ConnectionResetError(),
            QueueNotFoundError("queue_123"),
            DuplicateFileError("abc123"),
        ]

        for error in errors:
            assert isinstance(error, RelayError)
            assert isinstance(error, Exception)

    def test_transient_errors_inherit_correctly(self):
        """Transient errors should inherit from TransientError."""
        assert isinstance(ConnectionTimeoutError(), TransientError)
        assert isinstance(ConnectionResetError(), TransientError)

    def test_service_unavailable_errors_inherit_correctly(self):
        """Service unavailable errors should inherit from ServiceUnavailableError."""
        assert isinstance(CoreUnavailableError(), ServiceUnavailableError)
        assert isinstance(HeartBeatUnavailableError(), ServiceUnavailableError)
