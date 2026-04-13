"""
Integration tests for inventory_repository against real PostgreSQL.
"""

import pytest

from tests.ws4.integration.helpers import insert_inventory, insert_classification_candidate, needs_pg


def _inventory(**overrides):
    base = {
        "product_id": "prod-001",
        "product_name": "A4 Printing Paper",
        "product_name_normalized": "A4 PRINTING PAPER",
        "helium_sku": "HLM-TEST-00042",
        "hsn_code": "4802.55",
        "type": "GOODS",
        "vat_treatment": "STANDARD",
        "vat_rate": 7.5,
        "product_category": "Paper Products",
        "description": "80gsm A4 paper",
        "classification_source": "PDP",
        "company_id": "tenant-001",
    }
    base.update(overrides)
    return base


@needs_pg
@pytest.mark.asyncio
class TestGetById:
    async def test_returns_with_classification_candidates(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory())
        await insert_classification_candidate(pg_conn, "prod-001", 1, confidence=0.95)
        await insert_classification_candidate(pg_conn, "prod-001", 2, confidence=0.80)

        result = await inventory_repository.get_by_id(pg_conn, "prod-001")

        assert result is not None
        assert result["product_id"] == "prod-001"
        assert result["product_name"] == "A4 Printing Paper"
        assert len(result["classification_candidates"]) == 2
        # Sorted by rank
        assert result["classification_candidates"][0]["rank"] == 1

    async def test_returns_none_for_missing(self, pg_conn):
        from src.data import inventory_repository

        result = await inventory_repository.get_by_id(pg_conn, "nonexistent")
        assert result is None


@needs_pg
@pytest.mark.asyncio
class TestListPaginated:
    async def test_excludes_soft_deleted(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(product_id="prod-1"))
        await insert_inventory(pg_conn, _inventory(
            product_id="prod-2", deleted_at="2026-03-15T14:00:00+00:00"
        ))

        results = await inventory_repository.list_paginated(pg_conn)
        assert len(results) == 1

    async def test_filter_by_type(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(product_id="prod-1", type="GOODS"))
        await insert_inventory(pg_conn, _inventory(product_id="prod-2", type="SERVICE"))

        results = await inventory_repository.list_paginated(pg_conn, type_filter="SERVICE")
        assert len(results) == 1
        assert results[0]["product_id"] == "prod-2"

    async def test_filter_by_vat_treatment(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(product_id="prod-1", vat_treatment="STANDARD"))
        await insert_inventory(pg_conn, _inventory(product_id="prod-2", vat_treatment="EXEMPT"))

        results = await inventory_repository.list_paginated(pg_conn, vat_treatment="EXEMPT")
        assert len(results) == 1

    async def test_pagination(self, pg_conn):
        from src.data import inventory_repository

        for i in range(5):
            await insert_inventory(pg_conn, _inventory(
                product_id=f"prod-{i}", product_name=f"Product {i}"
            ))

        page1 = await inventory_repository.list_paginated(pg_conn, page=1, per_page=2)
        assert len(page1) == 2

    async def test_fts_search(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(
            product_id="prod-1", product_name="Hydraulic Pump",
            description="Industrial hydraulic pump"
        ))
        await insert_inventory(pg_conn, _inventory(
            product_id="prod-2", product_name="A4 Paper",
            description="Office paper"
        ))

        results = await inventory_repository.list_paginated(pg_conn, search="hydraulic pump")
        assert len(results) == 1
        assert results[0]["product_id"] == "prod-1"


@needs_pg
@pytest.mark.asyncio
class TestGetCount:
    async def test_count_excludes_deleted(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(product_id="prod-1"))
        await insert_inventory(pg_conn, _inventory(
            product_id="prod-2", deleted_at="2026-03-15T14:00:00+00:00"
        ))

        count = await inventory_repository.get_count(pg_conn)
        assert count == 1

    async def test_count_with_type_filter(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(product_id="prod-1", type="GOODS"))
        await insert_inventory(pg_conn, _inventory(product_id="prod-2", type="SERVICE"))

        count = await inventory_repository.get_count(pg_conn, type_filter="GOODS")
        assert count == 1


@needs_pg
@pytest.mark.asyncio
class TestUpdateFields:
    async def test_update_product_name_recomputes_normalized(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory())

        result = await inventory_repository.update_fields(
            pg_conn, "prod-001",
            {"product_name": "Premium Paper!"},
            updated_by="user-1",
        )

        assert result is not None
        assert result["product_name"] == "Premium Paper!"
        assert result["product_name_normalized"] == "PREMIUM PAPER"

    async def test_hsn_code_change_sets_manual_source(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(classification_source="PDP"))

        result = await inventory_repository.update_fields(
            pg_conn, "prod-001",
            {"hsn_code": "9999.99"},
            updated_by="user-1",
        )

        assert result["classification_source"] == "MANUAL"

    async def test_update_nonexistent_returns_none(self, pg_conn):
        from src.data import inventory_repository

        result = await inventory_repository.update_fields(
            pg_conn, "nonexistent", {"description": "x"}, updated_by="u"
        )
        assert result is None


@needs_pg
@pytest.mark.asyncio
class TestSoftDeleteAndRecover:
    async def test_soft_delete_and_recover_roundtrip(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory())

        deleted = await inventory_repository.soft_delete(pg_conn, "prod-001")
        assert deleted is not None
        assert deleted["deleted_at"] is not None

        recovered = await inventory_repository.recover(pg_conn, "prod-001")
        assert recovered is not None
        assert recovered["deleted_at"] is None

    async def test_soft_delete_already_deleted(self, pg_conn):
        from src.data import inventory_repository

        await insert_inventory(pg_conn, _inventory(
            deleted_at="2026-03-15T14:00:00+00:00"
        ))

        result = await inventory_repository.soft_delete(pg_conn, "prod-001")
        assert result is None
