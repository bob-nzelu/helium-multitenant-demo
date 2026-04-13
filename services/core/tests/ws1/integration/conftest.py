"""
Integration test fixtures for WS1.

Uses real local PostgreSQL (same pattern as WS0).
Each test gets a clean core_queue + processed_files table.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio

# Windows event loop fix
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PG_DSN = (
    f"host={os.environ.get('CORE_DB_HOST', 'localhost')} "
    f"port={os.environ.get('CORE_DB_PORT', '5432')} "
    f"dbname={os.environ.get('CORE_DB_NAME', 'helium_core')} "
    f"user={os.environ.get('CORE_DB_USER', 'postgres')} "
    f"password={os.environ.get('CORE_DB_PASSWORD', 'Technology100')}"
)

_PG_AVAILABLE = None


def _check_pg() -> bool:
    global _PG_AVAILABLE
    if _PG_AVAILABLE is not None:
        return _PG_AVAILABLE
    try:
        import psycopg
        with psycopg.connect(PG_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        _PG_AVAILABLE = True
    except Exception:
        _PG_AVAILABLE = False
    return _PG_AVAILABLE


needs_pg = pytest.mark.skipif(
    not _check_pg(),
    reason="PostgreSQL not available",
)


@pytest_asyncio.fixture
async def pg_pool():
    """Per-test async connection pool."""
    if not _check_pg():
        pytest.skip("PostgreSQL not available")

    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(conninfo=PG_DSN, min_size=1, max_size=3, open=False)
    await pool.open()

    # Ensure core schema + tables exist with WS1 columns
    async with pool.connection() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS core")
        await conn.execute("SET search_path TO core")

        # Drop and recreate for clean WS1 schema (test isolation)
        await conn.execute("DROP TABLE IF EXISTS core_queue CASCADE")
        await conn.execute("DROP TABLE IF EXISTS processed_files CASCADE")

        await conn.execute("""
            CREATE TABLE core_queue (
                queue_id TEXT PRIMARY KEY NOT NULL,
                blob_uuid TEXT NOT NULL UNIQUE,
                data_uuid TEXT,
                original_filename TEXT,
                company_id TEXT NOT NULL,
                uploaded_by TEXT,
                batch_id TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','PROCESSING','PROCESSED','PREVIEW_READY','FAILED')),
                priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
                processing_started_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ,
                error_message TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3
            )
        """)
        await conn.execute("""
            CREATE TABLE processed_files (
                file_hash TEXT PRIMARY KEY NOT NULL,
                original_filename TEXT,
                queue_id TEXT,
                data_uuid TEXT,
                processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

    yield pool

    # Cleanup: truncate test data
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO core")
        await conn.execute("DELETE FROM core_queue")
        await conn.execute("DELETE FROM processed_files")

    await pool.close()
