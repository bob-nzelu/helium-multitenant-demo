"""
GET /metrics — Prometheus metrics endpoint (Decision 5A).

Phase 1 stub: returns basic service info metrics in Prometheus exposition format.
Phase 2 will add prometheus_client counters/histograms for real request tracking.

No authentication required (standard for /metrics endpoints).
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
)
async def metrics(request: Request):
    """
    Export Prometheus-format metrics.

    Phase 1 stub — returns service info and health gauges.
    """
    config = request.app.state.config
    module_cache = request.app.state.module_cache
    redis = request.app.state.redis

    module_cache_up = 1 if module_cache.is_loaded else 0
    redis_up = 1 if redis.is_available else 0

    lines = [
        "# HELP helium_relay_info Relay service information",
        "# TYPE helium_relay_info gauge",
        f'helium_relay_info{{instance_id="{config.instance_id}",version="{request.app.version}"}} 1',
        "",
        "# HELP helium_relay_up Relay service health (1=up, 0=down)",
        "# TYPE helium_relay_up gauge",
        "helium_relay_up 1",
        "",
        "# HELP helium_relay_module_cache_loaded Module cache status (1=loaded, 0=not loaded)",
        "# TYPE helium_relay_module_cache_loaded gauge",
        f"helium_relay_module_cache_loaded {module_cache_up}",
        "",
        "# HELP helium_relay_redis_connected Redis connection status (1=connected, 0=disconnected)",
        "# TYPE helium_relay_redis_connected gauge",
        f"helium_relay_redis_connected {redis_up}",
        "",
    ]

    return PlainTextResponse(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
