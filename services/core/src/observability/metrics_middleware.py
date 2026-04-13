"""
WS6: Prometheus HTTP Middleware

Records request count and duration for every HTTP request.
Path normalization prevents label cardinality explosion from UUIDs.
"""

from __future__ import annotations

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.observability.metrics import http_request_duration_seconds, http_requests_total

# Matches UUIDv4/v7 segments and pure numeric segments in paths
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NUMERIC_PATTERN = re.compile(r"^\d+$")

# Paths to skip instrumenting
_SKIP_PATHS = {"/metrics", "/health", "/ready"}


def normalize_path(path: str) -> str:
    """
    Replace UUID-like and purely numeric path segments with {id}.

    /api/v1/invoices/550e8400-e29b-41d4-a716-446655440000 → /api/v1/invoices/{id}
    /api/v1/invoices/12345 → /api/v1/invoices/{id}
    """
    if path == "/":
        return "/"
    parts = path.rstrip("/").split("/")
    normalized = []
    for part in parts:
        if _UUID_PATTERN.fullmatch(part):
            normalized.append("{id}")
        elif _NUMERIC_PATTERN.fullmatch(part) and part != "":
            normalized.append("{id}")
        else:
            normalized.append(part)
    return "/".join(normalized)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record HTTP request count and duration for Prometheus."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip self-instrumentation and health checks
        if path in _SKIP_PATHS:
            return await call_next(request)

        endpoint = normalize_path(path)
        method = request.method

        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code=str(response.status_code),
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        return response
