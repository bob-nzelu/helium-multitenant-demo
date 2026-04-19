"""
SSE Stream API Router (SSE Spec Sections 2, 5, 6)

Endpoints:
    GET /api/sse/stream    — Authenticated SSE stream (Section 2)
    GET /api/sse/catchup   — Paginated event replay (Section 5)
    GET /api/sse/watermark — Reconciliation watermark (Section 6)

Multiplexes all HeartBeat component events with server-side
JWT permission filtering. Each client gets only events they
are authorized to see.

Wire format (SSE Spec Section 1):
    id: {sequence}
    event: {event_type}
    data: {"sequence": N, "event_type": "...", "data": {...},
           "timestamp": "...", "source": "heartbeat"}

Multiple Float instances may connect concurrently (up to 10 per
tenant per Section 12.2). Each gets its own subscriber queue.
"""

import asyncio
import fnmatch
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Query, Request
from uuid6 import uuid7
from fastapi.responses import StreamingResponse, JSONResponse

from ..auth.dependencies import get_current_user_token
from ..auth.jwt_manager import get_jwt_manager
from ..config import get_config
from ..database.event_ledger import get_event_ledger
from ..sse.producer import get_event_bus
from ..sse.publish import register_watermark_invalidation


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sse", tags=["sse"])

# ── SSE Stream Constants (SSE Spec) ────────────────────────────────────
KEEPALIVE_INTERVAL = 15.0  # seconds (Section 2.6)
RETRY_MS = 5000            # client retry interval (Section 1.1)

# ── Watermark Cache (Section 6.4) ──────────────────────────────────────
# In-memory cache: company_id -> (timestamp, response_dict)
_watermark_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
WATERMARK_TTL = 30.0  # seconds


def _invalidate_watermark(company_id: str) -> None:
    """Called by publish_event() to clear stale watermark cache."""
    _watermark_cache.pop(company_id, None)


# Register invalidation callback (runs at module import)
register_watermark_invalidation(_invalidate_watermark)


# ── Helpers ────────────────────────────────────────────────────────────

def _extract_claims(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT and return claims, or None if invalid."""
    try:
        jwt_mgr = get_jwt_manager()
        return jwt_mgr.verify_token(token)
    except Exception:
        return None


def _get_company_id(claims: Dict[str, Any]) -> str:
    """Extract company_id from JWT claims, fallback to config."""
    return claims.get("company_id", get_config().company_id)


# ── GET /api/sse/stream (Section 2) ───────────────────────────────────

@router.get("/stream")
async def sse_stream(
    request: Request,
    token: str = Depends(get_current_user_token),
    data_uuid: Optional[str] = Query(None, description="Filter to specific batch/entity group"),
    pattern: Optional[str] = Query(None, max_length=100, description="fnmatch pattern filter (e.g., blob.*)"),
):
    """
    Authenticated SSE stream endpoint (SSE Spec Section 2).

    Events (Section 3.2):
        blob.uploaded, blob.status_changed, config.updated,
        schema.updated, cache.refresh, auth.cipher_refresh,
        permission.changed, session.revoked, service.health_changed

    Query params:
        data_uuid: Filter to specific processing batch
        pattern: fnmatch filter on event_type (default: all)

    Headers required:
        Authorization: Bearer {jwt}
        Last-Event-ID: {sequence}  (on reconnect)

    Wire format:
        id: {sequence}
        event: {event_type}
        data: {"sequence": N, "event_type": "...", "data": {...},
               "timestamp": "...", "source": "heartbeat"}
        retry: 5000  (first event only)

    Keepalive: `: keepalive` comment every 15 seconds.
    JWT expiry: Checked on every write including keepalives (Section 11.3).
    Zombie eviction: Client evicted after 3x QueueFull in 60s (Section 9.1).
    """
    jwt_mgr = get_jwt_manager()

    # Verify token (reject expired/invalid)
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        return StreamingResponse(
            iter(['event: error\ndata: {"error": "TOKEN_INVALID"}\n\n']),
            media_type="text/event-stream",
            status_code=401,
        )

    company_id = _get_company_id(claims)
    client_id = f"sse-{uuid7()}"
    event_bus = get_event_bus()

    # Check for Last-Event-ID (reconnect — SSE Spec Section 7.1)
    last_event_id = request.headers.get("Last-Event-ID")

    # Prometheus metrics
    try:
        from ..observability.metrics import (
            SSE_CONNECTIONS_ACTIVE, SSE_RECONNECTIONS, SSE_CATCHUP_REQUESTS,
        )
        SSE_CONNECTIONS_ACTIVE.labels(service="heartbeat").inc()
        if last_event_id:
            SSE_RECONNECTIONS.labels(service="heartbeat").inc()
    except Exception:
        pass

    logger.info(
        f"SSE client connected: {client_id} "
        f"company_id={company_id} user={claims.get('sub')} "
        f"last_event_id={last_event_id}"
    )

    async def event_generator():
        """Generate SSE events for this client."""
        queue = await event_bus.subscribe(client_id, claims)

        try:
            # Send initial connection event (Section 2.5)
            yield (
                f"event: connected\n"
                f'data: {{"status": "connected", "source": "heartbeat"}}\n'
                f"retry: {RETRY_MS}\n\n"
            )

            # If reconnecting with Last-Event-ID, replay from ledger
            if last_event_id:
                try:
                    after_seq = int(last_event_id)
                    ledger = get_event_ledger()
                    replay = ledger.query_after(
                        company_id, after_seq, limit=1000,
                        data_uuid=data_uuid, pattern=pattern,
                    )
                    for ev in replay["events"]:
                        frame = _format_replay_event(ev)
                        yield frame
                    logger.info(
                        f"Replayed {len(replay['events'])} events "
                        f"for {client_id} from seq {after_seq}"
                    )
                except (ValueError, Exception) as e:
                    logger.warning(f"Last-Event-ID replay failed: {e}")

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_INTERVAL
                    )

                    # None sentinel = server-initiated disconnect (zombie eviction)
                    if event is None:
                        logger.info(f"SSE client {client_id} evicted (zombie)")
                        break

                    # Apply pattern filter if specified
                    if pattern and not fnmatch.fnmatch(event.event_type, pattern):
                        continue

                    yield event.format_sse()

                except asyncio.TimeoutError:
                    # JWT expiry check on keepalive (Section 11.3)
                    try:
                        jwt_mgr.verify_token(token)
                    except Exception:
                        yield 'event: error\ndata: {"error": "TOKEN_EXPIRED"}\n\n'
                        logger.info(f"SSE client {client_id} token expired")
                        break

                    # Zombie check on keepalive: if the client's queue
                    # has accumulated events they aren't consuming,
                    # evict them even during quiet periods where
                    # publish() isn't called. Threshold: 90% full.
                    depth = event_bus.get_queue_depth(client_id)
                    if depth >= 90:
                        logger.warning(
                            f"Evicting stale consumer {client_id}: "
                            f"queue depth {depth}/100 during keepalive"
                        )
                        try:
                            from ..observability.metrics import SSE_EVENTS_DROPPED
                            SSE_EVENTS_DROPPED.labels(
                                service="heartbeat", reason="stale_keepalive"
                            ).inc()
                        except Exception:
                            pass
                        break

                    # Send keepalive
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(client_id)
            try:
                SSE_CONNECTIONS_ACTIVE.labels(service="heartbeat").dec()
            except Exception:
                pass
            logger.info(f"SSE client disconnected: {client_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_replay_event(ev: Dict[str, Any]) -> str:
    """Format a ledger event dict as an SSE frame."""
    payload = json.dumps({
        "sequence": ev["sequence"],
        "event_type": ev["event_type"],
        "data": ev["data"],
        "timestamp": ev["timestamp"],
        "source": ev.get("source", "heartbeat"),
    })
    return f"id: {ev['sequence']}\nevent: {ev['event_type']}\ndata: {payload}\n\n"


# ── GET /api/sse/catchup (Section 5) ──────────────────────────────────

@router.get("/catchup")
async def sse_catchup(
    after_sequence: int = Query(..., description="Return events with sequence > this value"),
    limit: int = Query(500, ge=1, le=1000, description="Max events per page"),
    data_uuid: Optional[str] = Query(None, description="Filter to specific batch/entity group"),
    pattern: Optional[str] = Query(None, max_length=100, description="fnmatch filter on event_type"),
    token: str = Depends(get_current_user_token),
):
    """
    Paginated event replay from the Event Ledger (SSE Spec Section 5).

    Returns events with sequence > after_sequence, ordered ascending.
    Use has_more + next_sequence to paginate through large gaps.

    Multiple Float instances can call this concurrently during
    reconnect — the ledger is read-only, no contention.
    """
    jwt_mgr = get_jwt_manager()

    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        return JSONResponse(
            {"error": "TOKEN_INVALID"},
            status_code=401,
        )

    company_id = _get_company_id(claims)
    ledger = get_event_ledger()

    result = ledger.query_after(
        company_id=company_id,
        after_sequence=after_sequence,
        limit=limit,
        data_uuid=data_uuid,
        pattern=pattern,
    )

    try:
        from ..observability.metrics import SSE_CATCHUP_REQUESTS
        SSE_CATCHUP_REQUESTS.labels(service="heartbeat").inc()
    except Exception:
        pass

    logger.info(
        f"Catchup: company_id={company_id} after_seq={after_sequence} "
        f"returned={len(result['events'])} has_more={result['has_more']}"
    )

    return result


# ── GET /api/sse/watermark (Section 6) ────────────────────────────────

@router.get("/watermark")
async def sse_watermark(
    token: str = Depends(get_current_user_token),
):
    """
    Reconciliation watermark (SSE Spec Section 6).

    Returns latest_sequence, entity_counts, and ledger_oldest so
    Float SDK can detect drift without replaying events.

    Response is cached per company_id with 30s TTL (Section 6.4).
    Cache is invalidated on every new event publish.

    Multiple Float instances hitting this endpoint within a 30s window
    get the same cached snapshot — intentional per spec.
    """
    jwt_mgr = get_jwt_manager()

    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        return JSONResponse(
            {"error": "TOKEN_INVALID"},
            status_code=401,
        )

    company_id = _get_company_id(claims)
    now = time.time()

    # Check cache
    cached = _watermark_cache.get(company_id)
    if cached:
        cache_ts, cache_resp = cached
        if now - cache_ts < WATERMARK_TTL:
            result = cache_resp.copy()
            result["cached"] = True
            return result

    # Compute fresh watermark
    ledger = get_event_ledger()
    watermark = ledger.get_watermark(company_id)
    entity_counts = ledger.get_entity_counts(company_id)

    response = {
        "latest_sequence": watermark["latest_sequence"],
        "entity_counts": entity_counts,
        "ledger_oldest": watermark["ledger_oldest"],
        "timestamp": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z",
        "cached": False,
    }

    # Store in cache
    _watermark_cache[company_id] = (now, response.copy())

    return response
