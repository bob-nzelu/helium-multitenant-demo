"""Integration tests for POST /api/v1/enqueue."""

import pytest
import pytest_asyncio
from uuid6 import uuid7

from src.database.pool import get_connection
from src.ingestion.models import EnqueueRequest
from tests.ws1.integration.conftest import needs_pg


@needs_pg
class TestEnqueueIntegration:
    @pytest.mark.asyncio
    async def test_create_queue_entry(self, pg_pool):
        """Insert a queue entry and verify it exists."""
        queue_id = str(uuid7())
        blob_uuid = str(uuid7())

        async with get_connection(pg_pool, "core") as conn:
            await conn.execute(
                """INSERT INTO core_queue
                   (queue_id, blob_uuid, data_uuid, original_filename, company_id, priority, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')""",
                (queue_id, blob_uuid, "data-1", "invoice.xlsx", "tenant-1", 3),
            )

        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT queue_id, status FROM core_queue WHERE queue_id = %s",
                (queue_id,),
            )
            row = await cur.fetchone()

        assert row is not None
        assert row[0] == queue_id
        assert row[1] == "PENDING"

    @pytest.mark.asyncio
    async def test_idempotency_blob_uuid_unique(self, pg_pool):
        """Duplicate blob_uuid should be rejected."""
        blob_uuid = str(uuid7())

        async with get_connection(pg_pool, "core") as conn:
            await conn.execute(
                """INSERT INTO core_queue
                   (queue_id, blob_uuid, company_id, status)
                   VALUES (%s, %s, %s, 'PENDING')""",
                (str(uuid7()), blob_uuid, "tenant-1"),
            )

        import psycopg
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with get_connection(pg_pool, "core") as conn:
                await conn.execute(
                    """INSERT INTO core_queue
                       (queue_id, blob_uuid, company_id, status)
                       VALUES (%s, %s, %s, 'PENDING')""",
                    (str(uuid7()), blob_uuid, "tenant-1"),
                )

    @pytest.mark.asyncio
    async def test_priority_constraint(self, pg_pool):
        """Priority must be 1-5."""
        import psycopg
        with pytest.raises(psycopg.errors.CheckViolation):
            async with get_connection(pg_pool, "core") as conn:
                await conn.execute(
                    """INSERT INTO core_queue
                       (queue_id, blob_uuid, company_id, priority, status)
                       VALUES (%s, %s, %s, %s, 'PENDING')""",
                    (str(uuid7()), str(uuid7()), "tenant-1", 0),
                )

    @pytest.mark.asyncio
    async def test_status_constraint(self, pg_pool):
        """Status must be one of the valid values."""
        import psycopg
        with pytest.raises(psycopg.errors.CheckViolation):
            async with get_connection(pg_pool, "core") as conn:
                await conn.execute(
                    """INSERT INTO core_queue
                       (queue_id, blob_uuid, company_id, status)
                       VALUES (%s, %s, %s, 'INVALID')""",
                    (str(uuid7()), str(uuid7()), "tenant-1"),
                )

    @pytest.mark.asyncio
    async def test_status_update_to_processing(self, pg_pool):
        """Transition PENDING → PROCESSING."""
        queue_id = str(uuid7())
        async with get_connection(pg_pool, "core") as conn:
            await conn.execute(
                """INSERT INTO core_queue
                   (queue_id, blob_uuid, company_id, status)
                   VALUES (%s, %s, %s, 'PENDING')""",
                (queue_id, str(uuid7()), "tenant-1"),
            )
            await conn.execute(
                """UPDATE core_queue SET status = 'PROCESSING',
                   processing_started_at = NOW() WHERE queue_id = %s""",
                (queue_id,),
            )
            cur = await conn.execute(
                "SELECT status, processing_started_at FROM core_queue WHERE queue_id = %s",
                (queue_id,),
            )
            row = await cur.fetchone()
        assert row[0] == "PROCESSING"
        assert row[1] is not None

    @pytest.mark.asyncio
    async def test_max_attempts_default(self, pg_pool):
        """max_attempts should default to 3."""
        queue_id = str(uuid7())
        async with get_connection(pg_pool, "core") as conn:
            await conn.execute(
                """INSERT INTO core_queue (queue_id, blob_uuid, company_id, status)
                   VALUES (%s, %s, %s, 'PENDING')""",
                (queue_id, str(uuid7()), "tenant-1"),
            )
            cur = await conn.execute(
                "SELECT max_attempts FROM core_queue WHERE queue_id = %s",
                (queue_id,),
            )
            row = await cur.fetchone()
        assert row[0] == 3
