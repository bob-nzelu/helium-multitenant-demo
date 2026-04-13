"""
WS6 Observability Models

Pydantic models for audit events, notifications, and notification reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Audit ──────────────────────────────────────────────────────────────────


class AuditEvent(BaseModel):
    """A single audit log entry."""

    audit_id: str
    event_type: str  # e.g. 'invoice.created', 'pipeline.completed'
    entity_type: str  # 'invoice', 'customer', 'inventory', 'queue', 'system'
    entity_id: str | None = None
    action: str  # 'CREATE', 'UPDATE', 'DELETE', 'FINALIZE', 'TRANSMIT', 'PROCESS'
    actor_id: str | None = None  # helium_user_id or 'system'
    actor_type: str = "user"  # 'user', 'system', 'scheduler'
    company_id: str = ""
    x_trace_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    changed_fields: list[str] | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None


class AuditQueryParams(BaseModel):
    """Query parameters for the GET /audit endpoint."""

    entity_type: str | None = None
    entity_id: str | None = None
    event_type: str | None = None
    action: str | None = None
    actor_id: str | None = None
    company_id: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AuditQueryResponse(BaseModel):
    """Response for paginated audit queries."""

    entries: list[AuditEvent]
    total: int
    limit: int
    offset: int


# ── Notifications ──────────────────────────────────────────────────────────


class Notification(BaseModel):
    """A notification record."""

    notification_id: str
    company_id: str
    recipient_id: str | None = None  # null = broadcast to tenancy
    notification_type: str  # 'system', 'business', 'approval', 'report'
    category: str  # 'upload_complete', 'finalize_complete', 'error', etc.
    title: str
    body: str
    priority: str = "normal"  # 'low', 'normal', 'high', 'urgent'
    data: dict[str, Any] | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class NotificationResponse(BaseModel):
    """Notification with read status for API responses."""

    notification_id: str
    company_id: str
    recipient_id: str | None = None
    notification_type: str
    category: str
    title: str
    body: str
    priority: str = "normal"
    data: dict[str, Any] | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    is_read: bool = False
    read_at: datetime | None = None


class NotificationListResponse(BaseModel):
    """Response for paginated notification queries."""

    notifications: list[NotificationResponse]
    total: int
    limit: int
    offset: int


class UnreadCountResponse(BaseModel):
    """Response for unread notification count."""

    unread_count: int
