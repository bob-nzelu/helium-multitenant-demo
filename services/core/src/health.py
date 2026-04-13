"""
Health & Metrics Endpoints

GET /api/v1/health — Service health check
GET /api/v1/metrics — Prometheus metrics exposition
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from src import __version__
from src.database.pool import check_pool
from src.models import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["Health"])

# ── Prometheus Metrics Registry ──────────────────────────────────────────
# WS0 creates all metric objects. Other workstreams import and increment.

files_processed_total = Counter(
    "helium_core_files_processed_total",
    "Total files processed",
    ["status"],
)

processing_duration_seconds = Histogram(
    "helium_core_processing_duration_seconds",
    "Processing duration in seconds",
)

queue_depth = Gauge(
    "helium_core_queue_depth",
    "Current queue depth by status",
    ["status"],
)

workers_active = Gauge(
    "helium_core_workers_active",
    "Number of active workers",
)

invoices_total = Counter(
    "helium_core_invoices_total",
    "Total invoices created",
)

errors_total = Counter(
    "helium_core_errors_total",
    "Total errors by error code",
    ["error_code"],
)

# sse_connections_active is defined in src/sse/manager.py


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> Response:
    """
    Service health check.

    Status logic (per API_CONTRACTS):
    - healthy: database connected AND scheduler running
    - degraded: one of database/scheduler down
    - unhealthy: both down
    """
    # Check database
    pool = getattr(request.app.state, "pool", None)
    db_ok = False
    if pool is not None:
        db_ok = await check_pool(pool)

    # Check scheduler
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_ok = scheduler is not None

    # Determine status
    if db_ok and scheduler_ok:
        status = "healthy"
    elif db_ok or scheduler_ok:
        status = "degraded"
    else:
        status = "unhealthy"

    # Calculate uptime
    start_time = getattr(request.app.state, "start_time", time.monotonic())
    uptime = time.monotonic() - start_time

    body = HealthResponse(
        status=status,
        version=__version__,
        uptime_seconds=round(uptime, 2),
        database="connected" if db_ok else "disconnected",
        scheduler="running" if scheduler_ok else "stopped",
    )

    status_code = 200 if status != "unhealthy" else 503
    return Response(
        content=body.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
    )


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics in text exposition format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
