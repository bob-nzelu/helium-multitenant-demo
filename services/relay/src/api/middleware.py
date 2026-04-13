"""
API Middleware

TraceIDMiddleware: Adds X-Trace-ID to every request/response.
BodyCacheMiddleware: Pre-reads and caches raw body so both HMAC auth and
                     form parsing can access it without "Stream consumed" errors.
relay_error_handler: Catches RelayError and returns structured JSON.
"""

import asyncio
import logging
from typing import Any, Callable

from uuid6 import uuid7

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..errors import RelayError

logger = logging.getLogger(__name__)


class BodyCacheMiddleware:
    """
    Pure ASGI middleware that pre-reads the request body and replaces
    the receive callable so that both HMAC authentication and
    FastAPI's form/file parsers can read the same body.

    Must be added BEFORE any middleware that calls call_next() (like
    TraceIDMiddleware), because BaseHTTPMiddleware wraps receive.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Read the entire body from the original receive
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        # Store raw body in scope state so dependencies can access it
        # without calling request.body() (which may conflict with stream).
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["raw_body"] = body

        # Replace receive with a callable that replays the cached body.
        # After body is replayed, block forever instead of returning disconnect —
        # returning disconnect immediately kills StreamingResponse (SSE streams).
        body_sent = False
        disconnect_event = asyncio.Event()

        async def cached_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Block until the client actually disconnects (or server shuts down)
            await disconnect_event.wait()
            return {"type": "http.disconnect"}

        await self.app(scope, cached_receive, send)


class TraceIDMiddleware:
    """
    Pure ASGI middleware that injects X-Trace-ID into every request.

    If the client sends X-Trace-ID, use it. Otherwise, generate a new UUID.
    Stores trace_id in scope["state"] for downstream access via request.state.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Get or generate trace ID
        headers = dict(scope.get("headers", []))
        trace_id = headers.get(b"x-trace-id", b"").decode("utf-8")
        if not trace_id:
            trace_id = str(uuid7())

        # Store in scope state for request.state.trace_id access
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["trace_id"] = trace_id

        # Wrap send to inject response header
        async def send_with_trace(message: Message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode("utf-8")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_trace)


async def relay_error_handler(request: Request, exc: RelayError) -> JSONResponse:
    """
    Global exception handler for RelayError hierarchy.

    Converts any RelayError into a structured JSON response with
    the appropriate HTTP status code.
    """
    logger.warning(
        f"[{getattr(request.state, 'trace_id', 'unknown')}] "
        f"{exc.error_code}: {exc.message}",
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )
