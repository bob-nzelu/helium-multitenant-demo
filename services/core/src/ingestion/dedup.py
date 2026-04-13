"""
SHA256 Deduplication Checker

Checks and records file hashes in core.processed_files.
"""

from __future__ import annotations

import hashlib

import structlog
from psycopg_pool import AsyncConnectionPool

from src.database.pool import get_connection
from src.ingestion.models import DedupResult

logger = structlog.get_logger()


class DedupChecker:
    """File-level deduplication via SHA256 hash."""

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """Compute SHA256 hex digest of raw file bytes."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    async def check(file_hash: str, pool: AsyncConnectionPool) -> DedupResult:
        """
        Check if a file hash already exists in processed_files.

        Returns DedupResult with is_duplicate flag.
        Does NOT insert — that happens after finalization (WS5).
        """
        async with get_connection(pool, "core") as conn:
            cur = await conn.execute(
                "SELECT queue_id, original_filename FROM processed_files WHERE file_hash = %s",
                (file_hash,),
            )
            row = await cur.fetchone()

        if row:
            logger.info("dedup_duplicate_found", file_hash=file_hash[:16])
            return DedupResult(
                is_duplicate=True,
                file_hash=file_hash,
                existing_queue_id=row[0],
                existing_filename=row[1],
            )

        return DedupResult(is_duplicate=False, file_hash=file_hash)

    @staticmethod
    async def record(
        file_hash: str,
        original_filename: str,
        queue_id: str,
        data_uuid: str,
        pool: AsyncConnectionPool,
    ) -> None:
        """
        Record a processed file hash. Called by WS5 after finalization.
        ON CONFLICT DO NOTHING — idempotent.
        """
        async with get_connection(pool, "core") as conn:
            await conn.execute(
                "INSERT INTO processed_files (file_hash, original_filename, queue_id, data_uuid) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (file_hash, original_filename, queue_id, data_uuid),
            )
