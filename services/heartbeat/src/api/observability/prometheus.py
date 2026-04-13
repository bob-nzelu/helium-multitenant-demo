"""
Prometheus Metrics Endpoint (P2-A)

Exposes GET /metrics in Prometheus text exposition format.
Also provides FastAPI middleware for automatic request instrumentation.

The /metrics endpoint is UNAUTHENTICATED — Prometheus needs to scrape it
without API keys.

Endpoints:
    GET /metrics    — Prometheus scrape endpoint (text/plain)
"""

import logging
import time

from fastapi import APIRouter, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from ...observability.metrics import (
    REGISTRY,
    REQUEST_COUNT,
    REQUEST_DURATION,
    REQUESTS_IN_PROGRESS,
)


logger = logging.getLogger(__name__)


# ── Router ────────────────────────────────────────────────────────────────

router = APIRouter(tags=["Observability"])


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """
    Prometheus scrape endpoint.

    Returns metrics in Prometheus text exposition format.
    Unauthenticated — Prometheus needs direct access.
    """
    metrics_output = generate_latest(REGISTRY)
    return Response(
        content=metrics_output,
        media_type=CONTENT_TYPE_LATEST,
    )


# ── Middleware ─────────────────────────────────────────────────────────────

class PrometheusMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that instruments every request with Prometheus metrics.

    Tracks:
        - heartbeat_requests_total (counter by method, endpoint, status)
        - heartbeat_request_duration_seconds (histogram)
        - heartbeat_requests_in_progress (gauge)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip /metrics endpoint to avoid recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        # Normalize path — replace UUIDs/IDs with {id} to avoid high cardinality
        path = self._normalize_path(request.url.path)

        REQUESTS_IN_PROGRESS.inc()
        start_time = time.time()

        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.time() - start_time
            REQUESTS_IN_PROGRESS.dec()

            REQUEST_COUNT.labels(
                method=method, endpoint=path, status_code=status
            ).inc()

            REQUEST_DURATION.labels(
                method=method, endpoint=path, status_code=status
            ).observe(duration)

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Normalize URL path to reduce cardinality.

        Replaces UUID-like segments and numeric IDs with placeholders.
        e.g., /api/v1/heartbeat/blob/550e8400-.../status → /api/v1/heartbeat/blob/{uuid}/status
        """
        parts = path.split("/")
        normalized = []
        for part in parts:
            if not part:
                normalized.append(part)
                continue
            # UUID pattern (8-4-4-4-12 hex chars)
            if len(part) >= 32 and "-" in part:
                normalized.append("{uuid}")
            # Pure numeric
            elif part.isdigit():
                normalized.append("{id}")
            # Hex-like long strings (e.g., credential IDs)
            elif len(part) > 20 and all(c in "0123456789abcdef-" for c in part.lower()):
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/".join(normalized)
