"""
Entity Event Listener (WS4)

Background asyncio task that listens on PostgreSQL 'entity_events'
notification channel via pg_notify. Converts notifications into SSE
broadcasts via WS0's SSE manager.

Per Q3 APPROVED: Single unified 'entity_events' channel replaces
per-entity channels.
"""

from __future__ import annotations

import asyncio
import json

import structlog
from psycopg_pool import AsyncConnectionPool

from src.sse.manager import SSEConnectionManager
from src.sse.models import SSEEvent

logger = structlog.get_logger()

CHANNEL = "entity_events"
RECONNECT_DELAY = 5


class EntityEventListener:
    """
    Listens on the 'entity_events' pg_notify channel and forwards
    events to the SSE manager.

    Usage:
        listener = EntityEventListener(pool, sse_manager)
        await listener.start()
        # ... application runs ...
        await listener.stop()
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        sse_manager: SSEConnectionManager,
    ):
        self._pool = pool
        self._sse_manager = sse_manager
        self._task = None
        self._running = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("entity_event_listener_started", channel=CHANNEL)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("entity_event_listener_stopped")

    async def _listen_loop(self) -> None:
        while self._running:
            try:
                async with self._pool.connection() as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    logger.info("entity_event_listener_subscribed", channel=CHANNEL)

                    async for notify in conn.notifies():
                        if not self._running:
                            break
                        await self._handle_notification(notify.payload)

            except asyncio.CancelledError:
                raise
            except Exception:
                if self._running:
                    logger.exception(
                        "entity_event_listener_error",
                        reconnect_in=RECONNECT_DELAY,
                    )
                    await asyncio.sleep(RECONNECT_DELAY)

    async def _handle_notification(self, payload: str) -> None:
        try:
            data = json.loads(payload)
            event_type = data.get("event", "unknown")
            event_payload = data.get("payload", {})
            company_id = data.get("company_id") or event_payload.get("company_id")

            await self._sse_manager.publish(
                SSEEvent(
                    event_type=event_type,
                    data=event_payload,
                    company_id=company_id,
                )
            )

            logger.debug(
                "entity_event_forwarded",
                event_type=event_type,
                company_id=company_id,
                payload_keys=list(event_payload.keys()),
            )

        except json.JSONDecodeError:
            logger.warning("entity_event_invalid_json", payload=payload[:200])
        except Exception:
            logger.exception("entity_event_forward_error")
