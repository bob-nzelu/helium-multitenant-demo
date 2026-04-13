"""Tests for SSE transport — manager publish/subscribe, filtering, replay, heartbeat."""

import asyncio

import pytest

from src.sse.manager import SSEConnectionManager
from src.sse.models import SSEClient, SSEEvent


@pytest.fixture
def manager():
    return SSEConnectionManager(buffer_size=10, heartbeat_interval=1)


class TestSSEConnectionManager:
    """Test SSE manager core functionality."""

    def test_subscribe_creates_client(self, manager):
        client = manager.subscribe()
        assert client.client_id in manager._connections
        assert manager.connection_count == 1

    def test_unsubscribe_removes_client(self, manager):
        client = manager.subscribe()
        manager.unsubscribe(client.client_id)
        assert manager.connection_count == 0

    def test_unsubscribe_nonexistent_is_noop(self, manager):
        manager.unsubscribe("nonexistent")
        assert manager.connection_count == 0

    def test_subscribe_with_filter(self, manager):
        client = manager.subscribe(data_uuid_filter="abc-123")
        assert client.data_uuid_filter == "abc-123"

    def test_multiple_subscribers(self, manager):
        c1 = manager.subscribe()
        c2 = manager.subscribe()
        assert manager.connection_count == 2
        manager.unsubscribe(c1.client_id)
        assert manager.connection_count == 1


@pytest.mark.asyncio
class TestSSEPublish:
    """Test event publishing and filtering."""

    async def test_publish_delivers_to_subscriber(self, manager):
        client = manager.subscribe()
        event = SSEEvent(event_type="invoice.created", data={"id": "123"})
        await manager.publish(event)
        received = client.queue.get_nowait()
        assert received.event_type == "invoice.created"
        assert received.data == {"id": "123"}
        assert received.id == 1

    async def test_publish_assigns_monotonic_ids(self, manager):
        client = manager.subscribe()
        await manager.publish(SSEEvent(event_type="a", data={}))
        await manager.publish(SSEEvent(event_type="b", data={}))
        e1 = client.queue.get_nowait()
        e2 = client.queue.get_nowait()
        assert e1.id == 1
        assert e2.id == 2

    async def test_publish_stores_in_ring_buffer(self, manager):
        await manager.publish(SSEEvent(event_type="test", data={}))
        assert manager.buffer_size == 1

    async def test_ring_buffer_evicts_old(self, manager):
        """Buffer size is 10 — publish 15 events, only 10 remain."""
        for i in range(15):
            await manager.publish(SSEEvent(event_type="test", data={"i": i}))
        assert manager.buffer_size == 10

    async def test_data_uuid_filter_matches(self, manager):
        client = manager.subscribe(data_uuid_filter="abc")
        await manager.publish(SSEEvent(event_type="test", data={}, data_uuid="abc"))
        await manager.publish(SSEEvent(event_type="test", data={}, data_uuid="xyz"))
        assert client.queue.qsize() == 1

    async def test_no_filter_receives_all(self, manager):
        client = manager.subscribe()
        await manager.publish(SSEEvent(event_type="test", data={}, data_uuid="abc"))
        await manager.publish(SSEEvent(event_type="test", data={}, data_uuid="xyz"))
        assert client.queue.qsize() == 2


@pytest.mark.asyncio
class TestSSEReplay:
    """Test reconnection replay from ring buffer."""

    async def test_replay_sends_missed_events(self, manager):
        # Publish 5 events with no subscribers
        for i in range(5):
            await manager.publish(SSEEvent(event_type="test", data={"i": i}))

        # Now subscribe and replay from event 2
        client = manager.subscribe()
        await manager.replay(client.client_id, last_event_id=2)

        # Should get events 3, 4, 5
        events = []
        while not client.queue.empty():
            events.append(client.queue.get_nowait())
        assert len(events) == 3
        assert events[0].id == 3

    async def test_replay_with_filter(self, manager):
        await manager.publish(SSEEvent(event_type="a", data={}, data_uuid="abc"))
        await manager.publish(SSEEvent(event_type="b", data={}, data_uuid="xyz"))
        await manager.publish(SSEEvent(event_type="c", data={}, data_uuid="abc"))

        client = manager.subscribe(data_uuid_filter="abc")
        await manager.replay(client.client_id, last_event_id=0)

        events = []
        while not client.queue.empty():
            events.append(client.queue.get_nowait())
        assert len(events) == 2
        assert all(e.data_uuid == "abc" for e in events)

    async def test_replay_nonexistent_client(self, manager):
        # Should not raise
        await manager.replay("nonexistent", last_event_id=0)


@pytest.mark.asyncio
class TestSSEHeartbeat:
    """Test heartbeat lifecycle."""

    async def test_heartbeat_starts_and_stops(self, manager):
        await manager.start_heartbeat()
        assert manager._heartbeat_task is not None
        assert not manager._heartbeat_task.done()
        await manager.stop_heartbeat()
        assert manager._heartbeat_task is None

    async def test_heartbeat_delivers_to_clients(self, manager):
        client = manager.subscribe()
        await manager.start_heartbeat()
        # Wait for at least one heartbeat (interval=1s)
        await asyncio.sleep(1.5)
        await manager.stop_heartbeat()
        assert not client.queue.empty()
        hb = client.queue.get_nowait()
        assert hb.event_type == "__heartbeat__"


@pytest.mark.asyncio
class TestSSEDrain:
    """Test drain sends shutdown sentinel."""

    async def test_drain_sends_none_to_all(self, manager):
        c1 = manager.subscribe()
        c2 = manager.subscribe()
        await manager.drain()
        assert c1.queue.get_nowait() is None
        assert c2.queue.get_nowait() is None
        assert manager.connection_count == 0
