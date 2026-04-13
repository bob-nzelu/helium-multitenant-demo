"""WS3: Scheduled Jobs — stale processing detection + preview cleanup."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Thresholds
STALE_PROCESSING_MINUTES = 15
PREVIEW_RETENTION_DAYS = 7


async def stale_processing_detector(db_pool, sse_manager=None) -> None:
    """Detect queue entries stuck in PROCESSING > 15 minutes and mark FAILED.

    Runs every 5 minutes via APScheduler.
    Catches orchestrator crashes that leave entries in PROCESSING forever.
    """
    from src.database.pool import get_connection

    try:
        threshold = datetime.now(timezone.utc) - timedelta(minutes=STALE_PROCESSING_MINUTES)

        async with get_connection(db_pool, "core") as conn:
            # Find stale entries
            result = await conn.execute(
                "SELECT queue_id, data_uuid, processing_started_at "
                "FROM core_queue "
                "WHERE status = 'PROCESSING' AND processing_started_at < $1",
                (threshold,),
            )
            rows = await result.fetchall()

            if not rows:
                return

            logger.warning("Found %d stale PROCESSING entries (>%d min)", len(rows), STALE_PROCESSING_MINUTES)

            for row in rows:
                queue_id = row[0] if not isinstance(row, dict) else row["queue_id"]
                data_uuid = row[1] if not isinstance(row, dict) else row.get("data_uuid", "")

                await conn.execute(
                    "UPDATE core_queue SET status = 'FAILED', "
                    "error_message = $1, "
                    "processing_completed_at = CURRENT_TIMESTAMP "
                    "WHERE queue_id = $2 AND status = 'PROCESSING'",
                    (f"Orchestrator crash detected (stale >{STALE_PROCESSING_MINUTES}min)", queue_id),
                )

                logger.warning("Marked stale entry %s as FAILED", queue_id)

                # Emit SSE if available
                if sse_manager:
                    try:
                        from src.sse.models import SSEEvent
                        from src.sse.events import EVENT_PROCESSING_LOG, make_log_event
                        await sse_manager.publish(SSEEvent(
                            event_type=EVENT_PROCESSING_LOG,
                            data=make_log_event(
                                data_uuid,
                                "Processing timed out. Please resubmit.",
                                "error",
                            ),
                            data_uuid=data_uuid,
                        ))
                    except Exception:
                        pass

    except Exception:
        logger.exception("Stale processing detector failed")


async def preview_cleanup(db_pool, blob_client=None) -> None:
    """Delete expired preview data (>7 days) and mark queue entries as EXPIRED.

    Runs daily via APScheduler.
    """
    from src.database.pool import get_connection

    try:
        threshold = datetime.now(timezone.utc) - timedelta(days=PREVIEW_RETENTION_DAYS)

        async with get_connection(db_pool, "core") as conn:
            result = await conn.execute(
                "SELECT queue_id, data_uuid "
                "FROM core_queue "
                "WHERE status = 'PREVIEW_READY' AND processing_completed_at < $1",
                (threshold,),
            )
            rows = await result.fetchall()

            if not rows:
                return

            logger.info("Cleaning up %d expired preview entries (>%d days)", len(rows), PREVIEW_RETENTION_DAYS)

            for row in rows:
                queue_id = row[0] if not isinstance(row, dict) else row["queue_id"]

                await conn.execute(
                    "UPDATE core_queue SET status = 'EXPIRED' WHERE queue_id = $1",
                    (queue_id,),
                )
                logger.info("Marked %s as EXPIRED", queue_id)

    except Exception:
        logger.exception("Preview cleanup failed")
