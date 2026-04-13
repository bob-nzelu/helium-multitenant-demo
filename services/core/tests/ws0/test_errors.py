"""Tests for CoreError hierarchy — all 13 codes, status codes, to_dict()."""

import pytest

from src.errors import (
    CircuitOpenError,
    CoreError,
    CoreErrorCode,
    DatabaseError,
    DuplicateError,
    ERROR_STATUS_MAP,
    ExternalServiceError,
    InternalError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitedError,
    SchemaMismatchError,
    StaleDataError,
    TimeoutError,
    ValidationError,
)


class TestCoreErrorCode:
    """Verify all error codes exist with correct values."""

    def test_all_error_codes_exist(self):
        assert len(CoreErrorCode) == 41

    @pytest.mark.parametrize(
        "code,value",
        [
            (CoreErrorCode.VALIDATION_ERROR, "INV_001"),
            (CoreErrorCode.NOT_FOUND, "INV_002"),
            (CoreErrorCode.DUPLICATE, "INV_003"),
            (CoreErrorCode.TIMEOUT, "INV_004"),
            (CoreErrorCode.EXTERNAL_SERVICE, "INV_005"),
            (CoreErrorCode.DATABASE_ERROR, "INV_006"),
            (CoreErrorCode.PERMISSION_DENIED, "INV_007"),
            (CoreErrorCode.RATE_LIMITED, "INV_008"),
            (CoreErrorCode.QUOTA_EXCEEDED, "INV_009"),
            (CoreErrorCode.SCHEMA_MISMATCH, "INV_010"),
            (CoreErrorCode.STALE_DATA, "INV_011"),
            (CoreErrorCode.CIRCUIT_OPEN, "INV_012"),
            (CoreErrorCode.INTERNAL_ERROR, "INV_013"),
        ],
    )
    def test_code_values(self, code, value):
        assert code.value == value

    def test_codes_are_strings(self):
        for code in CoreErrorCode:
            assert isinstance(code.value, str)


class TestErrorStatusMap:
    """Verify status code mapping for all 13 codes."""

    @pytest.mark.parametrize(
        "code,expected_status",
        [
            (CoreErrorCode.VALIDATION_ERROR, 400),
            (CoreErrorCode.NOT_FOUND, 404),
            (CoreErrorCode.DUPLICATE, 409),
            (CoreErrorCode.TIMEOUT, 504),
            (CoreErrorCode.EXTERNAL_SERVICE, 502),
            (CoreErrorCode.DATABASE_ERROR, 500),
            (CoreErrorCode.PERMISSION_DENIED, 403),
            (CoreErrorCode.RATE_LIMITED, 429),
            (CoreErrorCode.QUOTA_EXCEEDED, 429),
            (CoreErrorCode.SCHEMA_MISMATCH, 422),
            (CoreErrorCode.STALE_DATA, 409),
            (CoreErrorCode.CIRCUIT_OPEN, 503),
            (CoreErrorCode.INTERNAL_ERROR, 500),
        ],
    )
    def test_status_codes(self, code, expected_status):
        assert ERROR_STATUS_MAP[code] == expected_status

    def test_all_codes_mapped(self):
        for code in CoreErrorCode:
            assert code in ERROR_STATUS_MAP


class TestCoreError:
    """Test base CoreError behavior."""

    def test_basic_creation(self):
        err = CoreError(CoreErrorCode.INTERNAL_ERROR, "something broke")
        assert err.error_code == CoreErrorCode.INTERNAL_ERROR
        assert err.message == "something broke"
        assert err.status_code == 500
        assert err.details == []

    def test_with_details(self):
        details = [{"field": "invoice_number", "error": "required"}]
        err = CoreError(CoreErrorCode.VALIDATION_ERROR, "bad input", details=details)
        assert err.details == details
        assert err.status_code == 400

    def test_custom_status_code_override(self):
        err = CoreError(CoreErrorCode.INTERNAL_ERROR, "custom", status_code=418)
        assert err.status_code == 418

    def test_to_dict_minimal(self):
        err = CoreError(CoreErrorCode.NOT_FOUND, "not found")
        d = err.to_dict()
        assert d == {"error": "INV_002", "message": "not found"}
        assert "details" not in d

    def test_to_dict_with_details(self):
        details = [{"field": "id", "error": "missing"}]
        err = CoreError(CoreErrorCode.VALIDATION_ERROR, "bad", details=details)
        d = err.to_dict()
        assert d["error"] == "INV_001"
        assert d["message"] == "bad"
        assert d["details"] == details

    def test_is_exception(self):
        err = CoreError(CoreErrorCode.INTERNAL_ERROR, "boom")
        assert isinstance(err, Exception)
        assert str(err) == "boom"


class TestErrorSubclasses:
    """Test all 13 concrete error subclasses."""

    @pytest.mark.parametrize(
        "cls,expected_code,expected_status",
        [
            (ValidationError, CoreErrorCode.VALIDATION_ERROR, 400),
            (NotFoundError, CoreErrorCode.NOT_FOUND, 404),
            (DuplicateError, CoreErrorCode.DUPLICATE, 409),
            (TimeoutError, CoreErrorCode.TIMEOUT, 504),
            (ExternalServiceError, CoreErrorCode.EXTERNAL_SERVICE, 502),
            (DatabaseError, CoreErrorCode.DATABASE_ERROR, 500),
            (PermissionDeniedError, CoreErrorCode.PERMISSION_DENIED, 403),
            (RateLimitedError, CoreErrorCode.RATE_LIMITED, 429),
            (QuotaExceededError, CoreErrorCode.QUOTA_EXCEEDED, 429),
            (SchemaMismatchError, CoreErrorCode.SCHEMA_MISMATCH, 422),
            (StaleDataError, CoreErrorCode.STALE_DATA, 409),
            (CircuitOpenError, CoreErrorCode.CIRCUIT_OPEN, 503),
            (InternalError, CoreErrorCode.INTERNAL_ERROR, 500),
        ],
    )
    def test_subclass_defaults(self, cls, expected_code, expected_status):
        err = cls()
        assert err.error_code == expected_code
        assert err.status_code == expected_status
        assert isinstance(err, CoreError)
        assert isinstance(err, Exception)
        assert err.message  # not empty

    def test_subclass_custom_message(self):
        err = NotFoundError(message="Invoice abc not found")
        assert err.message == "Invoice abc not found"
        assert err.error_code == CoreErrorCode.NOT_FOUND

    def test_subclass_with_details(self):
        details = [{"field": "tin", "error": "invalid format"}]
        err = ValidationError(message="Bad TIN", details=details)
        d = err.to_dict()
        assert d["details"] == details
