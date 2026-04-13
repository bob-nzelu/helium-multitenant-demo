"""
Shared test fixtures for WS0 tests.

Database integration tests assume PostgreSQL is already running locally.
Tests skip if PG is unreachable.

Pattern matches HeartBeat: local PostgreSQL, no testcontainers.
Docker Compose for AWS hosting; local PostgreSQL for dev/test.
"""

import asyncio
import os
import sys

import pytest
import pytest_asyncio

# ── Windows event loop fix for psycopg3 async ────────────────────────────
# psycopg3 async requires SelectorEventLoop, not ProactorEventLoop (Windows default).
# Must be set BEFORE any async fixtures or tests run.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ── PostgreSQL connection (local dev — matches HeartBeat pattern) ────────
# Docker Compose uses helium/helium_dev; local dev uses postgres/Technology100.
PG_DSN = (
    f"host={os.environ.get('CORE_DB_HOST', 'localhost')} "
    f"port={os.environ.get('CORE_DB_PORT', '5432')} "
    f"dbname={os.environ.get('CORE_DB_NAME', 'helium_core')} "
    f"user={os.environ.get('CORE_DB_USER', 'postgres')} "
    f"password={os.environ.get('CORE_DB_PASSWORD', 'Technology100')}"
)

# ── Check if PostgreSQL is reachable ─────────────────────────────────────
_PG_AVAILABLE = None


def _check_pg() -> bool:
    """Check if PostgreSQL is reachable at the configured DSN (sync check)."""
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
    reason="PostgreSQL not available — start local PostgreSQL or docker-compose up -d postgres",
)


# ── Function-scoped pool fixture ─────────────────────────────────────────
# Session-scoped async fixtures break on Windows with pytest-asyncio (the pool
# opens in one event loop but tests run on another).  Function scope creates
# a fresh pool per test — acceptable for ~12 integration tests.
@pytest_asyncio.fixture
async def pg_pool():
    """Per-test async connection pool to local PostgreSQL."""
    if not _check_pg():
        pytest.skip("PostgreSQL not available")

    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=PG_DSN,
        min_size=1,
        max_size=3,
        open=False,
    )
    await pool.open()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def pg_conn(pg_pool):
    """Per-test connection."""
    async with pg_pool.connection() as conn:
        yield conn
