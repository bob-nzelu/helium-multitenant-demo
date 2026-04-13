"""
Tests for WS6 EntityAuditMiddleware — HTTP-level entity CRUD audit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.observability.audit_middleware import EntityAuditMiddleware


def _create_app(mock_audit_logger=None):
    """Create a test app with the entity audit middleware and dummy endpoints."""
    app = FastAPI()
    app.add_middleware(EntityAuditMiddleware)

    # Dummy entity endpoints
    @app.put("/api/v1/invoices/{invoice_id}")
    async def update_invoice(invoice_id: str):
        return {"id": invoice_id, "updated": True}

    @app.delete("/api/v1/customers/{customer_id}")
    async def delete_customer(customer_id: str):
        return {"id": customer_id, "deleted": True}

    @app.patch("/api/v1/inventory/{product_id}")
    async def patch_inventory(product_id: str):
        return {"id": product_id, "patched": True}

    @app.post("/api/v1/search")
    async def search():
        return {"results": []}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    # Wire audit logger
    if mock_audit_logger is None:
        mock_audit_logger = AsyncMock()
        mock_audit_logger.log = AsyncMock(return_value="audit-id")
    app.state.audit_logger = mock_audit_logger
    return app, mock_audit_logger


class TestEntityAuditMiddleware:
    def test_put_logs_update_event(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.put("/api/v1/invoices/inv-123")
        assert resp.status_code == 200
        mock_al.log.assert_called()
        call_kwargs = mock_al.log.call_args.kwargs
        assert call_kwargs["event_type"] == "invoice.updated"
        assert call_kwargs["entity_id"] == "inv-123"
        assert call_kwargs["action"] == "UPDATE"

    def test_delete_logs_deleted_event(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.delete("/api/v1/customers/cust-456")
        assert resp.status_code == 200
        mock_al.log.assert_called()
        call_kwargs = mock_al.log.call_args.kwargs
        assert call_kwargs["event_type"] == "customer.deleted"
        assert call_kwargs["action"] == "DELETE"

    def test_patch_logs_update_event(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.patch("/api/v1/inventory/prod-789")
        assert resp.status_code == 200
        call_kwargs = mock_al.log.call_args.kwargs
        assert call_kwargs["event_type"] == "inventory.updated"

    def test_search_logs_search_executed(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.post("/api/v1/search")
        assert resp.status_code == 200
        # Should log search.executed
        calls = mock_al.log.call_args_list
        search_calls = [c for c in calls if c.kwargs.get("event_type") == "search.executed"]
        assert len(search_calls) == 1

    def test_get_skips_audit(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        mock_al.log.assert_not_called()

    def test_non_entity_post_skips_audit(self):
        """POST to a non-entity endpoint should not audit."""
        app, mock_al = _create_app()
        # health is GET-only, but any POST to unknown path should be skipped
        client = TestClient(app)
        # This will 405 or 404, but audit should not fire
        resp = client.post("/api/v1/health")
        mock_al.log.assert_not_called()

    def test_extracts_company_from_header(self):
        app, mock_al = _create_app()
        client = TestClient(app)
        resp = client.put(
            "/api/v1/invoices/inv-1",
            headers={"x-company-id": "comp-abc", "x-user-id": "user-xyz"},
        )
        assert resp.status_code == 200
        call_kwargs = mock_al.log.call_args.kwargs
        assert call_kwargs["company_id"] == "comp-abc"
        assert call_kwargs["actor_id"] == "user-xyz"

    def test_audit_failure_does_not_block_response(self):
        """If audit logger raises, the response should still succeed."""
        mock_al = AsyncMock()
        mock_al.log = AsyncMock(side_effect=Exception("DB down"))
        app, _ = _create_app(mock_al)
        client = TestClient(app)
        resp = client.put("/api/v1/invoices/inv-1")
        assert resp.status_code == 200  # Response still works
