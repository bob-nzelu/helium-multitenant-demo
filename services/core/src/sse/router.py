"""
SSE Endpoints — Stream, Catchup, Watermark

Per SSE_SPEC v1.1:
- GET /api/sse/stream — live event stream (Section 2)
- GET /api/sse/catchup — paginated replay from ledger (Section 5)
- GET /api/sse/watermark — reconciliation snapshot (Section 6)

All endpoints require JWT Bearer authentication (Section 2.2).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.auth.jwt_validator import JWTClaims, JWTError, extract_bearer_token, validate_jwt
from src.sse.models import SSEEvent

logger = structlog.get_logger()

router = APIRouter(tags=["SSE"])

# SSE retry interval (Section 2.5: 5000ms)
SSE_RETRY_MS = 5000

# Max length for fnmatch pattern param (prevents expensive wildcard queries)
MAX_PATTERN_LENGTH = 100


def _authenticate(request: Request) -> JWTClaims:
    """
    Validate JWT from Authorization header.

    Raises JWTError on invalid/expired/missing token.
    """
    config = request.app.state.config
    authorization = request.headers.get("authorization")
    token = extract_bearer_token(authorization)
    return validate_jwt(token, config.jwt_public_key, config.jwt_algorithm)


def _sse_error_response(error_code: str, status: int = 401) -> EventSourceResponse:
    """
    Return an SSE error frame then close (Section 2.2).

    Per spec: HTTP 401 with Content-Type: text/event-stream,
    send error event, then close.
    """
    async def error_gen() -> AsyncGenerator[dict, None]:
        yield {
            "event": "error",
            "data": json.dumps({"error": error_code}),
        }

    return EventSourceResponse(
        error_gen(),
        status_code=status,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/sse/stream")
async def sse_stream(
    request: Request,
    data_uuid: str | None = Query(default=None, description="Filter events by data_uuid"),
    pattern: str | None = Query(default=None, description="fnmatch pattern filter (e.g. invoice.*)"),
) -> EventSourceResponse:
    """
    Server-Sent Events stream (Section 2).

    Requires JWT Bearer token. Events are filtered by company_id from JWT.
    Supports data_uuid and pattern query params for further filtering.
    Checks JWT expiry on every write including keepalives (Section 11.3).
    """
    # Authenticate (Section 2.2)
    try:
        claims = _authenticate(request)
    except JWTError as e:
        return _sse_error_response(e.code)

    # Validate pattern length
    if pattern and len(pattern) > MAX_PATTERN_LENGTH:
        return _sse_error_response("TOKEN_INVALID", status=400)

    manager = request.app.state.sse_manager

    # Extract Last-Event-ID for replay (ignore malformed values)
    last_event_id: int | None = None
    last_event_id_raw = request.headers.get("last-event-id")
    if last_event_id_raw:
        try:
            last_event_id = int(last_event_id_raw)
        except ValueError:
            logger.warning("sse_invalid_last_event_id", value=last_event_id_raw[:50])

    # Subscribe with company_id scope (Section 11.1)
    client = manager.subscribe(
        company_id=claims.company_id,
        data_uuid_filter=data_uuid,
        pattern_filter=pattern,
        jwt_exp=claims.exp,
    )

    # Also set jwt_claims on request.state for backwards compat
    request.state.jwt_claims = claims.raw

    # Replay missed events if reconnecting
    if last_event_id is not None:
        await manager.replay(client.client_id, last_event_id)

    async def event_generator() -> AsyncGenerator[dict, None]:
        first_event = True
        try:
            while True:
                event: SSEEvent | None = await client.queue.get()

                # None sentinel = shutdown or eviction
                if event is None:
                    break

                # JWT expiry check on every write (Section 11.3)
                if client.jwt_exp and time.time() > client.jwt_exp:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": "TOKEN_EXPIRED"}),
                    }
                    logger.info(
                        "sse_token_expired",
                        client_id=client.client_id,
                        company_id=claims.company_id,
                    )
                    break

                # Heartbeat -> SSE comment (Section 1.6)
                if event.event_type == "__heartbeat__":
                    yield {"comment": "keepalive"}
                    continue

                # Build spec-compliant envelope (Section 1.2)
                envelope = {
                    "sequence": event.id,
                    "event_type": event.event_type,
                    "data": event.data,
                    "timestamp": event.timestamp,
                    "source": event.source,
                }

                frame: dict = {
                    "event": event.event_type,
                    "data": json.dumps(envelope),
                    "id": str(event.id) if event.id else None,
                }

                # Send retry on first event (Section 1.1)
                if first_event:
                    frame["retry"] = SSE_RETRY_MS
                    first_event = False

                yield frame

        except asyncio.CancelledError:
            pass
        finally:
            manager.unsubscribe(client.client_id)

    # Send connected event first (Section 2.5)
    async def stream_with_connected() -> AsyncGenerator[dict, None]:
        yield {
            "event": "connected",
            "data": json.dumps({"status": "connected", "source": "core"}),
            "retry": SSE_RETRY_MS,
        }
        async for frame in event_generator():
            yield frame

    return EventSourceResponse(
        stream_with_connected(),
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/sse/catchup")
async def sse_catchup(
    request: Request,
    after_sequence: int = Query(..., ge=0, description="Return events with sequence > this value"),
    limit: int = Query(default=500, ge=1, le=1000, description="Max events per page"),
    data_uuid: str | None = Query(default=None, description="Filter by data_uuid"),
    pattern: str | None = Query(default=None, max_length=MAX_PATTERN_LENGTH, description="fnmatch filter on event_type"),
) -> JSONResponse:
    """
    Catchup endpoint for paginated event replay from ledger (Section 5).

    Returns events after a given sequence, scoped to the caller's company_id.
    """
    try:
        claims = _authenticate(request)
    except JWTError as e:
        return JSONResponse(
            status_code=401,
            content={"error": e.code, "message": e.message},
        )

    from src.sse.manager import sse_catchup_requests_total
    sse_catchup_requests_total.labels(service="core").inc()

    pool = request.app.state.pool
    ledger = request.app.state.event_ledger

    events, has_more, oldest_available = await ledger.query(
        pool=pool,
        company_id=claims.company_id,
        after_sequence=after_sequence,
        limit=limit,
        data_uuid=data_uuid,
        pattern=pattern,
    )

    next_sequence = events[-1]["sequence"] if events else after_sequence

    logger.info(
        "sse_catchup_request",
        company_id=claims.company_id,
        after_sequence=after_sequence,
        events_returned=len(events),
    )

    return JSONResponse(content={
        "events": events,
        "has_more": has_more,
        "next_sequence": next_sequence,
        "oldest_available": oldest_available,
    })


@router.get("/api/sse/watermark")
async def sse_watermark(request: Request) -> JSONResponse:
    """
    Watermark endpoint for reconciliation (Section 6).

    Returns latest sequence, entity counts, and ledger bounds.
    Cached per company_id with 30s TTL (Section 6.4).
    """
    try:
        claims = _authenticate(request)
    except JWTError as e:
        return JSONResponse(
            status_code=401,
            content={"error": e.code, "message": e.message},
        )

    pool = request.app.state.pool
    ledger = request.app.state.event_ledger

    result = await ledger.watermark(pool=pool, company_id=claims.company_id)

    return JSONResponse(content=result)
