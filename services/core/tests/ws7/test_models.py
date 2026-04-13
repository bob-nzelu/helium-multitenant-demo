"""
Tests for WS7 Pydantic models — request/response validation.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.reports.models import (
    GenerateReportRequest,
    GenerateReportResponse,
    ReportFilters,
    ReportFormat,
    ReportStatusResponse,
    ReportType,
    StatisticsPeriod,
    StatisticsResponse,
    StatisticsSection,
    REPORT_FORMAT_MAP,
    REPORT_CONTENT_TYPE,
)


class TestReportType:
    def test_all_types_defined(self):
        assert len(ReportType) == 5

    def test_compliance(self):
        assert ReportType.COMPLIANCE == "compliance"

    def test_transmission(self):
        assert ReportType.TRANSMISSION == "transmission"

    def test_customer(self):
        assert ReportType.CUSTOMER == "customer"

    def test_audit_trail(self):
        assert ReportType.AUDIT_TRAIL == "audit_trail"

    def test_monthly_summary(self):
        assert ReportType.MONTHLY_SUMMARY == "monthly_summary"


class TestReportFormat:
    def test_pdf(self):
        assert ReportFormat.PDF == "pdf"

    def test_excel(self):
        assert ReportFormat.EXCEL == "excel"


class TestStatisticsSection:
    def test_all_sections(self):
        assert len(StatisticsSection) == 5
        assert StatisticsSection.OVERVIEW == "overview"
        assert StatisticsSection.INVOICES == "invoices"
        assert StatisticsSection.CUSTOMERS == "customers"
        assert StatisticsSection.INVENTORY == "inventory"
        assert StatisticsSection.COMPLIANCE == "compliance"


class TestStatisticsPeriod:
    def test_all_periods(self):
        assert len(StatisticsPeriod) == 6
        assert StatisticsPeriod.TODAY == "today"
        assert StatisticsPeriod.ALL == "all"


class TestReportFormatMap:
    def test_compliance_is_pdf(self):
        assert REPORT_FORMAT_MAP[ReportType.COMPLIANCE] == ReportFormat.PDF

    def test_transmission_is_excel(self):
        assert REPORT_FORMAT_MAP[ReportType.TRANSMISSION] == ReportFormat.EXCEL

    def test_customer_is_excel(self):
        assert REPORT_FORMAT_MAP[ReportType.CUSTOMER] == ReportFormat.EXCEL

    def test_audit_trail_is_pdf(self):
        assert REPORT_FORMAT_MAP[ReportType.AUDIT_TRAIL] == ReportFormat.PDF

    def test_monthly_summary_is_pdf(self):
        assert REPORT_FORMAT_MAP[ReportType.MONTHLY_SUMMARY] == ReportFormat.PDF


class TestContentType:
    def test_pdf_content_type(self):
        assert REPORT_CONTENT_TYPE[ReportFormat.PDF] == "application/pdf"

    def test_excel_content_type(self):
        assert "spreadsheetml" in REPORT_CONTENT_TYPE[ReportFormat.EXCEL]


class TestReportFilters:
    def test_empty_filters(self):
        f = ReportFilters()
        assert f.date_from is None
        assert f.date_to is None
        assert f.status is None
        assert f.customer_id is None

    def test_with_dates(self):
        f = ReportFilters(date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
        assert f.date_from == date(2026, 1, 1)
        assert f.date_to == date(2026, 3, 31)

    def test_with_status_filter(self):
        f = ReportFilters(status=["TRANSMITTED", "ACCEPTED"])
        assert len(f.status) == 2


class TestGenerateReportRequest:
    def test_minimal(self):
        req = GenerateReportRequest(report_type=ReportType.COMPLIANCE)
        assert req.report_type == ReportType.COMPLIANCE
        assert req.format is None  # Auto-determined
        assert req.filters.date_from is None

    def test_with_all_fields(self):
        req = GenerateReportRequest(
            report_type=ReportType.TRANSMISSION,
            format=ReportFormat.EXCEL,
            filters=ReportFilters(
                date_from=date(2026, 1, 1),
                date_to=date(2026, 3, 31),
                status=["TRANSMITTED"],
            ),
        )
        assert req.report_type == ReportType.TRANSMISSION
        assert req.format == ReportFormat.EXCEL


class TestGenerateReportResponse:
    def test_defaults(self):
        resp = GenerateReportResponse(report_id="rpt-123")
        assert resp.report_id == "rpt-123"
        assert resp.status == "generating"
        assert resp.estimated_seconds == 30


class TestReportStatusResponse:
    def test_from_row_generating(self):
        row = {
            "report_id": "rpt-1",
            "report_type": "compliance",
            "format": "pdf",
            "status": "generating",
            "title": "Compliance Report",
            "generated_at": None,
            "expires_at": None,
            "size_bytes": None,
            "error_message": None,
        }
        resp = ReportStatusResponse.from_row(row)
        assert resp.report_id == "rpt-1"
        assert resp.status == "generating"
        assert resp.download_url is None  # Not ready yet

    def test_from_row_ready(self):
        from datetime import datetime, timezone
        row = {
            "report_id": "rpt-2",
            "report_type": "transmission",
            "format": "excel",
            "status": "ready",
            "title": "Transmission Report",
            "generated_at": datetime(2026, 3, 18, tzinfo=timezone.utc),
            "expires_at": datetime(2026, 3, 25, tzinfo=timezone.utc),
            "size_bytes": 12345,
            "error_message": None,
        }
        resp = ReportStatusResponse.from_row(row)
        assert resp.status == "ready"
        assert resp.download_url == "/api/v1/reports/rpt-2/download"
        assert resp.size_bytes == 12345


class TestStatisticsResponse:
    def test_basic(self):
        resp = StatisticsResponse(
            section="overview",
            period="month",
            data={"total_invoices": 100},
        )
        assert resp.section == "overview"
        assert resp.data["total_invoices"] == 100
