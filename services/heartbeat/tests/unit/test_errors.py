"""
Tests for HeartBeat Error Hierarchy

Tests errors.py — all error classes serialize correctly.
"""

import pytest


class TestHeartBeatError:
    """Tests for error hierarchy."""

    def test_base_error(self):
        """Base error has all fields."""
        from src.errors import HeartBeatError
        err = HeartBeatError("TEST_CODE", "test message", status_code=418)
        assert err.error_code == "TEST_CODE"
        assert err.message == "test message"
        assert err.status_code == 418
        assert str(err) == "test message"

    def test_to_dict(self):
        """to_dict serializes to API format."""
        from src.errors import HeartBeatError
        err = HeartBeatError("ERR", "msg", details=[{"key": "val"}])
        d = err.to_dict()
        assert d["status"] == "error"
        assert d["error_code"] == "ERR"
        assert d["message"] == "msg"
        assert d["details"] == [{"key": "val"}]

    def test_to_dict_no_details(self):
        """to_dict omits details when None."""
        from src.errors import HeartBeatError
        d = HeartBeatError("ERR", "msg").to_dict()
        assert "details" not in d

    def test_validation_error(self):
        """ValidationError has status_code 400."""
        from src.errors import ValidationError
        err = ValidationError("bad input")
        assert err.status_code == 400
        assert err.error_code == "VALIDATION_FAILED"

    def test_authentication_error(self):
        """AuthenticationError has status_code 401."""
        from src.errors import AuthenticationError
        assert AuthenticationError().status_code == 401

    def test_not_found_error(self):
        """NotFoundError has status_code 404."""
        from src.errors import BlobNotFoundError
        err = BlobNotFoundError("uuid-123")
        assert err.status_code == 404
        assert "uuid-123" in err.message

    def test_conflict_error(self):
        """DuplicateBlobError has status_code 409."""
        from src.errors import DuplicateBlobError
        err = DuplicateBlobError("uuid-456")
        assert err.status_code == 409

    def test_daily_limit_error(self):
        """DailyLimitExceededError has status_code 429."""
        from src.errors import DailyLimitExceededError
        err = DailyLimitExceededError("company-x", 500, 500)
        assert err.status_code == 429
        assert "company-x" in err.message

    def test_storage_error(self):
        """StorageError has status_code 500."""
        from src.errors import StorageError
        assert StorageError().status_code == 500
        assert StorageError().error_code == "STORAGE_ERROR"

    def test_storage_unavailable(self):
        """StorageUnavailableError has status_code 503."""
        from src.errors import StorageUnavailableError
        assert StorageUnavailableError().status_code == 503

    def test_database_unavailable(self):
        """DatabaseUnavailableError has status_code 503."""
        from src.errors import DatabaseUnavailableError
        assert DatabaseUnavailableError().status_code == 503

    def test_transient_error(self):
        """TransientError signals retryable failure."""
        from src.errors import TransientError
        err = TransientError("temp failure")
        assert err.status_code == 500
        assert err.error_code == "TRANSIENT_ERROR"

    def test_error_inheritance(self):
        """All errors inherit from HeartBeatError."""
        from src.errors import (
            HeartBeatError, ValidationError, AuthenticationError,
            NotFoundError, ConflictError, InternalError,
            TransientError, DatabaseError, StorageError,
            ServiceUnavailableError,
        )
        for cls in [ValidationError, AuthenticationError, NotFoundError,
                     ConflictError, InternalError, TransientError,
                     DatabaseError, StorageError, ServiceUnavailableError]:
            assert issubclass(cls, HeartBeatError)
