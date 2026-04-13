"""
GET /health — Public health check endpoint.

Returns service health status. No authentication required.
Always returns 200 OK (healthy or degraded, never error).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health(request: Request):
    """
    Check Relay service health and downstream dependencies.

    Returns status="healthy" when all services are reachable,
    or status="degraded" with a message indicating which services are down.
    """
    config = request.app.state.config
    heartbeat = request.app.state.heartbeat
    module_cache = request.app.state.module_cache
    redis = request.app.state.redis

    # Check downstream services
    services = {}
    degraded_reasons = []

    # HeartBeat
    try:
        hb_healthy = await heartbeat.health_check()
        services["heartbeat"] = "healthy" if hb_healthy else "unavailable"
        if not hb_healthy:
            degraded_reasons.append("HeartBeat unavailable")
    except Exception:
        services["heartbeat"] = "unavailable"
        degraded_reasons.append("HeartBeat unavailable")

    # Module cache
    if module_cache.is_loaded:
        services["module_cache"] = "loaded"
    else:
        services["module_cache"] = "not_loaded"
        degraded_reasons.append("Module cache not loaded")

    # Redis
    services["redis"] = "connected" if redis.is_available else "disconnected"
    # Redis disconnected is not degraded — graceful degradation by design

    overall = "degraded" if degraded_reasons else "healthy"
    message = "; ".join(degraded_reasons) if degraded_reasons else None

    return HealthResponse(
        status=overall,
        instance_id=config.instance_id,
        relay_type="bulk",
        version=request.app.version,
        services=services,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        message=message,
    )
