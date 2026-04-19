"""
HeartBeat SSE Producer -- Shared Transport Layer

Single authenticated SSE endpoint that multiplexes all component events
with server-side permission filtering.

Architecture:
    [Auth]----------+
    [Blob Service]--+
    [Registry]------+---> [Internal Event Bus] ---> [SSE Router] ---> per-client filter ---> SSE stream
    [Platform Svc]--+         (asyncio queue)            |
                                                   user permissions
                                                   from JWT claims

Event types on the HeartBeat SSE stream:
    auth.cipher_refresh     -- SQLCipher key material (every ~9 min)
    permission.changed      -- User role/permission updated
    session.revoked         -- Session forcibly terminated
    blob.uploaded           -- New blob registered
    blob.status_changed     -- Blob processing status update
    config.changed          -- Service configuration updated
    notification.new        -- New notification for user
    notification.updated    -- Notification status change
    service.health_changed  -- Service health status change

Reference: HEARTBEAT_OVERVIEW_V2.md Section 5
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional, Set

from ..auth.jwt_manager import get_jwt_manager
from ..handlers.auth_handler import get_cipher_text_for_user


logger = logging.getLogger(__name__)


class SSEEvent:
    """A single SSE event ready for delivery (SSE Spec Section 1)."""

    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        sequence: int = 0,
        target_user_id: Optional[str] = None,
        target_role: Optional[str] = None,
        required_permission: Optional[str] = None,
    ):
        """
        Args:
            event_type: SSE event type (e.g., "blob.uploaded")
            data: Event payload dict (the entity-specific data)
            sequence: Monotonic sequence from event_ledger
            target_user_id: If set, only deliver to this user
            target_role: If set, only deliver to users with this role or higher
            required_permission: If set, only deliver to users with this permission
        """
        self.event_type = event_type
        self.data = data
        self.sequence = sequence
        self.target_user_id = target_user_id
        self.target_role = target_role
        self.required_permission = required_permission
        self.created_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

    def format_sse(self) -> str:
        """
        Format as SSE wire protocol (SSE Spec Section 1.1).

        Wire format:
            id: {sequence}
            event: {event_type}
            data: {"sequence": N, "event_type": "...", "data": {...},
                   "timestamp": "...", "source": "heartbeat"}
        """
        payload = json.dumps({
            "sequence": self.sequence,
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.created_at,
            "source": "heartbeat",
        })
        return f"id: {self.sequence}\nevent: {self.event_type}\ndata: {payload}\n\n"


# Role hierarchy for permission filtering
ROLE_HIERARCHY = {
    "Owner": 4,
    "Admin": 3,
    "Operator": 2,
    "Support": 1,
}


class SSEEventBus:
    """
    Internal event bus for publishing events to SSE clients.

    Components (Auth, Blob, Registry, Platform) publish events here.
    The SSE router reads from per-client queues with permission filtering.

    Zombie eviction (SSE Spec Section 9.1):
    Tracks consecutive QueueFull per client. If a client's queue is
    full 3 times within 60 seconds, the client is evicted.
    """

    ZOMBIE_WINDOW_SECONDS = 60
    ZOMBIE_THRESHOLD = 3

    def __init__(self):
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._subscriber_claims: Dict[str, Dict[str, Any]] = {}
        self._queue_full_tracker: Dict[str, list] = {}  # client_id -> [timestamps]
        self._lock = asyncio.Lock()

    async def subscribe(
        self, client_id: str, claims: Dict[str, Any]
    ) -> asyncio.Queue:
        """
        Register a new SSE client.

        Args:
            client_id: Unique identifier for this SSE connection
            claims: JWT claims from the authenticated user

        Returns:
            asyncio.Queue to read events from
        """
        queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers[client_id] = queue
            self._subscriber_claims[client_id] = claims
        logger.info(
            f"SSE client subscribed: {client_id} "
            f"user={claims.get('sub')}"
        )
        return queue

    async def unsubscribe(self, client_id: str) -> None:
        """Remove an SSE client."""
        async with self._lock:
            self._subscribers.pop(client_id, None)
            self._subscriber_claims.pop(client_id, None)
            self._queue_full_tracker.pop(client_id, None)
        logger.info(f"SSE client unsubscribed: {client_id}")

    async def publish(self, event: SSEEvent) -> int:
        """
        Publish an event to all eligible subscribers.

        Applies permission filtering based on event targeting and
        subscriber JWT claims. Evicts zombie clients after 3
        consecutive QueueFull within 60 seconds (SSE Spec Section 9.1).

        Returns:
            Number of clients that received the event.
        """
        delivered = 0
        evict_list = []
        now = time.time()

        async with self._lock:
            for client_id, queue in list(self._subscribers.items()):
                claims = self._subscriber_claims.get(client_id, {})

                if not self._should_deliver(event, claims):
                    continue

                try:
                    queue.put_nowait(event)
                    delivered += 1
                    # Successful delivery resets the tracker
                    self._queue_full_tracker.pop(client_id, None)
                except asyncio.QueueFull:
                    logger.warning(
                        f"SSE queue full for client {client_id}, "
                        f"dropping event {event.event_type} "
                        f"seq={event.sequence}"
                    )
                    # Track consecutive failures for zombie detection
                    tracker = self._queue_full_tracker.setdefault(client_id, [])
                    tracker.append(now)
                    # Prune entries outside the window
                    cutoff = now - self.ZOMBIE_WINDOW_SECONDS
                    self._queue_full_tracker[client_id] = [
                        t for t in tracker if t > cutoff
                    ]
                    if len(self._queue_full_tracker[client_id]) >= self.ZOMBIE_THRESHOLD:
                        evict_list.append(client_id)

            # Evict zombies (send None sentinel, remove from registry)
            if evict_list:
                try:
                    from ..observability.metrics import SSE_EVENTS_DROPPED
                    for _ in evict_list:
                        SSE_EVENTS_DROPPED.labels(
                            service="heartbeat", reason="eviction"
                        ).inc()
                except Exception:
                    pass

            for client_id in evict_list:
                logger.warning(
                    f"Evicting slow consumer {client_id}: "
                    f"queue full {self.ZOMBIE_THRESHOLD}x "
                    f"in {self.ZOMBIE_WINDOW_SECONDS}s"
                )
                queue = self._subscribers.get(client_id)
                if queue:
                    try:
                        # Force-put None sentinel (drop oldest if needed)
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        queue.put_nowait(None)  # Sentinel: close connection
                    except Exception:
                        pass
                self._subscribers.pop(client_id, None)
                self._subscriber_claims.pop(client_id, None)
                self._queue_full_tracker.pop(client_id, None)

        return delivered

    def _should_deliver(
        self, event: SSEEvent, claims: Dict[str, Any]
    ) -> bool:
        """Check if an event should be delivered to a specific client."""
        user_id = claims.get("sub", "")
        role = claims.get("role", "")
        permissions = claims.get("permissions", [])

        # User-targeted events
        if event.target_user_id and event.target_user_id != user_id:
            return False

        # Role-filtered events
        if event.target_role:
            user_level = ROLE_HIERARCHY.get(role, 0)
            required_level = ROLE_HIERARCHY.get(event.target_role, 0)
            if user_level < required_level:
                return False

        # Permission-filtered events
        if event.required_permission:
            if event.required_permission not in permissions and "*" not in permissions:
                return False

        return True

    def get_queue_depth(self, client_id: str) -> int:
        """Return current queue depth for a client (0 if not found)."""
        queue = self._subscribers.get(client_id)
        return queue.qsize() if queue else 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# -- Cipher Text Background Task --------------------------------------

class CipherTextScheduler:
    """
    Pushes auth.cipher_refresh events every ~9 minutes to each
    connected user via the SSE event bus.

    Each push includes:
      - cipher_text: the new SQLCipher key material
      - valid_until: ISO timestamp when the key expires
      - window_seconds: rotation interval (from config)
      - ttl_seconds: how long Float should wait before assuming
        disconnection and triggering a data wipe (2x window + 20s buffer)
      - rotation_sequence: monotonic per-user counter so Float can
        detect missed rotations (gap > 1 = missed keys)
    """

    def __init__(self, event_bus: SSEEventBus, window_seconds: int = 540):
        self._event_bus = event_bus
        self._window_seconds = window_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Per-user monotonic rotation counter.
        # Resets on service restart, but Float detects gaps
        # (gap > 1), not absolute values.
        self._rotation_sequences: Dict[str, int] = {}

    async def start(self):
        """Start the cipher text refresh loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Cipher text scheduler started "
            f"(window={self._window_seconds}s)"
        )

    async def stop(self):
        """Stop the cipher text refresh loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Cipher text scheduler stopped")

    async def _loop(self):
        """Main loop: push cipher text to all connected users."""
        while self._running:
            try:
                # Sleep until next window boundary
                now_ts = time.time()
                current_window = int(now_ts // self._window_seconds)
                next_window_ts = (current_window + 1) * self._window_seconds
                sleep_time = max(1, next_window_ts - now_ts)

                await asyncio.sleep(sleep_time)

                if not self._running:
                    break

                # Push cipher text to each connected user
                await self._push_cipher_texts()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cipher text scheduler error: {e}")
                await asyncio.sleep(10)

    async def _push_cipher_texts(self):
        """Push cipher text events to all connected users."""
        # Get unique user IDs from current subscribers
        user_ids: Set[str] = set()
        async with self._event_bus._lock:
            for claims in self._event_bus._subscriber_claims.values():
                uid = claims.get("sub")
                if uid:
                    user_ids.add(uid)

        # TTL = 2x window + 20s buffer.
        # Float starts a watchdog timer on each refresh. If the next
        # refresh doesn't arrive within ttl_seconds, Float assumes
        # HeartBeat is unreachable and triggers a security wipe
        # (clear in-memory data, lock UI to auth screen).
        ttl_seconds = (self._window_seconds * 2) + 20

        for user_id in user_ids:
            try:
                cipher_data = await get_cipher_text_for_user(user_id)
                if cipher_data:
                    # Increment per-user rotation sequence
                    seq = self._rotation_sequences.get(user_id, 0) + 1
                    self._rotation_sequences[user_id] = seq

                    cipher_data["ttl_seconds"] = ttl_seconds
                    cipher_data["rotation_sequence"] = seq

                    from .publish import publish_event
                    await publish_event(
                        "auth.cipher_refresh",
                        cipher_data,
                        target_user_id=user_id,
                    )
            except Exception as e:
                logger.error(
                    f"Failed to push cipher text for user {user_id}: {e}"
                )

        if user_ids:
            logger.debug(
                f"Cipher text pushed to {len(user_ids)} user(s) "
                f"ttl={ttl_seconds}s"
            )


# -- Singleton ---------------------------------------------------------

_event_bus: Optional[SSEEventBus] = None
_cipher_scheduler: Optional[CipherTextScheduler] = None


def get_event_bus() -> SSEEventBus:
    """Get singleton SSEEventBus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = SSEEventBus()
    return _event_bus


def get_cipher_scheduler() -> CipherTextScheduler:
    """Get singleton CipherTextScheduler instance."""
    global _cipher_scheduler
    if _cipher_scheduler is None:
        from ..config import get_config
        config = get_config()
        _cipher_scheduler = CipherTextScheduler(
            event_bus=get_event_bus(),
            window_seconds=config.cipher_window_seconds,
        )
    return _cipher_scheduler


def reset_sse_producer() -> None:
    """Reset singletons (for testing/shutdown)."""
    global _event_bus, _cipher_scheduler
    _event_bus = None
    _cipher_scheduler = None
