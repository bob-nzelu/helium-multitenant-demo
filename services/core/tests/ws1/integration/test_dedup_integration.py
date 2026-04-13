"""Integration tests for dedup checker against real PostgreSQL."""

import pytest
from uuid6 import uuid7

from src.ingestion.dedup import DedupChecker
from tests.ws1.integration.conftest import needs_pg


@needs_pg
class TestDedupIntegration:
    @pytest.mark.asyncio
    async def test_check_no_duplicate(self, pg_pool):
        result = await DedupChecker.check("abc123def456", pg_pool)
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_record_and_check(self, pg_pool):
        file_hash = DedupChecker.compute_hash(b"unique content")
        await DedupChecker.record(file_hash, "test.xlsx", str(uuid7()), "data-1", pg_pool)

        result = await DedupChecker.check(file_hash, pg_pool)
        assert result.is_duplicate is True
        assert result.existing_filename == "test.xlsx"

    @pytest.mark.asyncio
    async def test_record_idempotent(self, pg_pool):
        file_hash = DedupChecker.compute_hash(b"idempotent content")
        queue_id = str(uuid7())

        # Insert twice — should not raise
        await DedupChecker.record(file_hash, "a.csv", queue_id, "d-1", pg_pool)
        await DedupChecker.record(file_hash, "b.csv", queue_id, "d-1", pg_pool)

        result = await DedupChecker.check(file_hash, pg_pool)
        assert result.is_duplicate is True
        # First insert wins
        assert result.existing_filename == "a.csv"

    @pytest.mark.asyncio
    async def test_different_files_not_duplicate(self, pg_pool):
        h1 = DedupChecker.compute_hash(b"file A")
        h2 = DedupChecker.compute_hash(b"file B")

        await DedupChecker.record(h1, "a.csv", str(uuid7()), "d-1", pg_pool)

        result = await DedupChecker.check(h2, pg_pool)
        assert result.is_duplicate is False
