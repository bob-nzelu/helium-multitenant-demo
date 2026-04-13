"""Tests for database schema initialization — requires running PostgreSQL.

Local dev: PostgreSQL at localhost:5432 (postgres/Technology100, db=helium_core).
Docker: docker-compose up -d postgres (helium/helium_dev).
"""

import pytest

from tests.ws0.conftest import needs_pg


@needs_pg
@pytest.mark.asyncio
class TestSchemaCreation:
    """Test init_schemas creates all 5 schemas and executes DDL."""

    async def test_init_schemas_creates_all_schemas(self, pg_pool):
        """All 5 schemas should exist after init_schemas."""
        from src.database.init import SCHEMAS, init_schemas

        await init_schemas(pg_pool)

        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = ANY(%s)",
                (SCHEMAS,),
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        assert found == set(SCHEMAS)

    async def test_init_schemas_idempotent(self, pg_pool):
        """Running init_schemas twice must not raise."""
        from src.database.init import init_schemas

        await init_schemas(pg_pool)
        await init_schemas(pg_pool)
        # If we reach here without error, idempotency works


@needs_pg
@pytest.mark.asyncio
class TestInvoicesSchema:
    """Verify invoices schema tables exist after init."""

    async def test_invoices_tables_exist(self, pg_pool):
        from src.database.init import init_schemas

        await init_schemas(pg_pool)

        expected_tables = {
            "schema_version",
            "invoices",
            "invoice_line_items",
            "invoice_attachments",
        }
        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'invoices'"
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        for table in expected_tables:
            assert table in found, f"Missing invoices.{table}"

    async def test_invoices_schema_version(self, pg_pool):
        async with pg_pool.connection() as conn:
            await conn.execute("SET search_path TO invoices")
            result = await conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            )
            row = await result.fetchone()

        assert row is not None
        assert row[0].startswith("2.1")


@needs_pg
@pytest.mark.asyncio
class TestCustomersSchema:
    """Verify customers schema tables exist."""

    async def test_customers_tables_exist(self, pg_pool):
        from src.database.init import init_schemas

        await init_schemas(pg_pool)

        expected_tables = {"customers", "customer_branches", "customer_contacts"}
        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'customers'"
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        for table in expected_tables:
            assert table in found, f"Missing customers.{table}"


@needs_pg
@pytest.mark.asyncio
class TestInventorySchema:
    """Verify inventory schema tables exist."""

    async def test_inventory_tables_exist(self, pg_pool):
        from src.database.init import init_schemas

        await init_schemas(pg_pool)

        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'inventory'"
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        assert "inventory" in found


@needs_pg
@pytest.mark.asyncio
class TestCoreSchema:
    """Verify core schema tables and indexes."""

    async def test_core_tables_exist(self, pg_pool):
        from src.database.init import init_schemas

        await init_schemas(pg_pool)

        expected_tables = {
            "core_queue",
            "processed_files",
            "transformation_scripts",
            "config",
        }
        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'core'"
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        for table in expected_tables:
            assert table in found, f"Missing core.{table}"

    async def test_core_queue_indexes(self, pg_pool):
        expected_indexes = {
            "idx_queue_status",
            "idx_queue_priority",
            "idx_queue_data_uuid",
            "idx_queue_company",
        }
        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'core'"
            )
            rows = await result.fetchall()
            found = {row[0] for row in rows}

        for idx in expected_indexes:
            assert idx in found, f"Missing index core.{idx}"

    async def test_core_queue_status_constraint(self, pg_pool):
        """Status CHECK constraint should reject invalid values."""
        async with pg_pool.connection() as conn:
            await conn.execute("SET search_path TO core")
            with pytest.raises(Exception):
                await conn.execute(
                    "INSERT INTO core_queue (queue_id, company_id, status) "
                    "VALUES ('test-bad', 'co1', 'INVALID')"
                )


@needs_pg
@pytest.mark.asyncio
class TestNotificationsSchema:
    """Verify notifications schema exists (placeholder — no DDL yet)."""

    async def test_notifications_schema_exists(self, pg_pool):
        from src.database.init import init_schemas

        await init_schemas(pg_pool)

        async with pg_pool.connection() as conn:
            result = await conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'notifications'"
            )
            row = await result.fetchone()

        assert row is not None


@needs_pg
@pytest.mark.asyncio
class TestPoolOperations:
    """Test pool.py functions against running PostgreSQL."""

    async def test_create_pool(self):
        from src.config import CoreConfig
        from src.database.pool import close_pool, create_pool

        config = CoreConfig(
            db_user="postgres",
            db_password="Technology100",
            db_name="helium_core",
        )
        pool = await create_pool(config)
        assert pool is not None
        await close_pool(pool)

    async def test_check_pool(self):
        from src.config import CoreConfig
        from src.database.pool import check_pool, close_pool, create_pool

        config = CoreConfig(
            db_user="postgres",
            db_password="Technology100",
            db_name="helium_core",
        )
        pool = await create_pool(config)
        assert await check_pool(pool) is True
        await close_pool(pool)

    async def test_get_connection_sets_search_path(self, pg_pool):
        from src.database.pool import get_connection

        async with get_connection(pg_pool, schema="public") as conn:
            result = await conn.execute("SHOW search_path")
            row = await result.fetchone()
            assert "public" in row[0]
