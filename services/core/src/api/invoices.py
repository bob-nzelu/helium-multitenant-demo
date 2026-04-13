"""
Invoice Read Endpoints (WS4)

GET /api/v1/invoice/{invoice_id} - Single invoice with all child tables.
GET /api/v1/invoices - Paginated list with filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Query, Request

from src.data import invoice_repository
from src.database.pool import get_connection
from src.errors import CoreError, CoreErrorCode
from src.models.entities import PaginatedEnvelope

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["invoices"])

RECOVERY_WINDOW_HOURS = 24


@router.get("/invoice/{invoice_id}")
async def get_invoice(request: Request, invoice_id: str):
    """Fetch a single invoice with all child records."""
    pool = request.app.state.pool

    async with get_connection(pool, "public") as conn:
        invoice = await invoice_repository.get_by_id(conn, invoice_id)

    if invoice is None:
        raise CoreError(
            error_code=CoreErrorCode.ENTITY_NOT_FOUND,
            message=f"Invoice {invoice_id} not found",
        )

    if invoice.get("deleted_at"):
        deleted_at = invoice["deleted_at"]
        recovery_until = _compute_recovery_until(deleted_at)
        raise CoreError(
            error_code=CoreErrorCode.ENTITY_DELETED,
            message=f"Invoice {invoice_id} is deleted",
            details=[{
                "deleted_at": str(deleted_at),
                "recovery_until": str(recovery_until),
            }],
        )

    return invoice


@router.get("/invoices")
async def list_invoices(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    status: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    document_type: str | None = Query(default=None),
    transaction_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """Paginated invoice list with filtering and sorting."""

    status_list = status.split(",") if status else None

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
        status=status_list, direction=direction,
        document_type=document_type, transaction_type=transaction_type,
        date_from=date_from, date_to=date_to,
        search=search if search else None,
    )

    async with get_connection(pool, "public") as conn:
        items = await invoice_repository.list_paginated(conn, **filter_kwargs)
        total_count = await invoice_repository.get_count(
            conn,
            status=status_list, direction=direction,
            document_type=document_type, transaction_type=transaction_type,
            date_from=date_from, date_to=date_to,
            search=search if search else None,
        )

    return PaginatedEnvelope.build(items, total_count, page, per_page)


def _compute_recovery_until(deleted_at) -> str:
    """Compute the recovery deadline from deleted_at."""
    if isinstance(deleted_at, str):
        try:
            dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
    elif isinstance(deleted_at, datetime):
        dt = deleted_at
    else:
        dt = datetime.now(timezone.utc)

    recovery = dt + timedelta(hours=RECOVERY_WINDOW_HOURS)
    return recovery.isoformat()
