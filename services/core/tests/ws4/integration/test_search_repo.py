"""
Integration tests for search_repository against real PostgreSQL.
"""

import pytest

from tests.ws4.integration.helpers import insert_customer, insert_inventory, insert_invoice, needs_pg


def _invoice(**overrides):
    base = {
        "invoice_id": "inv-001",
        "invoice_number": "INV-2026-001",
        "buyer_name": "Test Buyer",
        "total_amount": 50000.00,
        "workflow_status": "COMMITTED",
        "company_id": "tenant-001",
        "direction": "OUTBOUND",
        "document_type": "COMMERCIAL_INVOICE",
        "transaction_type": "B2B",
        "seller_name": "Test Seller",
        "subtotal": 46500,
        "tax_amount": 3500,
    }
    base.update(overrides)
    return base


def _customer(**overrides):
    base = {
        "customer_id": "cust-001",
        "company_name": "Acme Corp",
        "company_name_normalized": "acme corp",
        "tin": "99887766-0001",
        "customer_type": "B2B",
        "compliance_score": 90,
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


def _inventory(**overrides):
    base = {
        "product_id": "prod-001",
        "product_name": "Widget Alpha",
        "product_name_normalized": "WIDGET ALPHA",
        "hsn_code": "8501.10",
        "type": "GOODS",
        "company_id": "tenant-001",
        "vat_rate": 7.5,
    }
    base.update(overrides)
    return base


@needs_pg
@pytest.mark.asyncio
class TestSearchEntity:
    async def test_search_invoices_by_buyer(self, pg_conn):
        from src.data import search_repository

        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-1", buyer_name="Oceanic Traders"
        ))
        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-2", buyer_name="Mountain Supply"
        ))

        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "Oceanic Traders"
        )

        assert count == 1
        assert len(items) == 1
        assert items[0]["invoice_id"] == "inv-1"
        assert "relevance" in items[0]

    async def test_search_customers(self, pg_conn):
        from src.data import search_repository

        await insert_customer(pg_conn, _customer(
            customer_id="cust-1", company_name="Zenith Bank"
        ))
        await insert_customer(pg_conn, _customer(
            customer_id="cust-2", company_name="First Bank"
        ))

        items, count = await search_repository.search_entity(
            pg_conn, "customer", "Zenith Bank"
        )

        assert count == 1
        assert items[0]["customer_id"] == "cust-1"

    async def test_search_inventory(self, pg_conn):
        from src.data import search_repository

        await insert_inventory(pg_conn, _inventory(
            product_id="prod-1", product_name="Hydraulic Pump"
        ))
        await insert_inventory(pg_conn, _inventory(
            product_id="prod-2", product_name="Electric Motor"
        ))

        items, count = await search_repository.search_entity(
            pg_conn, "inventory", "Hydraulic Pump"
        )

        assert count == 1
        assert items[0]["product_id"] == "prod-1"

    async def test_search_excludes_deleted(self, pg_conn):
        from src.data import search_repository

        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-1", buyer_name="Unique Buyer"
        ))
        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-2", buyer_name="Unique Buyer",
            deleted_at="2026-03-15T14:00:00+00:00", deleted_by="x"
        ))

        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "Unique Buyer"
        )

        assert count == 1

    async def test_search_with_pagination(self, pg_conn):
        from src.data import search_repository

        for i in range(5):
            await insert_invoice(pg_conn, _invoice(
                invoice_id=f"inv-{i}", buyer_name="Common Buyer"
            ))

        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "Common Buyer", page=1, per_page=2
        )

        assert count == 5
        assert len(items) == 2

    async def test_invoice_search_with_status_filter(self, pg_conn):
        from src.data import search_repository

        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-1", buyer_name="FilterTest Corp", workflow_status="COMMITTED"
        ))
        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-2", buyer_name="FilterTest Corp", workflow_status="DRAFT"
        ))

        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "FilterTest Corp", status=["COMMITTED"]
        )

        assert count == 1
        assert items[0]["invoice_id"] == "inv-1"

    async def test_invoice_search_with_date_filters(self, pg_conn):
        from src.data import search_repository

        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-1", buyer_name="DateTest Corp"
        ))

        # date_from in far future — should return 0
        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "DateTest Corp",
            date_from="2099-01-01T00:00:00Z",
        )
        assert count == 0

        # date_to in far future — should return 1
        items, count = await search_repository.search_entity(
            pg_conn, "invoice", "DateTest Corp",
            date_to="2099-01-01T00:00:00Z",
        )
        assert count == 1


@needs_pg
@pytest.mark.asyncio
class TestSearchAll:
    async def test_parallel_search_across_entities(self, pg_pool):
        from src.data import search_repository

        # Insert test data using a connection from the pool
        async with pg_pool.connection() as conn:
            from tests.ws4.integration.helpers import TRUNCATE_SQL
            await conn.execute(TRUNCATE_SQL)

            await insert_invoice(conn, _invoice(
                invoice_id="inv-1", buyer_name="Alpha Group"
            ))
            await insert_customer(conn, _customer(
                customer_id="cust-1", company_name="Alpha Group"
            ))
            await insert_inventory(conn, _inventory(
                product_id="prod-1", product_name="Alpha Widget"
            ))

        results = await search_repository.search_all(
            pg_pool, "Alpha",
            entity_types=["invoice", "customer", "inventory"],
        )

        assert "invoice" in results
        assert "customer" in results
        assert "inventory" in results

        inv_items, inv_count = results["invoice"]
        cust_items, cust_count = results["customer"]
        prod_items, prod_count = results["inventory"]

        assert inv_count >= 1
        assert cust_count >= 1
        assert prod_count >= 1
