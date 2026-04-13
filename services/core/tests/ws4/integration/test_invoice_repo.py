"""
Integration tests for invoice_repository against real PostgreSQL.
"""

import pytest

from tests.ws4.integration.helpers import insert_invoice, insert_invoice_line_item, needs_pg

# Test data factory
def _invoice(**overrides):
    base = {
        "invoice_id": "inv-001",
        "helium_invoice_no": "WM-TEST-A3F7B2C9",
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


@needs_pg
@pytest.mark.asyncio
class TestGetById:
    async def test_returns_invoice_with_children(self, pg_conn):
        from src.data import invoice_repository

        data = _invoice()
        await insert_invoice(pg_conn, data)
        await insert_invoice_line_item(pg_conn, "inv-001", 1, description="Widget A")
        await insert_invoice_line_item(pg_conn, "inv-001", 2, description="Widget B")

        result = await invoice_repository.get_by_id(pg_conn, "inv-001")

        assert result is not None
        assert result["invoice_id"] == "inv-001"
        assert result["buyer_name"] == "Buyer Inc"
        assert len(result["line_items"]) == 2
        assert result["line_items"][0]["description"] == "Widget A"
        assert isinstance(result["tax_categories"], list)
        assert isinstance(result["attachments"], list)
        assert isinstance(result["references"], list)
        assert isinstance(result["allowance_charges"], list)

    async def test_returns_none_for_missing(self, pg_conn):
        from src.data import invoice_repository

        result = await invoice_repository.get_by_id(pg_conn, "nonexistent")
        assert result is None

    async def test_returns_soft_deleted(self, pg_conn):
        from src.data import invoice_repository

        data = _invoice(deleted_at="2026-03-15T14:00:00+00:00", deleted_by="user-1")
        await insert_invoice(pg_conn, data)

        result = await invoice_repository.get_by_id(pg_conn, "inv-001")
        assert result is not None
        assert result["deleted_at"] is not None


@needs_pg
@pytest.mark.asyncio
class TestListPaginated:
    async def test_excludes_soft_deleted(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1"))
        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-2",
            deleted_at="2026-03-15T14:00:00+00:00",
            deleted_by="user-1",
        ))

        results = await invoice_repository.list_paginated(pg_conn)
        assert len(results) == 1
        assert results[0]["invoice_id"] == "inv-1"

    async def test_pagination(self, pg_conn):
        from src.data import invoice_repository

        for i in range(5):
            await insert_invoice(pg_conn, _invoice(
                invoice_id=f"inv-{i}",
                invoice_number=f"INV-{i}",
            ))

        page1 = await invoice_repository.list_paginated(pg_conn, page=1, per_page=2)
        page2 = await invoice_repository.list_paginated(pg_conn, page=2, per_page=2)

        assert len(page1) == 2
        assert len(page2) == 2

    async def test_filter_by_status(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", workflow_status="COMMITTED"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", workflow_status="DRAFT"))

        results = await invoice_repository.list_paginated(
            pg_conn, status=["COMMITTED"]
        )
        assert len(results) == 1
        assert results[0]["invoice_id"] == "inv-1"

    async def test_filter_by_direction(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", direction="OUTBOUND"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", direction="INBOUND"))

        results = await invoice_repository.list_paginated(pg_conn, direction="INBOUND")
        assert len(results) == 1
        assert results[0]["invoice_id"] == "inv-2"

    async def test_filter_by_document_type(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", document_type="COMMERCIAL_INVOICE"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", document_type="CREDIT_NOTE"))

        results = await invoice_repository.list_paginated(pg_conn, document_type="CREDIT_NOTE")
        assert len(results) == 1

    async def test_sort_order_asc(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-a", invoice_number="AAA"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-b", invoice_number="ZZZ"))

        results = await invoice_repository.list_paginated(
            pg_conn, sort_by="invoice_number", sort_order="asc"
        )
        assert results[0]["invoice_number"] == "AAA"

    async def test_filter_by_transaction_type(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", transaction_type="B2B"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", transaction_type="B2G"))

        results = await invoice_repository.list_paginated(pg_conn, transaction_type="B2G")
        assert len(results) == 1
        assert results[0]["invoice_id"] == "inv-2"

    async def test_filter_by_date_range(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1"))
        # Use date_from in the future to exclude all
        results = await invoice_repository.list_paginated(
            pg_conn, date_from="2099-01-01T00:00:00Z"
        )
        assert len(results) == 0

        # Use date_to in the future to include all
        results = await invoice_repository.list_paginated(
            pg_conn, date_to="2099-01-01T00:00:00Z"
        )
        assert len(results) == 1

    async def test_fts_search(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", buyer_name="Global Traders"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", buyer_name="Local Shop"))

        results = await invoice_repository.list_paginated(pg_conn, search="Global Traders")
        assert len(results) == 1
        assert results[0]["invoice_id"] == "inv-1"


@needs_pg
@pytest.mark.asyncio
class TestGetCount:
    async def test_count_excludes_deleted(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1"))
        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-2", deleted_at="2026-03-15T14:00:00+00:00", deleted_by="x"
        ))

        count = await invoice_repository.get_count(pg_conn)
        assert count == 1

    async def test_count_with_status_filter(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", workflow_status="COMMITTED"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-2", workflow_status="DRAFT"))
        await insert_invoice(pg_conn, _invoice(invoice_id="inv-3", workflow_status="COMMITTED"))

        count = await invoice_repository.get_count(pg_conn, status=["COMMITTED"])
        assert count == 2

    async def test_count_with_all_filters(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(
            invoice_id="inv-1", direction="OUTBOUND",
            document_type="COMMERCIAL_INVOICE", transaction_type="B2B",
        ))

        count = await invoice_repository.get_count(
            pg_conn, direction="OUTBOUND", document_type="COMMERCIAL_INVOICE",
            transaction_type="B2B", date_from="2020-01-01", date_to="2099-01-01"
        )
        assert count == 1

    async def test_count_with_search(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(invoice_id="inv-1", buyer_name="Unique Buyer XYZ"))

        count = await invoice_repository.get_count(pg_conn, search="Unique Buyer XYZ")
        assert count == 1


@needs_pg
@pytest.mark.asyncio
class TestUpdateFields:
    async def test_update_single_field(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice())

        result = await invoice_repository.update_fields(
            pg_conn, "inv-001", {"payment_status": "PAID"}, updated_by="user-1"
        )

        assert result is not None
        assert result["payment_status"] == "PAID"
        assert result["updated_by"] == "user-1"

    async def test_update_nonexistent_returns_none(self, pg_conn):
        from src.data import invoice_repository

        result = await invoice_repository.update_fields(
            pg_conn, "nonexistent", {"payment_status": "PAID"}, updated_by="user-1"
        )
        assert result is None

    async def test_update_multiple_fields(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice())

        result = await invoice_repository.update_fields(
            pg_conn, "inv-001",
            {"payment_status": "PARTIAL", "notes_to_firs": "Test note"},
            updated_by="user-2",
        )

        assert result["payment_status"] == "PARTIAL"
        assert result["notes_to_firs"] == "Test note"


@needs_pg
@pytest.mark.asyncio
class TestSoftDelete:
    async def test_soft_delete_sets_timestamps(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice())

        result = await invoice_repository.soft_delete(pg_conn, "inv-001", deleted_by="user-1")

        assert result is not None
        assert result["invoice_id"] == "inv-001"
        assert result["deleted_at"] is not None

    async def test_soft_delete_already_deleted_returns_none(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(
            deleted_at="2026-03-15T14:00:00+00:00", deleted_by="user-1"
        ))

        result = await invoice_repository.soft_delete(pg_conn, "inv-001", deleted_by="user-2")
        assert result is None

    async def test_soft_delete_nonexistent_returns_none(self, pg_conn):
        from src.data import invoice_repository

        result = await invoice_repository.soft_delete(pg_conn, "nonexistent", deleted_by="user-1")
        assert result is None


@needs_pg
@pytest.mark.asyncio
class TestRecover:
    async def test_recover_clears_deleted_fields(self, pg_conn):
        from src.data import invoice_repository

        await insert_invoice(pg_conn, _invoice(
            deleted_at="2026-03-15T14:00:00+00:00", deleted_by="user-1"
        ))

        result = await invoice_repository.recover(pg_conn, "inv-001")

        assert result is not None
        assert result["deleted_at"] is None
        assert result["deleted_by"] is None

    async def test_recover_nonexistent_returns_none(self, pg_conn):
        from src.data import invoice_repository

        result = await invoice_repository.recover(pg_conn, "nonexistent")
        assert result is None
