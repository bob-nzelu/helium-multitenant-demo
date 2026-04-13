"""
WS6: Metrics Collector — Background task updating Prometheus gauges.

Follows the QueueScanner pattern: start/stop/loop/tick with
asyncio.create_task and CancelledError handling.

Updates queue_depth and entity_count gauges every N seconds.
"""

from __future__ import annotations

import asyncio

import structlog
from psycopg_pool import AsyncConnectionPool

from src.observability.metrics import entity_count, queue_depth

logger = structlog.get_logger()


class MetricsCollector:
    """Background job: periodically update Prometheus gauges from DB."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        interval: int = 30,
    ) -> None:
        self._pool = pool
        self._interval = interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the collector background loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("metrics_collector_started", interval=self._interval)

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("metrics_collector_stopped")

    async def _loop(self) -> None:
        """Main loop: tick every interval."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("metrics_collector_tick_error")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """Update gauges from database."""
        try:
            async with self._pool.connection() as conn:
                # Queue depth by status
                cur = await conn.execute(
                    """SELECT status, COUNT(*)
                       FROM core.core_queue
                       GROUP BY status"""
                )
                rows = await cur.fetchall()
                # Reset all known statuses to 0 first
                for status in ("PENDING", "PROCESSING"):
                    queue_depth.labels(status=status).set(0)
                for row in rows:
                    if row[0] in ("PENDING", "PROCESSING"):
                        queue_depth.labels(status=row[0]).set(row[1])

                # Entity counts
                for table, entity_type in [
                    ("invoices.invoices", "invoice"),
                    ("customers.customers", "customer"),
                    ("inventory.inventory", "inventory"),
                ]:
                    try:
                        cur = await conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL"
                        )
                        row = await cur.fetchone()
                        entity_count.labels(entity_type=entity_type).set(
                            row[0] if row else 0
                        )
                    except Exception:
                        # Table may not exist yet — skip silently
                        pass
        except Exception as e:
            logger.warning("metrics_tick_db_error", error=str(e))
