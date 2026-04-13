"""
Tests for RelayError hierarchy
"""

import pytest

from src.errors import (
    AuthenticationFailedError,
    ConnectionResetError,
    ConnectionTimeoutError,
    CoreUnavailableError,
    DecryptionError,
    DuplicateFileError,
    EncryptionError,
    EncryptionRequiredError,
    FileSizeExceededError,
    HeartBeatUnavailableError,
    InternalError,
    InvalidAPIKeyError,
    InvalidFileExtensionError,
    IRNGenerationError,
    MalwareDetectedError,
    ModuleCacheError,
    ModuleNotLoadedError,
    NoFilesProvidedError,
    QRGenerationError,
    QueueNotFoundError,
    RateLimitExceededError,
    RelayError,
    ServiceUnavailableError,
    SignatureVerificationFailedError,
    TimestampExpiredError,
    TooManyFilesError,
    TotalSizeExceededError,
    TransientError,
    ValidationFailedError,
)


class TestRelayErrorBase:
    """Test base RelayError."""

    def test_base_error(self):
        err = RelayError("TEST_CODE", "Test message")
        assert err.error_code == "TEST_CODE"
        assert err.message == "Test message"
        assert err.status_code == 500
        assert err.details == []
        assert str(err) == "Test message"

    def test_base_error_with_details(self):
        details = [{"field": "file", "error": "too large"}]
        err = RelayError("TEST", "msg", details=details, status_code=400)
        assert err.details == details
        assert err.status_code == 400

    def test_to_dict(self):
        err = RelayError("CODE", "Message")
        d = err.to_dict()
        assert d["status"] == "error"
        assert d["error_code"] == "CODE"
        assert d["message"] == "Message"
        assert "details" not in d  # Empty details not included

    def test_to_dict_with_details(self):
        err = RelayError("C", "M", details=[{"x": "y"}])
        d = err.to_dict()
        assert d["details"] == [{"x": "y"}]

    def test_is_exception(self):
        err = RelayError("C", "M")
        assert isinstance(err, Exception)


class TestValidationErrors:
    """Test validation error subclasses."""

    def test_validation_failed(self):
        err = ValidationFailedError()
        assert err.status_code == 400
        assert err.error_code == "VALIDATION_FAILED"
        assert isinstance(err, RelayError)

    def test_validation_failed_custom_message(self):
        err = ValidationFailedError("Custom msg", details=[{"a": "b"}])
        assert err.message == "Custom msg"
        assert err.details == [{"a": "b"}]

    def test_no_files_provided(self):
        err = NoFilesProvidedError()
        assert err.status_code == 400
        assert "at least 1 file" in err.message
        assert isinstance(err, ValidationFailedError)

    def test_too_many_files(self):
        err = TooManyFilesError(count=5, limit=3)
        assert err.status_code == 400
        assert "5" in err.message
        assert "3" in err.message
        assert isinstance(err, ValidationFailedError)

    def test_invalid_file_extension(self):
        err = InvalidFileExtensionError("doc.exe", [".pdf", ".xml"])
        assert "doc.exe" in err.message
        assert ".pdf" in err.message
        assert err.status_code == 400
        assert isinstance(err, ValidationFailedError)

    def test_file_size_exceeded(self):
        err = FileSizeExceededError("big.pdf", 15.5, 10.0)
        assert "big.pdf" in err.message
        assert "15.5" in err.message
        assert "10" in err.message
        assert err.status_code == 400

    def test_total_size_exceeded(self):
        err = TotalSizeExceededError(35.0, 30.0)
        assert "35.0" in err.message
        assert "30" in err.message
        assert err.status_code == 400


class TestMalwareError:
    """Test malware detection error."""

    def test_malware_detected(self):
        err = MalwareDetectedError("evil.pdf", "Eicar-Test")
        assert "evil.pdf" in err.message
        assert "Eicar-Test" in err.message
        assert err.status_code == 400
        assert err.error_code == "MALWARE_DETECTED"


class TestAuthenticationErrors:
    """Test authentication error subclasses."""

    def test_auth_failed(self):
        err = AuthenticationFailedError()
        assert err.status_code == 401
        assert err.error_code == "AUTHENTICATION_FAILED"

    def test_auth_failed_custom(self):
        err = AuthenticationFailedError("Bad timestamp")
        assert err.message == "Bad timestamp"

    def test_invalid_api_key(self):
        err = InvalidAPIKeyError()
        assert err.status_code == 401
        assert isinstance(err, AuthenticationFailedError)

    def test_signature_failed(self):
        err = SignatureVerificationFailedError()
        assert err.status_code == 401
        assert isinstance(err, AuthenticationFailedError)

    def test_timestamp_expired(self):
        err = TimestampExpiredError(age_seconds=600)
        assert "600" in err.message
        assert "300" in err.message
        assert err.status_code == 401


class TestRateLimitError:
    """Test rate limit error."""

    def test_rate_limit(self):
        err = RateLimitExceededError()
        assert err.status_code == 429
        assert err.retry_after_seconds == 86400

    def test_rate_limit_custom(self):
        err = RateLimitExceededError("Custom limit", retry_after_seconds=3600)
        assert err.message == "Custom limit"
        assert err.retry_after_seconds == 3600


class TestNotFoundError:
    """Test not found error."""

    def test_queue_not_found(self):
        err = QueueNotFoundError("q-123")
        assert "q-123" in err.message
        assert err.status_code == 404


class TestDuplicateError:
    """Test duplicate file error."""

    def test_duplicate_file(self):
        err = DuplicateFileError("abc123")
        assert err.status_code == 409
        assert err.file_hash == "abc123"
        assert err.original_queue_id is None

    def test_duplicate_file_with_queue(self):
        err = DuplicateFileError("abc123", original_queue_id="q-456")
        assert err.original_queue_id == "q-456"
        assert err.details[0]["original_queue_id"] == "q-456"


class TestInternalError:
    """Test internal error."""

    def test_internal_error(self):
        err = InternalError()
        assert err.status_code == 500
        assert err.original_error is None

    def test_internal_error_with_original(self):
        original = ValueError("boom")
        err = InternalError("Wrapped", original_error=original)
        assert err.original_error is original
        assert err.message == "Wrapped"


class TestTransientErrors:
    """Test transient (retryable) errors."""

    def test_transient_error(self):
        err = TransientError()
        assert err.status_code == 500
        assert isinstance(err, RelayError)

    def test_connection_timeout(self):
        err = ConnectionTimeoutError()
        assert err.error_code == "CONNECTION_TIMEOUT"
        assert isinstance(err, TransientError)

    def test_connection_reset(self):
        err = ConnectionResetError()
        assert err.error_code == "CONNECTION_RESET"
        assert isinstance(err, TransientError)


class TestServiceUnavailableErrors:
    """Test service unavailable errors."""

    def test_service_unavailable(self):
        err = ServiceUnavailableError()
        assert err.status_code == 503
        assert err.service_name == "Service"

    def test_service_unavailable_named(self):
        err = ServiceUnavailableError("Redis")
        assert "Redis" in err.message
        assert err.service_name == "Redis"

    def test_core_unavailable(self):
        err = CoreUnavailableError()
        assert err.status_code == 503
        assert err.service_name == "Core"
        assert isinstance(err, ServiceUnavailableError)

    def test_heartbeat_unavailable(self):
        err = HeartBeatUnavailableError()
        assert err.status_code == 503
        assert err.service_name == "HeartBeat"
        assert isinstance(err, ServiceUnavailableError)


class TestEncryptionErrors:
    """Test encryption-related errors."""

    def test_encryption_error(self):
        err = EncryptionError()
        assert err.status_code == 400
        assert err.error_code == "ENCRYPTION_ERROR"

    def test_decryption_error(self):
        err = DecryptionError()
        assert isinstance(err, EncryptionError)
        assert "decrypt" in err.message.lower()

    def test_encryption_required(self):
        err = EncryptionRequiredError()
        assert err.status_code == 403
        assert err.error_code == "ENCRYPTION_REQUIRED"


class TestErrorHierarchy:
    """Test inheritance chain is correct."""

    def test_all_inherit_relay_error(self):
        errors = [
            ValidationFailedError(),
            NoFilesProvidedError(),
            TooManyFilesError(5, 3),
            InvalidFileExtensionError("f.exe", [".pdf"]),
            FileSizeExceededError("f", 1.0, 0.5),
            TotalSizeExceededError(10.0, 5.0),
            MalwareDetectedError("f"),
            AuthenticationFailedError(),
            InvalidAPIKeyError(),
            SignatureVerificationFailedError(),
            TimestampExpiredError(600),
            RateLimitExceededError(),
            QueueNotFoundError("q"),
            DuplicateFileError("h"),
            InternalError(),
            TransientError(),
            ConnectionTimeoutError(),
            ConnectionResetError(),
            ServiceUnavailableError(),
            CoreUnavailableError(),
            HeartBeatUnavailableError(),
            EncryptionError(),
            DecryptionError(),
            EncryptionRequiredError(),
        ]
        for err in errors:
            assert isinstance(err, RelayError), f"{type(err).__name__} not a RelayError"
            assert isinstance(err, Exception)

    def test_validation_subclasses(self):
        """All validation errors should be catchable as ValidationFailedError."""
        errors = [
            NoFilesProvidedError(),
            TooManyFilesError(5, 3),
            InvalidFileExtensionError("f.exe", [".pdf"]),
            FileSizeExceededError("f", 1.0, 0.5),
            TotalSizeExceededError(10.0, 5.0),
        ]
        for err in errors:
            assert isinstance(err, ValidationFailedError)

    def test_auth_subclasses(self):
        """All auth errors should be catchable as AuthenticationFailedError."""
        errors = [
            InvalidAPIKeyError(),
            SignatureVerificationFailedError(),
            TimestampExpiredError(600),
        ]
        for err in errors:
            assert isinstance(err, AuthenticationFailedError)

    def test_transient_subclasses(self):
        """All transient errors should be catchable as TransientError."""
        errors = [
            ConnectionTimeoutError(),
            ConnectionResetError(),
        ]
        for err in errors:
            assert isinstance(err, TransientError)


class TestModuleCacheErrors:
    """Test module cache and IRN/QR error classes."""

    def test_module_cache_error(self):
        err = ModuleCacheError()
        assert err.error_code == "MODULE_CACHE_ERROR"
        assert err.status_code == 500
        assert isinstance(err, RelayError)

    def test_module_cache_error_custom_message(self):
        err = ModuleCacheError("Failed to load irn_generator")
        assert "irn_generator" in err.message
        assert err.status_code == 500

    def test_module_not_loaded_error(self):
        err = ModuleNotLoadedError("qr_generator")
        assert err.module_name == "qr_generator"
        assert err.status_code == 503
        assert isinstance(err, ServiceUnavailableError)
        assert "qr_generator" in err.message

    def test_module_not_loaded_is_service_unavailable(self):
        """ModuleNotLoadedError should be catchable as ServiceUnavailableError."""
        err = ModuleNotLoadedError("irn_generator")
        assert isinstance(err, ServiceUnavailableError)
        assert isinstance(err, RelayError)

    def test_irn_generation_error(self):
        err = IRNGenerationError()
        assert err.error_code == "IRN_GENERATION_ERROR"
        assert err.status_code == 500
        assert isinstance(err, RelayError)

    def test_irn_generation_error_custom_message(self):
        err = IRNGenerationError("Invalid invoice data: missing TIN")
        assert "missing TIN" in err.message

    def test_qr_generation_error(self):
        err = QRGenerationError()
        assert err.error_code == "QR_GENERATION_ERROR"
        assert err.status_code == 500
        assert isinstance(err, RelayError)

    def test_qr_generation_error_custom_message(self):
        err = QRGenerationError("FIRS public key expired")
        assert "expired" in err.message

    def test_to_dict_module_cache(self):
        err = ModuleCacheError("cache dir missing")
        d = err.to_dict()
        assert d["error_code"] == "MODULE_CACHE_ERROR"
        assert d["status"] == "error"

    def test_to_dict_irn(self):
        err = IRNGenerationError("no sequence")
        d = err.to_dict()
        assert d["error_code"] == "IRN_GENERATION_ERROR"

    def test_to_dict_qr(self):
        err = QRGenerationError("encoding failed")
        d = err.to_dict()
        assert d["error_code"] == "QR_GENERATION_ERROR"
