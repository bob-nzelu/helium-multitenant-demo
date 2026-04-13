"""
Delayed Cleanup for Completed Queue Entries

Per CORE_QUEUE_DELAYED_CLEANUP_SPEC: core_queue entries must survive
24 hours after completion for HeartBeat reconciliation.

Runs every 60 minutes via APScheduler.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def cleanup_completed_queue_entries(pool) -> None:
    """Delete core_queue entries that completed more than 24 hours ago.

    Only removes entries with terminal status (PREVIEW_READY, FINALIZED).
    PENDING and PROCESSING entries are never touched.
    FAILED entries are handled by a separate job (30-day retention).
    """
    from src.database.pool import get_connection

    tables_cleaned = {
        "PREVIEW_READY": 0,
        "FINALIZED": 0,
    }

    try:
        async with get_connection(pool, "core") as conn:
            result = await conn.execute(
                """
                DELETE FROM core_queue
                WHERE status IN ('PREVIEW_READY', 'FINALIZED')
                  AND processing_completed_at < NOW() - INTERVAL '24 hours'
                RETURNING queue_id, status
                """
            )
            rows = await result.fetchall()
            for row in rows:
                tables_cleaned[row[1]] = tables_cleaned.get(row[1], 0) + 1

        total = sum(tables_cleaned.values())
        if total > 0:
            logger.info(
                "cleanup_completed_queue",
                deleted=total,
                preview_ready=tables_cleaned["PREVIEW_READY"],
                finalized=tables_cleaned["FINALIZED"],
            )
        else:
            logger.debug("cleanup_completed_queue_noop")

    except Exception:
        logger.exception("cleanup_completed_queue_error")
