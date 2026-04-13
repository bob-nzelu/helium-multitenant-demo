"""
Integration tests for edit_history_repository against real PostgreSQL.
"""

import pytest

from tests.ws4.integration.helpers import insert_customer, insert_inventory, insert_invoice, needs_pg


def _invoice(**overrides):
    base = {
        "invoice_id": "inv-001",
        "invoice_number": "INV-2026-001",
        "buyer_name": "Buyer Inc",
        "payment_status": "UNPAID",
        "notes_to_firs": None,
        "company_id": "tenant-001",
        "direction": "OUTBOUND",
        "document_type": "COMMERCIAL_INVOICE",
        "transaction_type": "B2B",
        "seller_name": "Seller Corp",
        "subtotal": 100000,
        "tax_amount": 7500,
        "total_amount": 107500,
    }
    base.update(overrides)
    return base


@needs_pg
@pytest.mark.asyncio
class TestWriteFieldChanges:
    async def test_writes_changed_field_to_db(self, pg_conn):
        from src.data import edit_history_repository

        await insert_invoice(pg_conn, _invoice())

        current = {"payment_status": "UNPAID", "notes_to_firs": None}
        new_fields = {"payment_status": "PAID"}

        changed = await edit_history_repository.write_field_changes(
            pg_conn,
            entity_type="invoice",
            entity_id="inv-001",
            current_record=current,
            new_fields=new_fields,
            changed_by="user-1",
        )

        assert changed == ["payment_status"]

        # Verify in DB
        cur = await pg_conn.execute(
            "SELECT * FROM invoices.invoice_edit_history WHERE invoice_id = %s",
            ("inv-001",),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1

    async def test_skips_unchanged_fields(self, pg_conn):
        from src.data import edit_history_repository

        await insert_invoice(pg_conn, _invoice())

        current = {"payment_status": "UNPAID"}
        new_fields = {"payment_status": "UNPAID"}

        changed = await edit_history_repository.write_field_changes(
            pg_conn,
            entity_type="invoice",
            entity_id="inv-001",
            current_record=current,
            new_fields=new_fields,
            changed_by="user-1",
        )

        assert changed == []

    async def test_writes_to_customer_history_table(self, pg_conn):
        from src.data import edit_history_repository

        await insert_customer(pg_conn, {
            "customer_id": "cust-001",
            "company_name": "Old Name",
            "company_name_normalized": "old name",
            "company_id": "tenant-001",
        })

        changed = await edit_history_repository.write_field_changes(
            pg_conn,
            entity_type="customer",
            entity_id="cust-001",
            current_record={"company_name": "Old Name"},
            new_fields={"company_name": "New Name"},
            changed_by="user-1",
            change_reason="Rebranding",
        )

        assert changed == ["company_name"]

        cur = await pg_conn.execute(
            "SELECT change_reason FROM customers.customer_edit_history WHERE customer_id = %s",
            ("cust-001",),
        )
        row = await cur.fetchone()
        assert row[0] == "Rebranding"

    async def test_writes_to_inventory_history_table(self, pg_conn):
        from src.data import edit_history_repository

        await insert_inventory(pg_conn, {
            "product_id": "prod-001",
            "product_name": "Widget",
            "product_name_normalized": "WIDGET",
            "company_id": "tenant-001",
            "type": "GOODS",
            "vat_rate": 7.5,
        })

        changed = await edit_history_repository.write_field_changes(
            pg_conn,
            entity_type="inventory",
            entity_id="prod-001",
            current_record={"description": None},
            new_fields={"description": "Heavy duty widget"},
            changed_by="user-1",
        )

        assert changed == ["description"]

    async def test_null_to_value_recorded(self, pg_conn):
        from src.data import edit_history_repository

        await insert_invoice(pg_conn, _invoice())

        changed = await edit_history_repository.write_field_changes(
            pg_conn,
            entity_type="invoice",
            entity_id="inv-001",
            current_record={"notes_to_firs": None},
            new_fields={"notes_to_firs": "Important note"},
            changed_by="user-1",
        )

        assert changed == ["notes_to_firs"]

        cur = await pg_conn.execute(
            "SELECT old_value, new_value FROM invoices.invoice_edit_history WHERE invoice_id = %s AND field_name = %s",
            ("inv-001", "notes_to_firs"),
        )
        row = await cur.fetchone()
        assert row[0] is None  # old_value was NULL
        assert row[1] == "Important note"
