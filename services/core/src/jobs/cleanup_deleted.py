"""
Soft-Delete Cleanup Job (WS4)

APScheduler job that permanently deletes records past the 24-hour
soft-delete recovery window. Runs every 60 minutes.

Per MENTAL_MODEL \u00a77.4: Permanent deletion cascades to all child
tables via ON DELETE CASCADE foreign keys.
"""

from __future__ import annotations

import structlog
from psycopg_pool import AsyncConnectionPool

logger = structlog.get_logger()

TABLES: list[tuple[str, str]] = [
    ("invoices.invoices", "invoice"),
    ("customers.customers", "customer"),
    ("inventory.inventory", "inventory"),
]

RECOVERY_WINDOW = "24 hours"


async def cleanup_expired_soft_deletes(pool: AsyncConnectionPool) -> None:
    """
    Permanently delete records past the 24-hour recovery window.

    ON DELETE CASCADE handles child tables automatically.
    """
    total_deleted = 0

    async with pool.connection() as conn:
        for table, entity_type in TABLES:
            cur = await conn.execute(
                f"""
                DELETE FROM {table}
                WHERE deleted_at IS NOT NULL
                  AND deleted_at < NOW() - INTERVAL '{RECOVERY_WINDOW}'
                """
            )
            count = cur.rowcount
            if count > 0:
                total_deleted += count
                logger.info(
                    "cleanup_expired_deleted",
                    entity_type=entity_type,
                    count=count,
                )

    if total_deleted > 0:
        logger.info("cleanup_expired_total", total_deleted=total_deleted)
    else:
        logger.debug("cleanup_expired_none")
