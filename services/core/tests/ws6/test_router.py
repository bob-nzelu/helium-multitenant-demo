"""
Tests for WS6 Observability Router — audit, notifications, metrics endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.observability.router import router


def _create_test_app(mock_pool=None, mock_notification_service=None):
    """Create a minimal FastAPI app with the observability router."""
    app = FastAPI()
    app.include_router(router)

    # Wire up state
    if mock_pool is None:
        mock_pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(0,))
        cursor.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value=cursor)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool.connection = MagicMock(return_value=cm)

    if mock_notification_service is None:
        mock_notification_service = AsyncMock()
        mock_notification_service.list_for_user = AsyncMock(return_value=([], 0))
        mock_notification_service.mark_read = AsyncMock(return_value=True)
        mock_notification_service.unread_count = AsyncMock(return_value=0)

    app.state.pool = mock_pool
    app.state.notification_service = mock_notification_service
    return app


# ── Metrics Endpoint ──────────────────────────────────────────────────────


class TestMetricsEndpoint:
    def test_get_metrics_200(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"] or "text/plain" in resp.headers.get("content-type", "")

    def test_metrics_contains_expected_metrics(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        body = resp.text
        assert "core_http_requests" in body or "core_queue_depth" in body


# ── Audit Endpoint ────────────────────────────────────────────────────────


class TestAuditEndpoint:
    def test_get_audit_empty(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0

    def test_get_audit_with_filters(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?entity_type=invoice&action=CREATE&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 10


# ── Notification Endpoints ────────────────────────────────────────────────


class TestNotificationEndpoints:
    def test_get_notifications_empty(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/notifications?company_id=comp-1&user_id=user-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []
        assert data["total"] == 0

    def test_get_notifications_unread_only(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/notifications?company_id=comp-1&user_id=user-1&unread_only=true"
        )
        assert resp.status_code == 200

    def test_post_mark_read(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.post("/api/v1/notifications/notif-1/read?user_id=user-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_get_unread_count(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/notifications/unread-count?company_id=comp-1&user_id=user-1"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["unread_count"] == 0
