"""
Tests for WS7 error codes — verify they exist and have correct HTTP status mappings.
"""

from __future__ import annotations

from src.errors import CoreError, CoreErrorCode, ERROR_STATUS_MAP


class TestWs7ErrorCodes:
    def test_report_not_found(self):
        assert CoreErrorCode.REPORT_NOT_FOUND == "REPORT_NOT_FOUND"
        assert ERROR_STATUS_MAP[CoreErrorCode.REPORT_NOT_FOUND] == 404

    def test_report_not_ready(self):
        assert CoreErrorCode.REPORT_NOT_READY == "REPORT_NOT_READY"
        assert ERROR_STATUS_MAP[CoreErrorCode.REPORT_NOT_READY] == 409

    def test_report_expired(self):
        assert CoreErrorCode.REPORT_EXPIRED == "REPORT_EXPIRED"
        assert ERROR_STATUS_MAP[CoreErrorCode.REPORT_EXPIRED] == 410

    def test_report_generation_failed(self):
        assert CoreErrorCode.REPORT_GENERATION_FAILED == "REPORT_GENERATION_FAILED"
        assert ERROR_STATUS_MAP[CoreErrorCode.REPORT_GENERATION_FAILED] == 500

    def test_invalid_report_type(self):
        assert CoreErrorCode.INVALID_REPORT_TYPE == "INVALID_REPORT_TYPE"
        assert ERROR_STATUS_MAP[CoreErrorCode.INVALID_REPORT_TYPE] == 400

    def test_invalid_statistics_section(self):
        assert CoreErrorCode.INVALID_STATISTICS_SECTION == "INVALID_STATISTICS_SECTION"
        assert ERROR_STATUS_MAP[CoreErrorCode.INVALID_STATISTICS_SECTION] == 400

    def test_core_error_to_dict(self):
        err = CoreError(
            error_code=CoreErrorCode.REPORT_NOT_FOUND,
            message="Report rpt-123 not found",
        )
        d = err.to_dict()
        assert d["error"] == "REPORT_NOT_FOUND"
        assert "rpt-123" in d["message"]
        assert err.status_code == 404
