"""
PostgreSQL Connection Pool (psycopg3)

Thin wrapper around psycopg3's AsyncConnectionPool.
Per D-WS0-002: min=5, max=20 (configurable).
Per D-WS0-015: SET search_path per connection.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from src.config import CoreConfig

logger = structlog.get_logger()

# Health check timeout (D-WS0-011: 5 seconds)
HEALTH_CHECK_TIMEOUT = 5.0


async def create_pool(config: CoreConfig) -> AsyncConnectionPool:
    """
    Create and open an async connection pool.

    Args:
        config: CoreConfig with database connection parameters.

    Returns:
        An open AsyncConnectionPool ready for use.
    """
    pool = AsyncConnectionPool(
        conninfo=config.conninfo,
        min_size=config.db_pool_min,
        max_size=config.db_pool_max,
        open=False,
    )
    await pool.open()
    await pool.check()
    logger.info(
        "database_pool_created",
        min_size=config.db_pool_min,
        max_size=config.db_pool_max,
        db_name=config.db_name,
    )
    return pool


async def close_pool(pool: AsyncConnectionPool) -> None:
    """Close the connection pool, draining all connections."""
    await pool.close()
    logger.info("database_pool_closed")


async def check_pool(pool: AsyncConnectionPool) -> bool:
    """
    Health check: SELECT 1 with 5-second timeout.

    Returns:
        True if database is reachable, False otherwise.
    """
    try:
        async with pool.connection() as conn:
            await asyncio.wait_for(
                conn.execute("SELECT 1"),
                timeout=HEALTH_CHECK_TIMEOUT,
            )
        return True
    except Exception:
        return False


@asynccontextmanager
async def get_connection(
    pool: AsyncConnectionPool,
    schema: str = "public",
) -> AsyncGenerator[AsyncConnection, None]:
    """
    Borrow a connection from the pool with search_path set.

    Args:
        pool: The connection pool.
        schema: PostgreSQL schema name to set as search_path.

    Yields:
        An AsyncConnection with search_path configured.
    """
    async with pool.connection() as conn:
        await conn.execute(f"SET search_path TO {schema}")
        yield conn
