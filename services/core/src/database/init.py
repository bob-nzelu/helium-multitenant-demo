"""
Schema Initialization

Creates all 5 PostgreSQL schemas and executes DDL from SQL files.
Per D-WS0-008: All DDL uses IF NOT EXISTS — idempotent on every startup.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from psycopg_pool import AsyncConnectionPool

logger = structlog.get_logger()

# All schemas Core manages
SCHEMAS = ["invoices", "customers", "inventory", "core", "notifications"]

# SQL files to execute (order matters for cross-schema FKs)
SQL_DIR = Path(__file__).parent / "schemas"
SQL_FILES = [
    ("invoices", "invoices.sql"),
    ("customers", "customers.sql"),
    ("inventory", "inventory.sql"),
    ("core", "core.sql"),
    ("core", "audit.sql"),
    ("core", "event_ledger.sql"),
    ("notifications", "notifications.sql"),
]


async def init_schemas(pool: AsyncConnectionPool) -> None:
    """
    Initialize all database schemas.

    1. CREATE SCHEMA IF NOT EXISTS for all 5 schemas
    2. Execute DDL from each SQL file
    3. Log completion

    This is idempotent — safe to call on every startup.
    """
    async with pool.connection() as conn:
        # Step 1: Create all schemas
        for schema in SCHEMAS:
            await conn.execute(
                f"CREATE SCHEMA IF NOT EXISTS {schema}"
            )
            logger.info("schema_created", schema=schema)

        # Step 2: Execute DDL files
        for schema_name, sql_file in SQL_FILES:
            sql_path = SQL_DIR / sql_file
            if not sql_path.exists():
                logger.warning(
                    "schema_sql_not_found",
                    schema=schema_name,
                    path=str(sql_path),
                )
                continue

            sql = sql_path.read_text(encoding="utf-8")
            await conn.execute(sql)
            logger.info(
                "schema_ddl_executed",
                schema=schema_name,
                file=sql_file,
            )

    logger.info("all_schemas_initialized", schemas=SCHEMAS)
