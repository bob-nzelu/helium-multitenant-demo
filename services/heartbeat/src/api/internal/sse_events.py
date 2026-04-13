"""
SSE Event Streaming API (P2-D — Backwards Compatibility)

Server-Sent Events endpoints for real-time event monitoring.
This is the P2-D internal event bus endpoint. It does NOT use JWT
auth, event_ledger, or spec-compliant wire format.

NOTE (SSE Spec v1.1): The primary SSE endpoint per the Helium SSE
Specification is GET /api/sse/stream (authenticated, ledger-backed).
This P2-D endpoint is retained for backwards compatibility with
existing consumers. New consumers SHOULD use /api/sse/stream.

Endpoints:
    GET /api/v1/events/stream   — General SSE stream (all events, pattern=*)
    GET /api/v1/events/blobs    — SSE stream for blob events (pattern=blob.*)
    GET /api/v1/events/status   — Event bus status (subscriber count, event count)
"""

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ...events import get_event_bus


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["SSE Events"])

KEEPALIVE_INTERVAL = 15  # seconds (SSE Spec Section 2.6)


def _sse_stream_response(pattern: str) -> StreamingResponse:
    """
    Create an SSE StreamingResponse for the given fnmatch pattern.

    Shared by /stream and /blobs endpoints. Registers a subscriber on the
    EventBus, yields SSE-formatted events, and sends keepalive comments
    every KEEPALIVE_INTERVAL seconds.
    """

    async def stream_with_keepalive():
        bus = get_event_bus()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async with bus._lock:
            if pattern not in bus._subscribers:
                bus._subscribers[pattern] = set()
            bus._subscribers[pattern].add(queue)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_INTERVAL
                    )
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            async with bus._lock:
                if pattern in bus._subscribers:
                    bus._subscribers[pattern].discard(queue)
                    if not bus._subscribers[pattern]:
                        del bus._subscribers[pattern]

    return StreamingResponse(
        stream_with_keepalive(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream")
async def stream_all_events(
    pattern: str = Query("*", description="Event pattern filter (fnmatch, default: all events)"),
):
    """
    General-purpose SSE stream for all HeartBeat events.

    Events include:
        blob.*     — Blob lifecycle (registered, status_changed, finalized, error)
        schema.*   — Schema updates (schema.updated)

    The Float SDK connects here to receive all event types on a single stream.
    Use the `pattern` query param to filter (fnmatch syntax).

    Keepalive: sends `:keepalive` comment every 30s.
    """
    return _sse_stream_response(pattern)


@router.get("/blobs")
async def stream_blob_events(
    pattern: str = Query("blob.*", description="Event pattern filter (fnmatch)"),
):
    """
    SSE stream for blob lifecycle events (backwards-compatible).

    Events:
        blob.registered      — New blob metadata registered
        blob.status_changed  — Blob status updated
        blob.finalized       — Blob reached terminal state
        blob.error           — Error during blob processing

    Query params:
        pattern: fnmatch pattern (default "blob.*")

    Keepalive: sends `:keepalive` comment every 30s.
    """
    return _sse_stream_response(pattern)


@router.get("/status")
async def event_bus_status():
    """Event bus health — subscriber count, total events published."""
    bus = get_event_bus()
    return {
        "subscribers": bus.subscriber_count,
        "total_events_published": bus.event_count,
    }
