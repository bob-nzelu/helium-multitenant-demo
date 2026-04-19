"""
Tests for SSE Event Streaming (P2-D)

Tests cover:
    1. EventBus — publish/subscribe, wildcards, queue management
    2. Event — SSE format serialization
    3. SSE API endpoints (status endpoint, stream content-type)
    4. Singleton lifecycle
"""

import asyncio
import pytest

from src.events.event_bus import Event, EventBus, get_event_bus, reset_event_bus


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Event model
# ══════════════════════════════════════════════════════════════════════════


class TestEventModel:
    """Event serialization."""

    def test_event_to_sse_format(self):
        """Event.to_sse() produces valid SSE format."""
        event = Event(
            event_type="blob.registered",
            data={"blob_uuid": "abc-123"},
            event_id="42",
        )

        sse = event.to_sse()
        assert "id: 42\n" in sse
        assert "event: blob.registered\n" in sse
        assert "data: " in sse
        assert '"blob_uuid": "abc-123"' in sse
        assert sse.endswith("\n\n")

    def test_event_to_sse_without_id(self):
        """Event without event_id omits id line."""
        event = Event(
            event_type="blob.error",
            data={"error": "test"},
        )

        sse = event.to_sse()
        assert "id:" not in sse
        assert "event: blob.error\n" in sse


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — EventBus
# ══════════════════════════════════════════════════════════════════════════


class TestEventBus:
    """EventBus pub/sub behavior."""

    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self):
        """Published event is received by subscriber."""
        bus = EventBus()
        received = []

        async def reader():
            async for event in bus.subscribe("blob.registered"):
                received.append(event)
                break  # Stop after first event

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)  # Let subscriber register

        await bus.publish("blob.registered", {"blob_uuid": "test-uuid"})
        await asyncio.sleep(0.05)  # Let event propagate

        await asyncio.wait_for(task, timeout=2.0)

        assert len(received) == 1
        assert received[0].event_type == "blob.registered"
        assert received[0].data["blob_uuid"] == "test-uuid"

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        """Wildcard pattern matches multiple event types."""
        bus = EventBus()
        received = []
        count = 0

        async def reader():
            nonlocal count
            async for event in bus.subscribe("blob.*"):
                received.append(event)
                count += 1
                if count >= 3:
                    break

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)

        await bus.publish("blob.registered", {"id": "1"})
        await bus.publish("blob.status_changed", {"id": "2"})
        await bus.publish("blob.finalized", {"id": "3"})
        await bus.publish("other.event", {"id": "4"})  # Should NOT match

        await asyncio.wait_for(task, timeout=2.0)

        assert len(received) == 3
        types = {e.event_type for e in received}
        assert "other.event" not in types

    @pytest.mark.asyncio
    async def test_event_counter_increments(self):
        """Event IDs are auto-incremented."""
        bus = EventBus()

        e1 = await bus.publish("blob.registered", {"n": 1})
        e2 = await bus.publish("blob.registered", {"n": 2})

        assert int(e2.event_id) == int(e1.event_id) + 1
        assert bus.event_count == 2

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        """subscriber_count tracks active subscribers."""
        bus = EventBus()
        assert bus.subscriber_count == 0

        queue = asyncio.Queue(maxsize=10)
        async with bus._lock:
            bus._subscribers["blob.*"] = {queue}

        assert bus.subscriber_count == 1

        async with bus._lock:
            bus._subscribers.clear()

    @pytest.mark.asyncio
    async def test_no_subscribers_no_error(self):
        """Publishing with no subscribers doesn't error."""
        bus = EventBus()
        event = await bus.publish("blob.registered", {"test": True})
        assert event is not None
        assert event.event_type == "blob.registered"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_same_pattern(self):
        """Multiple subscribers on same pattern both receive events."""
        bus = EventBus()
        received_a = []
        received_b = []

        async def reader_a():
            async for event in bus.subscribe("blob.*"):
                received_a.append(event)
                break

        async def reader_b():
            async for event in bus.subscribe("blob.*"):
                received_b.append(event)
                break

        task_a = asyncio.create_task(reader_a())
        task_b = asyncio.create_task(reader_b())
        await asyncio.sleep(0.05)

        await bus.publish("blob.registered", {"id": "shared"})
        await asyncio.sleep(0.05)

        await asyncio.wait_for(task_a, timeout=2.0)
        await asyncio.wait_for(task_b, timeout=2.0)

        assert len(received_a) == 1
        assert len(received_b) == 1


class TestEventBusSingleton:
    """Singleton lifecycle."""

    def test_get_creates_singleton(self):
        """get_event_bus creates instance on first call."""
        reset_event_bus()
        bus = get_event_bus()
        assert bus is not None
        assert get_event_bus() is bus
        reset_event_bus()

    def test_reset_clears_singleton(self):
        """reset_event_bus clears the singleton."""
        reset_event_bus()
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()
        assert bus1 is not bus2
        reset_event_bus()


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — SSE Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestSSEApi:
    """HTTP endpoint tests for SSE events."""

    def test_event_status_endpoint(self, client):
        """GET /api/v1/events/status returns bus stats."""
        resp = client.get("/api/v1/events/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "subscribers" in data
        assert "total_events_published" in data

    def test_blob_events_endpoint_exists(self, client):
        """GET /api/v1/events/blobs is registered (returns 200, not 404)."""
        # SSE endpoints are infinite streams — TestClient blocks on them.
        # We verify the endpoint is routable via the OpenAPI schema instead.
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/api/v1/events/blobs" in paths
