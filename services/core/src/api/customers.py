"""
Customer Read Endpoints (WS4)

GET /api/v1/customer/{customer_id} - Single customer with branches + contacts.
GET /api/v1/customers - Paginated list with filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Query, Request

from src.data import customer_repository
from src.database.pool import get_connection
from src.errors import CoreError, CoreErrorCode
from src.models.entities import PaginatedEnvelope

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["customers"])

RECOVERY_WINDOW_HOURS = 24


@router.get("/customer/{customer_id}")
async def get_customer(request: Request, customer_id: str):
    """Fetch a single customer with branches and contacts."""
    pool = request.app.state.pool

    async with get_connection(pool, "public") as conn:
        customer = await customer_repository.get_by_id(conn, customer_id)

    if customer is None:
        raise CoreError(
            error_code=CoreErrorCode.ENTITY_NOT_FOUND,
            message=f"Customer {customer_id} not found",
        )

    if customer.get("deleted_at"):
        deleted_at = customer["deleted_at"]
        recovery_until = _compute_recovery_until(deleted_at)
        raise CoreError(
            error_code=CoreErrorCode.ENTITY_DELETED,
            message=f"Customer {customer_id} is deleted",
            details=[{
                "deleted_at": str(deleted_at),
                "recovery_until": str(recovery_until),
            }],
        )

    return customer


@router.get("/customers")
async def list_customers(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    customer_type: str | None = Query(default=None),
    state: str | None = Query(default=None),
    compliance_min: int | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """Paginated customer list with filtering and sorting."""

    if search is not None:
        search = search.strip()
        if len(search) < 2:
            raise CoreError(
                error_code=CoreErrorCode.SEARCH_QUERY_TOO_SHORT,
                message="Search query must be at least 2 characters",
            )

    pool = request.app.state.pool
    filter_kwargs = dict(
        page=page, per_page=per_page,
        sort_by=sort_by, sort_order=sort_order,
        customer_type=customer_type, state=state,
        compliance_min=compliance_min,
        search=search if search else None,
    )

    async with get_connection(pool, "public") as conn:
        items = await customer_repository.list_paginated(conn, **filter_kwargs)
        total_count = await customer_repository.get_count(
            conn,
            customer_type=customer_type, state=state,
            compliance_min=compliance_min,
            search=search if search else None,
        )

    return PaginatedEnvelope.build(items, total_count, page, per_page)


def _compute_recovery_until(deleted_at) -> str:
    if isinstance(deleted_at, str):
        try:
            dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
    elif isinstance(deleted_at, datetime):
        dt = deleted_at
    else:
        dt = datetime.now(timezone.utc)

    return (dt + timedelta(hours=RECOVERY_WINDOW_HOURS)).isoformat()
