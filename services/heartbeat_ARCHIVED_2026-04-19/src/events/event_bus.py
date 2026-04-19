"""
In-Process Event Bus (P2-D)

Simple pub/sub for internal events. Subscribers receive events via
asyncio queues, enabling SSE streaming endpoints.

Event types (blob lifecycle):
    blob.registered       — New blob metadata registered
    blob.status_changed   — Blob status updated (preview_pending → processing → etc.)
    blob.finalized        — Blob reached terminal state (stored/failed/rejected)
    blob.error            — Error during blob processing

Usage:
    bus = get_event_bus()

    # Publisher (in a handler):
    await bus.publish("blob.registered", {"blob_uuid": "abc", "source": "relay"})

    # Subscriber (SSE endpoint):
    async for event in bus.subscribe("blob.*"):
        yield event
"""

import asyncio
import fnmatch
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, AsyncIterator, Set
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


@dataclass
class Event:
    """An event published to the bus."""
    event_type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: Optional[str] = None

    def to_sse(self) -> str:
        """Format as Server-Sent Event string."""
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        lines.append(f"event: {self.event_type}")
        lines.append(f"data: {json.dumps({'type': self.event_type, 'data': self.data, 'timestamp': self.timestamp})}")
        lines.append("")  # Trailing newline to end the event
        return "\n".join(lines) + "\n"


class EventBus:
    """
    In-process async event bus (P2-D — legacy).

    NOTE: This is the P2-D internal event bus. It is NOT the primary
    SSE transport. The primary spec-compliant SSE path is via
    src/sse/publish.py -> SSEEventBus -> /api/sse/stream.

    The event_counter is synced from the event_ledger on startup via
    sync_counter_from_ledger() so that P2-D SSE id: fields don't
    reset to 0 on service restart. However, P2-D SSE IDs are NOT
    suitable for Last-Event-ID replay — use /api/sse/stream for that.

    Supports wildcard subscriptions (e.g., "blob.*" matches "blob.registered").
    Each subscriber gets its own asyncio.Queue to avoid blocking.
    """

    def __init__(self):
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._event_counter = 0
        self._lock = asyncio.Lock()

    def sync_counter_from_ledger(self) -> None:
        """
        Sync _event_counter from MAX(sequence) in event_ledger.

        Call this on startup so P2-D SSE id: fields don't reset to 0
        after a service restart. The ledger is the authoritative source
        for monotonic sequence numbers.
        """
        try:
            import os
            import sqlite3
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "databases",
                "blob.db",
            )
            if not os.path.exists(db_path):
                return
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT MAX(sequence) FROM event_ledger"
                ).fetchone()
                if row and row[0] is not None:
                    self._event_counter = row[0]
                    logger.info(
                        f"P2-D EventBus counter synced from ledger: {self._event_counter}"
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to sync P2-D counter from ledger: {e}")

    async def publish(
        self, event_type: str, data: Dict[str, Any]
    ) -> Event:
        """
        Publish an event to all matching subscribers.

        Returns the published Event.
        """
        self._event_counter += 1
        event = Event(
            event_type=event_type,
            data=data,
            event_id=str(self._event_counter),
        )

        async with self._lock:
            for pattern, queues in self._subscribers.items():
                if fnmatch.fnmatch(event_type, pattern):
                    dead_queues = set()
                    for queue in queues:
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull:
                            dead_queues.add(queue)
                            logger.warning(f"Subscriber queue full, dropping event {event_type}")
                    queues -= dead_queues

        return event

    async def subscribe(
        self,
        pattern: str = "*",
        max_queue_size: int = 100,
    ) -> AsyncIterator[Event]:
        """
        Subscribe to events matching a pattern.

        Yields Event objects as they are published.
        Use "blob.*" to match all blob events, or "*" for all events.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)

        async with self._lock:
            if pattern not in self._subscribers:
                self._subscribers[pattern] = set()
            self._subscribers[pattern].add(queue)

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                if pattern in self._subscribers:
                    self._subscribers[pattern].discard(queue)
                    if not self._subscribers[pattern]:
                        del self._subscribers[pattern]

    @property
    def subscriber_count(self) -> int:
        """Total number of active subscriber queues."""
        return sum(len(queues) for queues in self._subscribers.values())

    @property
    def event_count(self) -> int:
        """Total events published since startup."""
        return self._event_counter


# ── Singleton ──────────────────────────────────────────────────────────

_event_bus_instance: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get singleton EventBus (creates on first call)."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = EventBus()
    return _event_bus_instance


def reset_event_bus() -> None:
    """Reset event bus singleton (for testing)."""
    global _event_bus_instance
    _event_bus_instance = None
