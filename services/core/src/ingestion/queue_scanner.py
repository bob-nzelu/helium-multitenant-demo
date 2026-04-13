"""
Queue Scanner — Safety-net background task.

Polls core_queue every 60s for PENDING entries that weren't picked up
by the immediate processing path. Also recovers stale entries stuck
in PROCESSING for too long.
"""

from __future__ import annotations

import asyncio

import structlog
from psycopg_pool import AsyncConnectionPool

from src.config import CoreConfig
from src.database.pool import get_connection
from src.ingestion.heartbeat_client import HeartBeatBlobClient
from src.ingestion.parsers.registry import ParserRegistry
from src.ingestion.router import process_entry
from src.sse.manager import SSEConnectionManager

logger = structlog.get_logger()


class QueueScanner:
    """
    Safety-net scanner for the ingestion queue.

    Primary processing happens immediately via asyncio.create_task in the
    enqueue endpoint. This scanner catches anything that fell through:
    - Entries created while Core was restarting
    - Entries where immediate processing failed before the task started
    - Stale entries stuck in PROCESSING for too long
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        config: CoreConfig,
        sse_manager: SSEConnectionManager,
        heartbeat_client: HeartBeatBlobClient,
        parser_registry: ParserRegistry,
        audit_logger=None,
        notification_service=None,
    ) -> None:
        self._pool = pool
        self._config = config
        self._sse_manager = sse_manager
        self._heartbeat_client = heartbeat_client
        self._parser_registry = parser_registry
        self._audit_logger = audit_logger
        self._notification_service = notification_service
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the scanner background loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "scanner_started",
            interval=self._config.scanner_interval,
            stale_threshold=self._config.scanner_stale_threshold,
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("scanner_stopped")

    async def _loop(self) -> None:
        """Main loop: tick + recover_stale every interval."""
        while self._running:
            try:
                await self._tick()
                await self._recover_stale()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("scanner_tick_error")

            try:
                await asyncio.sleep(self._config.scanner_interval)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """
        Pick up PENDING entries and process them.

        Uses FOR UPDATE SKIP LOCKED so multiple scanner instances
        don't contend on the same rows.
        """
        async with get_connection(self._pool, "core") as conn:
            cur = await conn.execute(
                """SELECT queue_id, blob_uuid, data_uuid, original_filename, company_id
                   FROM core_queue
                   WHERE status = 'PENDING'
                   ORDER BY priority DESC, created_at ASC
                   LIMIT %s
                   FOR UPDATE SKIP LOCKED""",
                (self._config.batch_size,),
            )
            rows = await cur.fetchall()

        if not rows:
            return

        logger.info("scanner_tick", pending_count=len(rows))

        for row in rows:
            queue_id, blob_uuid, data_uuid, filename, company_id = row

            # WS6: Audit queue.processing
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="queue.processing",
                    entity_type="queue",
                    entity_id=queue_id,
                    action="PROCESS",
                    company_id=company_id or "",
                    actor_type="scheduler",
                    metadata={"source": "scanner"},
                )

            try:
                await process_entry(
                    queue_id=queue_id,
                    blob_uuid=blob_uuid,
                    data_uuid=data_uuid or "",
                    original_filename=filename or "",
                    company_id=company_id,
                    pool=self._pool,
                    sse_manager=self._sse_manager,
                    heartbeat_client=self._heartbeat_client,
                    parser_registry=self._parser_registry,
                    audit_logger=self._audit_logger,
                )
            except Exception:
                logger.exception("scanner_process_error", queue_id=queue_id)

    async def _recover_stale(self) -> None:
        """
        Reset entries stuck in PROCESSING beyond the stale threshold.

        If retry_count < max_attempts → reset to PENDING (will be retried).
        If retry_count >= max_attempts → mark FAILED permanently.
        """
        threshold_seconds = self._config.scanner_stale_threshold

        async with get_connection(self._pool, "core") as conn:
            # Reset recoverable entries
            cur = await conn.execute(
                """UPDATE core_queue
                   SET status = 'PENDING',
                       retry_count = retry_count + 1,
                       updated_at = NOW()
                   WHERE status = 'PROCESSING'
                   AND processing_started_at < NOW() - INTERVAL '%s seconds'
                   AND retry_count < max_attempts
                   RETURNING queue_id""",
                (threshold_seconds,),
            )
            recovered = await cur.fetchall()

            # Fail permanently
            cur = await conn.execute(
                """UPDATE core_queue
                   SET status = 'FAILED',
                       error_message = 'Max retry attempts exceeded (stale recovery)',
                       updated_at = NOW()
                   WHERE status = 'PROCESSING'
                   AND processing_started_at < NOW() - INTERVAL '%s seconds'
                   AND retry_count >= max_attempts
                   RETURNING queue_id""",
                (threshold_seconds,),
            )
            failed = await cur.fetchall()

        if recovered:
            logger.warning("scanner_stale_recovered", count=len(recovered))
            # WS6: Audit queue.stale_recovered
            if self._audit_logger:
                for r in recovered:
                    await self._audit_logger.log(
                        event_type="queue.stale_recovered",
                        entity_type="queue",
                        entity_id=r[0],
                        action="PROCESS",
                        actor_type="scheduler",
                        metadata={"stuck_duration_threshold_s": threshold_seconds},
                    )
        if failed:
            logger.error("scanner_stale_failed_permanently", count=len(failed))
            # WS6: Audit + notify user (EH-003)
            for f in failed:
                queue_id = f[0]
                if self._audit_logger:
                    await self._audit_logger.log(
                        event_type="queue.retry",
                        entity_type="queue",
                        entity_id=queue_id,
                        action="PROCESS",
                        actor_type="scheduler",
                        metadata={"permanent_failure": True},
                    )
                # EH-003: Notify uploading user
                notification_svc = getattr(self, "_notification_service", None)
                if notification_svc:
                    # Fetch upload details for notification
                    try:
                        async with get_connection(self._pool, "core") as conn:
                            cur = await conn.execute(
                                "SELECT original_filename, uploaded_by, company_id "
                                "FROM core_queue WHERE queue_id = %s",
                                (queue_id,),
                            )
                            entry = await cur.fetchone()
                        if entry:
                            filename, uploaded_by, company_id = entry
                            await notification_svc.send(
                                company_id=company_id or "",
                                notification_type="business",
                                category="upload_failed",
                                title=f"Upload failed: {filename or 'unknown file'}",
                                body="Processing failed after maximum retry attempts.",
                                priority="high",
                                recipient_id=uploaded_by,
                                data={"queue_id": queue_id},
                            )
                    except Exception:
                        logger.exception("scanner_notification_failed", queue_id=queue_id)
