"""
Request Logging Middleware (Pure ASGI)

Logs request start and response completion with structlog.
Per API_CONTRACTS: Skip logging for /api/v1/health and /api/v1/metrics.
"""

from __future__ import annotations

import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()

# Paths to skip logging (noise reduction)
SKIP_PATHS = {"/api/v1/health", "/api/v1/metrics"}


class RequestLoggingMiddleware:
    """Pure ASGI middleware for structured request/response logging."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        start = time.monotonic()

        # Capture response status
        status_code = 0

        async def send_with_log(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_with_log)
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            logger.info(
                "request_completed",
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )
