"""
Integration tests for customer_repository against real PostgreSQL.
"""

import pytest

from tests.ws4.integration.helpers import (
    insert_customer,
    insert_customer_branch,
    insert_customer_contact,
    needs_pg,
)


def _customer(**overrides):
    base = {
        "customer_id": "cust-001",
        "company_name": "Global Traders Ltd",
        "company_name_normalized": "global traders ltd",
        "customer_code": "CUST-0042",
        "tin": "12345678-0001",
        "rc_number": "RC1234567890123",
        "trading_name": "GlobalTrade",
        "customer_type": "B2B",
        "tax_classification": "STANDARD",
        "state": "Lagos",
        "city": "Lagos",
        "compliance_score": 85,
        "total_invoices": 47,
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


@needs_pg
@pytest.mark.asyncio
class TestGetById:
    async def test_returns_customer_with_children(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer())
        await insert_customer_branch(pg_conn, "cust-001", "branch-001")
        await insert_customer_contact(pg_conn, "cust-001")

        result = await customer_repository.get_by_id(pg_conn, "cust-001")

        assert result is not None
        assert result["customer_id"] == "cust-001"
        assert result["company_name"] == "Global Traders Ltd"
        assert len(result["branches"]) == 1
        assert len(result["contacts"]) == 1

    async def test_returns_none_for_missing(self, pg_conn):
        from src.data import customer_repository

        result = await customer_repository.get_by_id(pg_conn, "nonexistent")
        assert result is None


@needs_pg
@pytest.mark.asyncio
class TestListPaginated:
    async def test_excludes_soft_deleted(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1"))
        await insert_customer(pg_conn, _customer(
            customer_id="cust-2", deleted_at="2026-03-15T14:00:00+00:00"
        ))

        results = await customer_repository.list_paginated(pg_conn)
        assert len(results) == 1

    async def test_filter_by_customer_type(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1", customer_type="B2B"))
        await insert_customer(pg_conn, _customer(customer_id="cust-2", customer_type="B2G"))

        results = await customer_repository.list_paginated(pg_conn, customer_type="B2G")
        assert len(results) == 1
        assert results[0]["customer_id"] == "cust-2"

    async def test_filter_by_state(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1", state="Lagos"))
        await insert_customer(pg_conn, _customer(customer_id="cust-2", state="Abuja"))

        results = await customer_repository.list_paginated(pg_conn, state="Abuja")
        assert len(results) == 1

    async def test_filter_by_compliance_min(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1", compliance_score=90))
        await insert_customer(pg_conn, _customer(customer_id="cust-2", compliance_score=50))

        results = await customer_repository.list_paginated(pg_conn, compliance_min=80)
        assert len(results) == 1
        assert results[0]["customer_id"] == "cust-1"

    async def test_pagination(self, pg_conn):
        from src.data import customer_repository

        for i in range(5):
            await insert_customer(pg_conn, _customer(
                customer_id=f"cust-{i}", customer_code=f"CODE-{i}"
            ))

        page1 = await customer_repository.list_paginated(pg_conn, page=1, per_page=2)
        assert len(page1) == 2

    async def test_fts_search(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(
            customer_id="cust-1", company_name="Acme Corporation"
        ))
        await insert_customer(pg_conn, _customer(
            customer_id="cust-2", company_name="Zeta Industries"
        ))

        results = await customer_repository.list_paginated(pg_conn, search="Acme Corporation")
        assert len(results) == 1
        assert results[0]["customer_id"] == "cust-1"


@needs_pg
@pytest.mark.asyncio
class TestGetCount:
    async def test_count_excludes_deleted(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1"))
        await insert_customer(pg_conn, _customer(
            customer_id="cust-2", deleted_at="2026-03-15T14:00:00+00:00"
        ))

        count = await customer_repository.get_count(pg_conn)
        assert count == 1

    async def test_count_with_filter(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(customer_id="cust-1", customer_type="B2B"))
        await insert_customer(pg_conn, _customer(customer_id="cust-2", customer_type="B2G"))

        count = await customer_repository.get_count(pg_conn, customer_type="B2B")
        assert count == 1


@needs_pg
@pytest.mark.asyncio
class TestUpdateFields:
    async def test_update_company_name_recomputes_normalized(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer())

        result = await customer_repository.update_fields(
            pg_conn, "cust-001",
            {"company_name": "New Trading Co."},
            updated_by="user-1",
        )

        assert result is not None
        assert result["company_name"] == "New Trading Co."
        assert result["company_name_normalized"] == "new trading co"

    async def test_update_nonexistent_returns_none(self, pg_conn):
        from src.data import customer_repository

        result = await customer_repository.update_fields(
            pg_conn, "nonexistent", {"city": "Abuja"}, updated_by="user-1"
        )
        assert result is None


@needs_pg
@pytest.mark.asyncio
class TestSoftDeleteAndRecover:
    async def test_soft_delete_and_recover_roundtrip(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer())

        # Delete
        deleted = await customer_repository.soft_delete(pg_conn, "cust-001")
        assert deleted is not None
        assert deleted["deleted_at"] is not None

        # Recover
        recovered = await customer_repository.recover(pg_conn, "cust-001")
        assert recovered is not None
        assert recovered["deleted_at"] is None

    async def test_soft_delete_already_deleted(self, pg_conn):
        from src.data import customer_repository

        await insert_customer(pg_conn, _customer(deleted_at="2026-03-15T14:00:00+00:00"))

        result = await customer_repository.soft_delete(pg_conn, "cust-001")
        assert result is None
