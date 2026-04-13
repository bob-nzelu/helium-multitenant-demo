"""
TraceID Middleware (Pure ASGI)

Extracts X-Trace-ID from request headers; if absent, generates one via uuid7().
Sets it on scope state and response headers. Binds to structlog contextvars.
"""

from __future__ import annotations

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uuid6 import uuid7

logger = structlog.get_logger()


class TraceIDMiddleware:
    """Pure ASGI middleware — NOT BaseHTTPMiddleware."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract or generate trace ID
        trace_id = _extract_trace_id(scope) or str(uuid7())

        # Store in scope state
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["trace_id"] = trace_id

        # Bind to structlog context for request-scoped logging
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        # Inject trace ID into response headers
        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_trace)


def _extract_trace_id(scope: Scope) -> str | None:
    """Extract X-Trace-ID from request headers."""
    headers = scope.get("headers", [])
    for name, value in headers:
        if name.lower() == b"x-trace-id":
            decoded = value.decode("latin-1").strip()
            if decoded:
                return decoded
    return None
