"""
WS7: Pydantic request/response models for Reports & Statistics.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────


class ReportType(str, Enum):
    COMPLIANCE = "compliance"
    TRANSMISSION = "transmission"
    CUSTOMER = "customer"
    AUDIT_TRAIL = "audit_trail"
    MONTHLY_SUMMARY = "monthly_summary"


class ReportFormat(str, Enum):
    PDF = "pdf"
    EXCEL = "excel"


class ReportStatus(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"


class StatisticsSection(str, Enum):
    OVERVIEW = "overview"
    INVOICES = "invoices"
    CUSTOMERS = "customers"
    INVENTORY = "inventory"
    COMPLIANCE = "compliance"


class StatisticsPeriod(str, Enum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    ALL = "all"


# Report type → allowed format mapping
REPORT_FORMAT_MAP: dict[ReportType, ReportFormat] = {
    ReportType.COMPLIANCE: ReportFormat.PDF,
    ReportType.TRANSMISSION: ReportFormat.EXCEL,
    ReportType.CUSTOMER: ReportFormat.EXCEL,
    ReportType.AUDIT_TRAIL: ReportFormat.PDF,
    ReportType.MONTHLY_SUMMARY: ReportFormat.PDF,
}

# Report type → content type mapping
REPORT_CONTENT_TYPE: dict[ReportFormat, str] = {
    ReportFormat.PDF: "application/pdf",
    ReportFormat.EXCEL: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


# ── Request Models ───────────────────────────────────────────────────────


class ReportFilters(BaseModel):
    """Filters for report generation."""

    date_from: date | None = None
    date_to: date | None = None
    status: list[str] | None = None
    customer_id: str | None = None


class GenerateReportRequest(BaseModel):
    """POST /api/v1/reports/generate body."""

    report_type: ReportType
    format: ReportFormat | None = None  # Auto-determined from report_type if omitted
    filters: ReportFilters = Field(default_factory=ReportFilters)


# ── Response Models ──────────────────────────────────────────────────────


class GenerateReportResponse(BaseModel):
    """202 Accepted response for report generation."""

    report_id: str
    status: str = "generating"
    estimated_seconds: int = 30


class ReportStatusResponse(BaseModel):
    """GET /api/v1/reports/{report_id}/status response."""

    report_id: str
    report_type: str
    format: str
    status: str
    title: str | None = None
    download_url: str | None = None
    generated_at: str | None = None
    expires_at: str | None = None
    size_bytes: int | None = None
    error_message: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ReportStatusResponse:
        report_id = row["report_id"]
        status = row["status"]
        download_url = (
            f"/api/v1/reports/{report_id}/download"
            if status == "ready"
            else None
        )
        return cls(
            report_id=report_id,
            report_type=row["report_type"],
            format=row["format"],
            status=status,
            title=row.get("title"),
            download_url=download_url,
            generated_at=(
                row["generated_at"].isoformat()
                if row.get("generated_at")
                else None
            ),
            expires_at=(
                row["expires_at"].isoformat()
                if row.get("expires_at")
                else None
            ),
            size_bytes=row.get("size_bytes"),
            error_message=row.get("error_message"),
        )


class StatisticsResponse(BaseModel):
    """GET /api/v1/statistics response."""

    section: str
    period: str
    date_from: str | None = None
    date_to: str | None = None
    data: dict[str, Any]
