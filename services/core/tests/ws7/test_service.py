"""
Tests for WS7 Report Service — orchestration of async report generation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

from src.reports.models import ReportFormat, ReportType
from src.reports.service import _REPORT_TITLES, _get_generator, request_report


class TestReportTitles:
    def test_all_types_have_titles(self):
        for rt in ReportType:
            assert rt in _REPORT_TITLES
            assert len(_REPORT_TITLES[rt]) > 0

    def test_compliance_title(self):
        assert _REPORT_TITLES[ReportType.COMPLIANCE] == "Compliance Report"


class TestGetGenerator:
    def test_compliance_generator(self):
        gen = _get_generator(ReportType.COMPLIANCE)
        assert hasattr(gen, "generate")

    def test_transmission_generator(self):
        gen = _get_generator(ReportType.TRANSMISSION)
        assert hasattr(gen, "generate")

    def test_customer_generator(self):
        gen = _get_generator(ReportType.CUSTOMER)
        assert hasattr(gen, "generate")

    def test_audit_trail_generator(self):
        gen = _get_generator(ReportType.AUDIT_TRAIL)
        assert hasattr(gen, "generate")

    def test_monthly_summary_generator(self):
        gen = _get_generator(ReportType.MONTHLY_SUMMARY)
        assert hasattr(gen, "generate")

    def test_generator_cached(self):
        gen1 = _get_generator(ReportType.COMPLIANCE)
        gen2 = _get_generator(ReportType.COMPLIANCE)
        assert gen1 is gen2


class TestRequestReport:
    @pytest.mark.asyncio
    async def test_creates_record_and_returns(self, mock_pool):
        """request_report should create a DB record and return it."""
        # Setup mock to return a report row
        cursor = mock_pool._mock_cursor
        cursor.description = [
            type("Desc", (), {"name": n})()
            for n in [
                "report_id", "company_id", "report_type", "format",
                "status", "title", "blob_uuid", "filters",
                "generated_at", "expires_at", "size_bytes",
                "error_message", "generated_by", "created_at", "updated_at",
            ]
        ]
        cursor.fetchone = AsyncMock(return_value=(
            "rpt-test", "comp-1", "compliance", "pdf",
            "generating", "Compliance Report", None, None,
            None, None, None,
            None, "user-1", datetime.now(timezone.utc), datetime.now(timezone.utc),
        ))

        result = await request_report(
            mock_pool,
            report_type=ReportType.COMPLIANCE,
            report_format=ReportFormat.PDF,
            filters={"date_from": "2026-01-01"},
            company_id="comp-1",
            generated_by="user-1",
            heartbeat_client=AsyncMock(),
            notification_service=AsyncMock(),
            sse_manager=AsyncMock(),
            audit_logger=AsyncMock(),
        )

        assert result["report_id"] == "rpt-test"
        assert result["status"] == "generating"

    @pytest.mark.asyncio
    async def test_auto_format_from_type(self, mock_pool):
        """When format is None, it should be auto-determined from report_type."""
        cursor = mock_pool._mock_cursor
        cursor.description = [
            type("Desc", (), {"name": n})()
            for n in [
                "report_id", "company_id", "report_type", "format",
                "status", "title", "blob_uuid", "filters",
                "generated_at", "expires_at", "size_bytes",
                "error_message", "generated_by", "created_at", "updated_at",
            ]
        ]
        cursor.fetchone = AsyncMock(return_value=(
            "rpt-auto", "comp-1", "transmission", "excel",
            "generating", "Transmission Report", None, None,
            None, None, None, None, None,
            datetime.now(timezone.utc), datetime.now(timezone.utc),
        ))

        result = await request_report(
            mock_pool,
            report_type=ReportType.TRANSMISSION,
            report_format=None,  # Should auto-pick excel
            filters={},
            company_id="comp-1",
            generated_by=None,
            heartbeat_client=AsyncMock(),
            notification_service=AsyncMock(),
            sse_manager=AsyncMock(),
            audit_logger=AsyncMock(),
        )
        assert result["format"] == "excel"
