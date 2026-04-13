"""Integration tests for queue status queries."""

import pytest
from uuid6 import uuid7

from src.database.pool import get_connection
from tests.ws1.integration.conftest import needs_pg


@needs_pg
class TestQueueStatusIntegration:
    async def _insert_entry(self, pool, status="PENDING", company_id="tenant-1", priority=3):
        queue_id = str(uuid7())
        blob_uuid = str(uuid7())
        async with get_connection(pool, "core") as conn:
            await conn.execute(
                """INSERT INTO core_queue
                   (queue_id, blob_uuid, data_uuid, original_filename, company_id, priority, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (queue_id, blob_uuid, "data-1", "file.xlsx", company_id, priority, status),
            )
        return queue_id

    @pytest.mark.asyncio
    async def test_count_by_status(self, pg_pool):
        await self._insert_entry(pg_pool, "PENDING")
        await self._insert_entry(pg_pool, "PENDING")
        await self._insert_entry(pg_pool, "PROCESSED")

        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM core_queue WHERE status = 'PENDING'"
            )
            row = await cur.fetchone()
        assert row[0] == 2

    @pytest.mark.asyncio
    async def test_filter_by_company(self, pg_pool):
        await self._insert_entry(pg_pool, company_id="alpha")
        await self._insert_entry(pg_pool, company_id="beta")

        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM core_queue WHERE company_id = %s",
                ("alpha",),
            )
            row = await cur.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_priority_ordering(self, pg_pool):
        q_low = await self._insert_entry(pg_pool, priority=1)
        q_high = await self._insert_entry(pg_pool, priority=5)

        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT queue_id FROM core_queue ORDER BY priority DESC"
            )
            rows = await cur.fetchall()
        assert rows[0][0] == q_high
        assert rows[1][0] == q_low

    @pytest.mark.asyncio
    async def test_pagination(self, pg_pool):
        for _ in range(5):
            await self._insert_entry(pg_pool)

        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT queue_id FROM core_queue ORDER BY created_at LIMIT 2 OFFSET 2"
            )
            rows = await cur.fetchall()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_empty_result(self, pg_pool):
        async with get_connection(pg_pool, "core") as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM core_queue WHERE company_id = 'nonexistent'"
            )
            row = await cur.fetchone()
        assert row[0] == 0
