"""
WS7: Statistics Router — GET /api/v1/statistics

Serves aggregate metrics for Float's Statistics mApp (5 sub-tabs).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request

from src.auth.permissions import check_permission
from src.errors import CoreError, CoreErrorCode
from src.reports.models import (
    StatisticsPeriod,
    StatisticsResponse,
    StatisticsSection,
)
from src.reports.statistics_service import get_statistics, resolve_period

router = APIRouter(prefix="/api/v1", tags=["statistics"])


@router.get("/statistics")
async def statistics(
    request: Request,
    section: str = Query(..., description="overview|invoices|customers|inventory|compliance"),
    period: str = Query(default="month", description="today|week|month|quarter|year|all"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> StatisticsResponse:
    """
    Aggregate statistics for a given section and time period.

    Consumed by Float SDK's Statistics mApp (5 sub-tabs).
    """
    check_permission(request, "invoice.read")

    # Validate section
    try:
        section_enum = StatisticsSection(section)
    except ValueError:
        raise CoreError(
            error_code=CoreErrorCode.INVALID_STATISTICS_SECTION,
            message=f"Invalid section: {section}. Must be one of: overview, invoices, customers, inventory, compliance",
        )

    # Validate period
    try:
        period_enum = StatisticsPeriod(period)
    except ValueError:
        raise CoreError(
            error_code=CoreErrorCode.VALIDATION_ERROR,
            message=f"Invalid period: {period}. Must be one of: today, week, month, quarter, year, all",
        )

    pool = request.app.state.pool
    data = await get_statistics(
        pool,
        section=section_enum,
        period=period_enum,
        date_from=date_from,
        date_to=date_to,
    )

    resolved_from, resolved_to = resolve_period(period_enum, date_from, date_to)

    return StatisticsResponse(
        section=section,
        period=period,
        date_from=str(resolved_from),
        date_to=str(resolved_to),
        data=data,
    )
