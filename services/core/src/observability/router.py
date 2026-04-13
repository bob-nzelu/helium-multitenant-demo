"""
WS6: Observability Router

Endpoints:
    GET  /api/v1/audit                      — Paginated audit log query
    GET  /api/v1/notifications              — User's notifications
    POST /api/v1/notifications/{id}/read    — Mark notification read
    GET  /api/v1/notifications/unread-count — Badge count
    GET  /metrics                           — Prometheus text format
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from src.observability.models import (
    AuditEvent,
    AuditQueryResponse,
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)

logger = structlog.get_logger()

router = APIRouter(tags=["Observability"])


# ── GET /api/v1/audit ──────────────────────────────────────────────────────


@router.get("/api/v1/audit", response_model=AuditQueryResponse)
async def get_audit_log(
    request: Request,
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    event_type: str | None = Query(None),
    action: str | None = Query(None),
    actor_id: str | None = Query(None),
    company_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AuditQueryResponse:
    """Paginated audit log query with filters."""
    pool = request.app.state.pool

    # Build dynamic WHERE clause
    conditions: list[str] = []
    params: list[Any] = []

    if entity_type:
        conditions.append("entity_type = %s")
        params.append(entity_type)
    if entity_id:
        conditions.append("entity_id = %s")
        params.append(entity_id)
    if event_type:
        conditions.append("event_type = %s")
        params.append(event_type)
    if action:
        conditions.append("action = %s")
        params.append(action)
    if actor_id:
        conditions.append("actor_id = %s")
        params.append(actor_id)
    if company_id:
        conditions.append("company_id = %s")
        params.append(company_id)
    if date_from:
        conditions.append("created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= %s")
        params.append(date_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.connection() as conn:
        # Count total
        cur = await conn.execute(
            f"SELECT COUNT(*) FROM core.audit_log {where}", params
        )
        row = await cur.fetchone()
        total = row[0] if row else 0

        # Fetch page
        cur = await conn.execute(
            f"""SELECT audit_id, event_type, entity_type, entity_id, action,
                       actor_id, actor_type, company_id, x_trace_id,
                       before_state, after_state, changed_fields, metadata,
                       created_at
                FROM core.audit_log {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    entries = []
    for r in rows:
        entries.append(AuditEvent(
            audit_id=r[0],
            event_type=r[1],
            entity_type=r[2],
            entity_id=r[3],
            action=r[4],
            actor_id=r[5],
            actor_type=r[6] or "user",
            company_id=r[7] or "",
            x_trace_id=r[8],
            before_state=r[9] if isinstance(r[9], dict) else (json.loads(r[9]) if r[9] else None),
            after_state=r[10] if isinstance(r[10], dict) else (json.loads(r[10]) if r[10] else None),
            changed_fields=list(r[11]) if r[11] else None,
            metadata=r[12] if isinstance(r[12], dict) else (json.loads(r[12]) if r[12] else None),
            created_at=r[13],
        ))

    return AuditQueryResponse(
        entries=entries,
        total=total,
        limit=limit,
        offset=offset,
    )


# ── GET /api/v1/notifications ──────────────────────────────────────────────


@router.get("/api/v1/notifications", response_model=NotificationListResponse)
async def get_notifications(
    request: Request,
    company_id: str = Query(...),
    user_id: str = Query(...),
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> NotificationListResponse:
    """User's notifications (paginated)."""
    notification_service = request.app.state.notification_service
    notifications, total = await notification_service.list_for_user(
        company_id=company_id,
        user_id=user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )

    return NotificationListResponse(
        notifications=[NotificationResponse(**n) for n in notifications],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── POST /api/v1/notifications/{id}/read ──────────────────────────────────


@router.post("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    request: Request,
    notification_id: str,
    user_id: str = Query(...),
) -> dict:
    """Mark a notification as read."""
    notification_service = request.app.state.notification_service
    success = await notification_service.mark_read(notification_id, user_id)
    return {"success": success}


# ── GET /api/v1/notifications/unread-count ─────────────────────────────────


@router.get("/api/v1/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    request: Request,
    company_id: str = Query(...),
    user_id: str = Query(...),
) -> UnreadCountResponse:
    """Unread notification count for badge display."""
    notification_service = request.app.state.notification_service
    count = await notification_service.unread_count(company_id, user_id)
    return UnreadCountResponse(unread_count=count)


# ── GET /metrics ───────────────────────────────────────────────────────────


@router.get("/metrics")
async def get_metrics() -> Response:
    """Prometheus metrics endpoint (no auth — scrape target)."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
