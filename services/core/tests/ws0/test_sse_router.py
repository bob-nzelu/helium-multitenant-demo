"""Tests for SSE router endpoint — GET /sse/stream."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from src.sse.manager import SSEConnectionManager
from src.sse.models import SSEEvent
from src.sse.router import router as sse_router


def _make_sse_app() -> tuple[FastAPI, SSEConnectionManager]:
    """Create minimal app with SSE router and real manager."""
    app = FastAPI()
    app.include_router(sse_router)

    manager = SSEConnectionManager(buffer_size=10, heartbeat_interval=30)
    app.state.sse_manager = manager
    return app, manager


@pytest.mark.asyncio
class TestSSERouter:
    """Test the SSE streaming endpoint."""

    async def test_sse_stream_connects_and_receives_event(self):
        """GET /sse/stream should return text/event-stream and deliver events."""
        app, manager = _make_sse_app()

        async def publish_then_close():
            """Publish one event, then drain (sends None sentinel)."""
            await asyncio.sleep(0.2)
            await manager.publish(SSEEvent(event_type="test.created", data={"id": "42"}))
            await asyncio.sleep(0.2)
            await manager.drain()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(publish_then_close())
            resp = await client.get("/sse/stream", timeout=5.0)
            await task

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = resp.text
        assert "test.created" in body
        assert "42" in body

    async def test_sse_stream_with_data_uuid_filter(self):
        """Query param data_uuid should be accepted."""
        app, manager = _make_sse_app()

        async def publish_then_close():
            await asyncio.sleep(0.2)
            await manager.publish(
                SSEEvent(event_type="a", data={"v": 1}, data_uuid="uuid-1")
            )
            await manager.publish(
                SSEEvent(event_type="b", data={"v": 2}, data_uuid="uuid-2")
            )
            await asyncio.sleep(0.1)
            await manager.drain()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(publish_then_close())
            resp = await client.get(
                "/sse/stream",
                params={"data_uuid": "uuid-1"},
                timeout=5.0,
            )
            await task

        assert resp.status_code == 200
        body = resp.text
        # Should contain uuid-1 event but NOT uuid-2
        assert '"v": 1' in body or '"v":1' in body

    async def test_sse_stream_heartbeat_as_comment(self):
        """Heartbeat events should render as SSE comments."""
        app, manager = _make_sse_app()

        async def send_heartbeat_then_close():
            await asyncio.sleep(0.2)
            # Manually push a heartbeat event
            event = SSEEvent(event_type="__heartbeat__", data={})
            for c in manager._connections.values():
                await c.queue.put(event)
            await asyncio.sleep(0.1)
            await manager.drain()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            task = asyncio.create_task(send_heartbeat_then_close())
            resp = await client.get("/sse/stream", timeout=5.0)
            await task

        assert resp.status_code == 200
        # SSE comment format: ": heartbeat\n"
        assert "heartbeat" in resp.text
