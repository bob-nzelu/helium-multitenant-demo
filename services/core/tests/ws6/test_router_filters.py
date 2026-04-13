"""
Additional router tests to cover audit query filter branches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.observability.router import router


def _create_test_app(mock_pool=None):
    app = FastAPI()
    app.include_router(router)

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

    ns = AsyncMock()
    ns.list_for_user = AsyncMock(return_value=([], 0))
    ns.mark_read = AsyncMock(return_value=True)
    ns.unread_count = AsyncMock(return_value=0)

    app.state.pool = mock_pool
    app.state.notification_service = ns
    return app


class TestAuditFilterBranches:
    """Cover each filter branch in the audit endpoint."""

    def test_filter_entity_type(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?entity_type=invoice")
        assert resp.status_code == 200

    def test_filter_entity_id(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?entity_id=inv-123")
        assert resp.status_code == 200

    def test_filter_event_type(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?event_type=invoice.created")
        assert resp.status_code == 200

    def test_filter_action(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?action=CREATE")
        assert resp.status_code == 200

    def test_filter_actor_id(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?actor_id=user-1")
        assert resp.status_code == 200

    def test_filter_company_id(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?company_id=comp-1")
        assert resp.status_code == 200

    def test_filter_date_from(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?date_from=2026-01-01")
        assert resp.status_code == 200

    def test_filter_date_to(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get("/api/v1/audit?date_to=2026-12-31")
        assert resp.status_code == 200

    def test_filter_all_combined(self):
        app = _create_test_app()
        client = TestClient(app)
        resp = client.get(
            "/api/v1/audit?entity_type=invoice&entity_id=inv-1"
            "&event_type=invoice.created&action=CREATE&actor_id=u1"
            "&company_id=c1&date_from=2026-01-01&date_to=2026-12-31"
            "&limit=10&offset=5"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 10
        assert data["offset"] == 5
