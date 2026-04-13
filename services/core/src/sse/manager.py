"""
SSE Connection Manager

Per SSE_SPEC v1.1:
- Ring buffer of 1000 events (Section 4.4).
- Heartbeat every 15 seconds (Section 2.6).
- Tenant isolation via company_id (Section 11.1).
- Zombie eviction: 3x QueueFull in 60s (Section 9.1).
- Prometheus metrics (Section 10.1).
- Event envelope: sequence, event_type, data, timestamp, source (Section 1.2).
"""

from __future__ import annotations

import asyncio
import collections
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any

import structlog
from prometheus_client import Counter, Gauge
from uuid6 import uuid7

from src.sse.models import SSEClient, SSEEvent

logger = structlog.get_logger()

# ── Prometheus Metrics (Section 10.1) ────────────────────────────────────

sse_connections_active = Gauge(
    "helium_sse_connections_active",
    "Number of active SSE connections",
    ["service"],
)

sse_events_published_total = Counter(
    "helium_sse_events_published_total",
    "Total events published",
    ["service", "event_type"],
)

sse_events_dropped_total = Counter(
    "helium_sse_events_dropped_total",
    "Events dropped (queue_full, eviction)",
    ["service", "reason"],
)

sse_catchup_requests_total = Counter(
    "helium_sse_catchup_requests_total",
    "Catchup endpoint calls",
    ["service"],
)

sse_reconnections_total = Counter(
    "helium_sse_reconnections_total",
    "Client reconnections (Last-Event-ID present)",
    ["service"],
)

sse_ledger_size = Gauge(
    "helium_sse_ledger_size",
    "Current row count in event_ledger",
    ["service"],
)

# Zombie eviction constants (Section 9.1)
ZOMBIE_QUEUE_FULL_THRESHOLD = 3
ZOMBIE_WINDOW_SECONDS = 60


class SSEConnectionManager:
    """
    Manages SSE client connections, event publishing, and replay.

    Usage:
        manager = SSEConnectionManager(buffer_size=1000, heartbeat_interval=15)
        await manager.start_heartbeat()

        # In endpoint:
        client = manager.subscribe(company_id="tenant_001", data_uuid_filter="abc")
        # ... yield events from client.queue ...
        manager.unsubscribe(client.client_id)

        # From any workstream:
        await manager.publish(SSEEvent(event_type="invoice.created", data={...}))
    """

    def __init__(
        self,
        buffer_size: int = 1000,
        heartbeat_interval: int = 15,
        event_ledger=None,
        pool=None,
    ):
        self._connections: dict[str, SSEClient] = {}
        self._ring_buffer: collections.deque[SSEEvent] = collections.deque(
            maxlen=buffer_size
        )
        self._sequence: int = 0
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._event_ledger = event_ledger  # EventLedger instance
        self._pool = pool  # AsyncConnectionPool for ledger writes

        # Zombie tracking: client_id → list of QueueFull timestamps
        self._queue_full_tracker: dict[str, list[float]] = {}

    def set_ledger(self, event_ledger, pool) -> None:
        """Attach ledger and pool after construction (avoids circular deps)."""
        self._event_ledger = event_ledger
        self._pool = pool

    async def sync_sequence_from_ledger(self) -> None:
        """
        Initialize in-memory sequence counter from the ledger's max sequence.

        Must be called after set_ledger() during startup. Prevents the
        in-memory counter from resetting to 0 on server restart, which
        would create duplicate sequence IDs.
        """
        if self._pool is None:
            return
        try:
            async with self._pool.connection() as conn:
                row = await conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM core.event_ledger"
                )
                max_seq = (await row.fetchone())[0]
                if max_seq > self._sequence:
                    self._sequence = max_seq
                    logger.info("sse_sequence_synced_from_ledger", sequence=max_seq)
        except Exception:
            logger.exception("sse_sequence_sync_failed")

    @property
    def connection_count(self) -> int:
        """Number of active SSE connections."""
        return len(self._connections)

    @property
    def buffer_size(self) -> int:
        """Current number of events in the ring buffer."""
        return len(self._ring_buffer)

    @property
    def current_sequence(self) -> int:
        """Current sequence counter value."""
        return self._sequence

    def subscribe(
        self,
        company_id: str | None = None,
        data_uuid_filter: str | None = None,
        pattern_filter: str | None = None,
        jwt_exp: int = 0,
    ) -> SSEClient:
        """
        Register a new SSE client.

        Args:
            company_id: Tenant ID from JWT claims (Section 11.1).
            data_uuid_filter: If set, client only receives events matching this data_uuid.
            pattern_filter: fnmatch pattern for event_type filtering (Section 2.3).
            jwt_exp: JWT expiry unix timestamp for per-write checks.

        Returns:
            SSEClient with a queue to read events from.
        """
        client_id = str(uuid7())
        client = SSEClient(
            client_id=client_id,
            company_id=company_id,
            data_uuid_filter=data_uuid_filter,
            pattern_filter=pattern_filter,
            jwt_exp=jwt_exp,
        )
        self._connections[client_id] = client
        sse_connections_active.labels(service="core").set(len(self._connections))
        logger.info(
            "sse_client_connected",
            client_id=client_id,
            company_id=company_id,
            source="core",
            filter=data_uuid_filter,
            pattern=pattern_filter,
        )
        return client

    def unsubscribe(self, client_id: str) -> None:
        """Remove a client from the connection registry."""
        client = self._connections.pop(client_id, None)
        if client:
            duration = time.monotonic() - client.connected_at
            self._queue_full_tracker.pop(client_id, None)
            sse_connections_active.labels(service="core").set(len(self._connections))
            logger.info(
                "sse_client_disconnected",
                client_id=client_id,
                duration_seconds=round(duration, 1),
            )

    async def publish(self, event: SSEEvent) -> None:
        """
        Publish an event to all matching connected clients.

        Per Section 1.2: Assigns monotonic sequence, sets timestamp and source.
        Per Section 1.3: SSE id field MUST equal event_ledger.sequence.
        Per Section 9.1: Tracks QueueFull for zombie eviction.

        Sequence assignment:
        - Events with company_id: ledger BIGSERIAL is the authoritative sequence.
          Written to ledger FIRST, returned sequence becomes event.id.
        - Events without company_id (system events): in-memory counter only.
          These skip the ledger and are not available for catchup.
        """
        # Set envelope fields if not already set (Section 1.2)
        if event.timestamp is None:
            event.timestamp = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z"
        if event.source is None:
            event.source = "core"

        # Assign sequence — ledger is authoritative (Section 1.3)
        uses_ledger = (
            self._event_ledger is not None
            and self._pool is not None
            and event.company_id  # System events without company_id skip ledger
        )

        if uses_ledger:
            try:
                ledger_seq = await self._event_ledger.write(
                    pool=self._pool,
                    event_type=event.event_type,
                    data=event.data,
                    timestamp=event.timestamp,
                    company_id=event.company_id,
                    data_uuid=event.data_uuid,
                )
                event.id = ledger_seq
                # Keep in-memory counter at or ahead of ledger
                if ledger_seq > self._sequence:
                    self._sequence = ledger_seq
            except Exception:
                # Ledger write failed — fall back to in-memory sequence.
                # Event is delivered to live clients but NOT in catchup.
                self._sequence += 1
                event.id = self._sequence
                logger.exception(
                    "ledger_write_failed",
                    sequence=event.id,
                    event_type=event.event_type,
                )
        else:
            self._sequence += 1
            event.id = self._sequence

        # Store in ring buffer for replay
        self._ring_buffer.append(event)

        # Metric: events published
        sse_events_published_total.labels(
            service="core", event_type=event.event_type
        ).inc()

        # Push to matching clients
        to_evict: list[str] = []
        for client in list(self._connections.values()):
            if _client_matches(client, event):
                try:
                    client.queue.put_nowait(event)
                except asyncio.QueueFull:
                    if self._track_queue_full(client.client_id):
                        to_evict.append(client.client_id)
                    else:
                        sse_events_dropped_total.labels(
                            service="core", reason="queue_full"
                        ).inc()
                        logger.warning(
                            "sse_event_dropped_queue_full",
                            client_id=client.client_id,
                            event_type=event.event_type,
                            sequence=event.id,
                        )

        # Evict zombies (Section 9.1)
        for client_id in to_evict:
            await self._evict_zombie(client_id)

    def _track_queue_full(self, client_id: str) -> bool:
        """
        Track QueueFull occurrences. Returns True if client should be evicted.

        Per Section 9.1: 3 consecutive QueueFull within 60 seconds → evict.
        """
        now = time.monotonic()
        timestamps = self._queue_full_tracker.setdefault(client_id, [])
        timestamps.append(now)

        # Prune entries outside the 60-second window
        cutoff = now - ZOMBIE_WINDOW_SECONDS
        timestamps[:] = [t for t in timestamps if t >= cutoff]

        return len(timestamps) >= ZOMBIE_QUEUE_FULL_THRESHOLD

    async def _evict_zombie(self, client_id: str) -> None:
        """
        Evict a slow consumer (Section 9.1).

        Sends None sentinel, removes from registry, logs at WARN.
        """
        client = self._connections.get(client_id)
        if not client:
            return

        logger.warning(
            "sse_zombie_evicted",
            client_id=client_id,
            reason=f"queue full {ZOMBIE_QUEUE_FULL_THRESHOLD}x in {ZOMBIE_WINDOW_SECONDS}s",
        )

        # Send close sentinel
        try:
            client.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Remove from registry
        self.unsubscribe(client_id)
        sse_events_dropped_total.labels(service="core", reason="eviction").inc()

    async def replay(self, client_id: str, last_event_id: int) -> None:
        """
        Replay missed events from ring buffer to a client.

        Events with id > last_event_id are pushed to the client's queue.
        If last_event_id has been evicted from the buffer, replay starts
        from the oldest available event.
        """
        client = self._connections.get(client_id)
        if not client:
            return

        sse_reconnections_total.labels(service="core").inc()

        for event in self._ring_buffer:
            if event.id is not None and event.id > last_event_id:
                if _client_matches(client, event):
                    try:
                        client.queue.put_nowait(event)
                    except asyncio.QueueFull:
                        break

    async def start_heartbeat(self) -> None:
        """Start the background heartbeat task."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("sse_heartbeat_started", interval=self._heartbeat_interval)

    async def stop_heartbeat(self) -> None:
        """Stop the background heartbeat task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info("sse_heartbeat_stopped")

    async def drain(self) -> None:
        """Signal all clients to disconnect (send None sentinel)."""
        for client in list(self._connections.values()):
            try:
                client.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._connections.clear()
        self._queue_full_tracker.clear()
        sse_connections_active.labels(service="core").set(0)

    async def _heartbeat_loop(self) -> None:
        """Send keepalive comments to all clients periodically (Section 2.6)."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            heartbeat = SSEEvent(
                event_type="__heartbeat__",
                data={},
            )
            to_evict: list[str] = []
            for client in list(self._connections.values()):
                try:
                    client.queue.put_nowait(heartbeat)
                except asyncio.QueueFull:
                    if self._track_queue_full(client.client_id):
                        to_evict.append(client.client_id)

            for client_id in to_evict:
                await self._evict_zombie(client_id)


def _client_matches(client: SSEClient, event: SSEEvent) -> bool:
    """
    Check if a client should receive this event.

    Per Section 11.1: Tenant isolation via company_id.
    Per Section 2.3: fnmatch pattern filtering on event_type.
    """
    # Heartbeats go to everyone
    if event.event_type == "__heartbeat__":
        return True

    # Tenant isolation (Section 11.1)
    if client.company_id and event.company_id:
        if client.company_id != event.company_id:
            return False

    # data_uuid filter
    if client.data_uuid_filter is not None:
        if event.data_uuid != client.data_uuid_filter:
            return False

    # Pattern filter (Section 2.3)
    if client.pattern_filter is not None:
        if not fnmatch(event.event_type, client.pattern_filter):
            return False

    return True
