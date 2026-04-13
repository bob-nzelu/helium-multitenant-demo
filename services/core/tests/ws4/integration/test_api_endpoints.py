"""
Integration tests for WS4 API endpoints against real PostgreSQL.

Uses httpx AsyncClient with FastAPI app wired to real DB pool + mock SSE.
"""

import asyncio
import sys

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from tests.ws4.integration.helpers import (
    MockSSEManager,
    insert_customer,
    insert_invoice,
    insert_inventory,
    needs_pg,
    TRUNCATE_SQL,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _create_test_app(pool, sse_manager, jwt_claims=None):
    """Create a FastAPI app wired to real pool for testing."""
    from src.api.entities import router as entities_router
    from src.api.invoices import router as invoices_router
    from src.api.customers import router as customers_router
    from src.api.inventory import router as inventory_router
    from src.api.search import router as search_router
    from src.errors import CoreError

    app = FastAPI()
    app.state.pool = pool
    app.state.sse_manager = sse_manager

    # Inject JWT claims middleware
    @app.middleware("http")
    async def inject_jwt(request: Request, call_next):
        if jwt_claims:
            request.state.jwt_claims = jwt_claims
        return await call_next(request)

    # Error handler
    @app.exception_handler(CoreError)
    async def handle_core_error(request: Request, exc: CoreError):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    app.include_router(entities_router)
    app.include_router(invoices_router)
    app.include_router(customers_router)
    app.include_router(inventory_router)
    app.include_router(search_router)

    return app


def _invoice(**overrides):
    base = {
        "invoice_id": "inv-001",
        "helium_invoice_no": "WM-TEST-001",
        "invoice_number": "INV-2026-001",
        "irn": "IRN-001",
        "direction": "OUTBOUND",
        "document_type": "COMMERCIAL_INVOICE",
        "transaction_type": "B2B",
        "issue_date": "2026-03-15",
        "due_date": "2026-04-14",
        "workflow_status": "COMMITTED",
        "transmission_status": "NOT_REQUIRED",
        "payment_status": "UNPAID",
        "seller_name": "Test Corp",
        "buyer_name": "Buyer Inc",
        "subtotal": 100000.00,
        "tax_amount": 7500.00,
        "total_amount": 107500.00,
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


def _customer(**overrides):
    base = {
        "customer_id": "cust-001",
        "company_name": "Global Traders",
        "company_name_normalized": "global traders",
        "customer_code": "CUST-0042",
        "tin": "12345678-0001",
        "customer_type": "B2B",
        "state": "Lagos",
        "city": "Lagos",
        "compliance_score": 85,
        "total_invoices": 47,
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


def _inventory(**overrides):
    base = {
        "product_id": "prod-001",
        "product_name": "A4 Paper",
        "product_name_normalized": "A4 PAPER",
        "helium_sku": "HLM-001",
        "hsn_code": "4802.55",
        "type": "GOODS",
        "vat_treatment": "STANDARD",
        "vat_rate": 7.5,
        "product_category": "Paper",
        "description": "80gsm paper",
        "classification_source": "PDP",
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


ADMIN_CLAIMS = {
    "sub": "admin-001",
    "company_id": "tenant-001",
    "permissions": ["system.admin"],
}

INVOICE_CLAIMS = {
    "sub": "user-001",
    "company_id": "tenant-001",
    "permissions": ["invoice.update", "invoice.delete"],
}

READONLY_CLAIMS = {
    "sub": "readonly-001",
    "company_id": "tenant-001",
    "permissions": [],
}


# ── Invoice Read Endpoints ─────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestGetInvoice:
    async def test_get_invoice_returns_200(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/invoice/inv-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["invoice_id"] == "inv-001"
        assert "line_items" in data

    async def test_get_invoice_not_found_404(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/invoice/nonexistent")

        assert resp.status_code == 404

    async def test_get_deleted_invoice_returns_410(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice(
                deleted_at="2026-03-15T14:00:00+00:00", deleted_by="user-1"
            ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/invoice/inv-001")

        assert resp.status_code == 410
        data = resp.json()
        assert data["error"] == "ENTITY_DELETED"


@needs_pg
@pytest.mark.asyncio
class TestListInvoices:
    async def test_list_returns_paginated(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            for i in range(3):
                await insert_invoice(conn, _invoice(
                    invoice_id=f"inv-{i}", invoice_number=f"INV-{i}"
                ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/invoices?per_page=2")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 3
        assert len(data["items"]) == 2
        assert data["has_next"] is True

    async def test_search_too_short_returns_400(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/invoices?search=a")

        assert resp.status_code == 400


# ── Customer Read Endpoints ────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestGetCustomer:
    async def test_get_customer_returns_200(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_customer(conn, _customer())

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/customer/cust-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["customer_id"] == "cust-001"

    async def test_get_customer_not_found_404(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/customer/nonexistent")

        assert resp.status_code == 404


# ── Inventory Read Endpoints ───────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestGetInventory:
    async def test_get_inventory_returns_200(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_inventory(conn, _inventory())

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/inventory/prod-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["product_id"] == "prod-001"


# ── Entity Update Endpoint ─────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestUpdateEntity:
    async def test_update_invoice_field(self, pg_pool):
        sse = MockSSEManager()
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, sse, INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={"payment_status": "PAID"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["payment_status"] == "PAID"
        assert len(sse.events) == 1
        assert sse.events[0].event_type == "invoice.updated"

    async def test_update_not_found_returns_404(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/nonexistent",
                json={"payment_status": "PAID"},
            )

        assert resp.status_code == 404

    async def test_update_forbidden_without_permission(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, MockSSEManager(), READONLY_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={"payment_status": "PAID"},
            )

        assert resp.status_code == 403

    async def test_update_invalid_entity_type(self, pg_pool):
        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/bogus/id-001",
                json={"field": "value"},
            )

        assert resp.status_code == 400

    async def test_recover_soft_deleted_entity(self, pg_pool):
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat()
        sse = MockSSEManager()
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice(
                deleted_at=recent, deleted_by="user-1"
            ))

        app = _create_test_app(pg_pool, sse, INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={"recover": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_at"] is None


# ── Entity Delete Endpoint ─────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestDeleteEntity:
    async def test_soft_delete_invoice(self, pg_pool):
        sse = MockSSEManager()
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, sse, INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/v1/entity/invoice/inv-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        assert data["entity_type"] == "invoice"
        assert "recovery_until" in data
        assert len(sse.events) == 1
        assert sse.events[0].event_type == "invoice.deleted"

    async def test_delete_already_deleted_returns_410(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice(
                deleted_at="2026-03-15T14:00:00+00:00", deleted_by="user-1"
            ))

        app = _create_test_app(pg_pool, MockSSEManager(), INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/v1/entity/invoice/inv-001")

        assert resp.status_code == 410

    async def test_delete_not_found_returns_404(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/v1/entity/invoice/nonexistent")

        assert resp.status_code == 404

    async def test_delete_forbidden_without_permission(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, MockSSEManager(), READONLY_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/v1/entity/invoice/inv-001")

        assert resp.status_code == 403


# ── Search Endpoint ────────────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestSearchEndpoint:
    async def test_cross_entity_search(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice(
                invoice_id="inv-1", buyer_name="SearchTarget Corp"
            ))
            await insert_customer(conn, _customer(
                customer_id="cust-1", company_name="SearchTarget Corp"
            ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/search",
                json={"query": "SearchTarget Corp"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "SearchTarget Corp"
        assert "invoices" in data["results"]
        assert "customers" in data["results"]


# ── Customer List Endpoint ─────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestListCustomers:
    async def test_list_returns_paginated(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            for i in range(3):
                await insert_customer(conn, _customer(
                    customer_id=f"cust-{i}", customer_code=f"CODE-{i}"
                ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/customers?per_page=2")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 3
        assert len(data["items"]) == 2

    async def test_search_too_short_returns_400(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/customers?search=x")

        assert resp.status_code == 400

    async def test_get_deleted_customer_returns_410(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_customer(conn, _customer(
                deleted_at="2026-03-15T14:00:00+00:00"
            ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/customer/cust-001")

        assert resp.status_code == 410


# ── Inventory List Endpoint ────────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestListInventories:
    async def test_list_returns_paginated(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            for i in range(3):
                await insert_inventory(conn, _inventory(
                    product_id=f"prod-{i}", product_name=f"Product {i}"
                ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/inventories?per_page=2")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 3
        assert len(data["items"]) == 2

    async def test_search_too_short_returns_400(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/inventories?search=x")

        assert resp.status_code == 400

    async def test_get_deleted_inventory_returns_410(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_inventory(conn, _inventory(
                deleted_at="2026-03-15T14:00:00+00:00"
            ))

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/inventory/prod-001")

        assert resp.status_code == 410

    async def test_inventory_not_found_404(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)

        app = _create_test_app(pg_pool, MockSSEManager(), ADMIN_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/inventory/nonexistent")

        assert resp.status_code == 404


# ── Entity Update Edge Cases ───────────────────────────────────────────


@needs_pg
@pytest.mark.asyncio
class TestEntityUpdateEdgeCases:
    async def test_update_with_no_field_changes_returns_current(self, pg_pool):
        """Empty body (no field updates, no recover) should return current record."""
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, MockSSEManager(), INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={},
            )

        assert resp.status_code == 200

    async def test_update_unchanged_value_returns_current(self, pg_pool):
        """Updating to same value should not emit SSE event."""
        sse = MockSSEManager()
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice(payment_status="UNPAID"))

        app = _create_test_app(pg_pool, sse, INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={"payment_status": "UNPAID"},
            )

        assert resp.status_code == 200
        # No SSE event when value unchanged
        assert len(sse.events) == 0

    async def test_recover_not_deleted_returns_error(self, pg_pool):
        """Recovering a non-deleted entity should return 422."""
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_invoice(conn, _invoice())

        app = _create_test_app(pg_pool, MockSSEManager(), INVOICE_CLAIMS)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/v1/entity/invoice/inv-001",
                json={"recover": True},
            )

        assert resp.status_code == 422

    async def test_delete_customer_entity(self, pg_pool):
        """Test soft delete for customer entity type (non-invoice path)."""
        sse = MockSSEManager()
        async with pg_pool.connection() as conn:
            await conn.execute(TRUNCATE_SQL)
            await insert_customer(conn, _customer())

        customer_claims = {
            "sub": "user-001", "company_id": "tenant-001",
            "permissions": ["customer.update", "customer.delete"],
        }
        app = _create_test_app(pg_pool, sse, customer_claims)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/v1/entity/customer/cust-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_type"] == "customer"
        assert sse.events[0].event_type == "customer.deleted"
