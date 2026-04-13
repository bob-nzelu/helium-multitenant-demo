"""
Integration tests for cleanup_deleted job against real PostgreSQL.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from tests.ws4.integration.helpers import insert_customer, insert_inventory, insert_invoice, needs_pg


def _invoice(**overrides):
    base = {
        "invoice_id": "inv-001",
        "invoice_number": "INV-001",
        "buyer_name": "Buyer",
        "seller_name": "Seller",
        "direction": "OUTBOUND",
        "document_type": "COMMERCIAL_INVOICE",
        "transaction_type": "B2B",
        "subtotal": 100000,
        "tax_amount": 7500,
        "total_amount": 107500,
        "workflow_status": "COMMITTED",
        "payment_status": "UNPAID",
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


def _customer(**overrides):
    base = {
        "customer_id": "cust-001",
        "company_name": "Test Customer",
        "company_name_normalized": "test customer",
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


def _inventory(**overrides):
    base = {
        "product_id": "prod-001",
        "product_name": "Test Product",
        "product_name_normalized": "TEST PRODUCT",
        "type": "GOODS",
        "vat_rate": 7.5,
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


@needs_pg
@pytest.mark.asyncio
class TestCleanupExpiredSoftDeletes:
    async def test_deletes_expired_records(self, pg_pool):
        from src.jobs.cleanup_deleted import cleanup_expired_soft_deletes

        # Insert records deleted more than 24 hours ago
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

        async with pg_pool.connection() as conn:
            from tests.ws4.integration.helpers import TRUNCATE_SQL
            await conn.execute(TRUNCATE_SQL)

            await insert_invoice(conn, _invoice(
                invoice_id="inv-expired",
                deleted_at=expired_time,
                deleted_by="user-1",
            ))
            await insert_invoice(conn, _invoice(invoice_id="inv-active"))

        await cleanup_expired_soft_deletes(pg_pool)

        # Verify expired is gone, active remains
        async with pg_pool.connection() as conn:
            cur = await conn.execute(
                "SELECT invoice_id FROM invoices.invoices"
            )
            rows = await cur.fetchall()
            ids = [r[0] for r in rows]
            assert "inv-expired" not in ids
            assert "inv-active" in ids

    async def test_preserves_recently_deleted(self, pg_pool):
        from src.jobs.cleanup_deleted import cleanup_expired_soft_deletes

        # Deleted 1 hour ago — within recovery window
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        async with pg_pool.connection() as conn:
            from tests.ws4.integration.helpers import TRUNCATE_SQL
            await conn.execute(TRUNCATE_SQL)

            await insert_invoice(conn, _invoice(
                invoice_id="inv-recent",
                deleted_at=recent_time,
                deleted_by="user-1",
            ))

        await cleanup_expired_soft_deletes(pg_pool)

        async with pg_pool.connection() as conn:
            cur = await conn.execute(
                "SELECT invoice_id FROM invoices.invoices WHERE invoice_id = %s",
                ("inv-recent",),
            )
            row = await cur.fetchone()
            assert row is not None

    async def test_cleans_all_entity_types(self, pg_pool):
        from src.jobs.cleanup_deleted import cleanup_expired_soft_deletes

        expired_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()

        async with pg_pool.connection() as conn:
            from tests.ws4.integration.helpers import TRUNCATE_SQL
            await conn.execute(TRUNCATE_SQL)

            await insert_invoice(conn, _invoice(
                invoice_id="inv-exp", deleted_at=expired_time, deleted_by="x"
            ))
            await insert_customer(conn, _customer(
                customer_id="cust-exp", deleted_at=expired_time
            ))
            await insert_inventory(conn, _inventory(
                product_id="prod-exp", deleted_at=expired_time
            ))

        await cleanup_expired_soft_deletes(pg_pool)

        async with pg_pool.connection() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM invoices.invoices")
            assert (await cur.fetchone())[0] == 0

            cur = await conn.execute("SELECT COUNT(*) FROM customers.customers")
            assert (await cur.fetchone())[0] == 0

            cur = await conn.execute("SELECT COUNT(*) FROM inventory.inventory")
            assert (await cur.fetchone())[0] == 0

    async def test_no_expired_records_is_noop(self, pg_pool):
        from src.jobs.cleanup_deleted import cleanup_expired_soft_deletes

        async with pg_pool.connection() as conn:
            from tests.ws4.integration.helpers import TRUNCATE_SQL
            await conn.execute(TRUNCATE_SQL)

            await insert_invoice(conn, _invoice(invoice_id="inv-active"))

        # Should complete without error
        await cleanup_expired_soft_deletes(pg_pool)

        async with pg_pool.connection() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM invoices.invoices")
            assert (await cur.fetchone())[0] == 1
