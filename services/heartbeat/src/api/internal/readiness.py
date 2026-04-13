"""
Readiness API — Aggregate readiness of all managed services.

Endpoint:
    GET /api/status/readiness

No auth required — localhost only, used by:
    - Float App (startup connection check)
    - Installer splash screen (startup progress)
    - System tray app (status monitoring)

Returns a comprehensive status of HeartBeat itself and all managed services,
including whether the platform is fully ready for user interaction.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from ...config import get_config
from ...database import get_blob_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get(
    "/readiness",
    summary="Platform readiness check",
    response_description="Aggregate status of HeartBeat and all managed services",
)
async def get_readiness():
    """
    Aggregate readiness of all managed services.

    No auth required — open endpoint on localhost.

    Used by Float/Installer splash screen to determine when the platform
    is ready. Float polls this until `ready=true` before showing the main UI.

    Returns:
        {
            "ready": bool,           # True when all auto_start services are healthy
            "services": {
                "heartbeat": {"status": "healthy", "since": "..."},
                "core": {"status": "healthy", "pid": 1234, ...},
                "relay": {"status": "starting", "pid": 5678, ...},
                ...
            },
            "tier": "standard",
            "total_services": int,
            "healthy_services": int,
            "timestamp": "..."
        }
    """
    config = get_config()
    now = datetime.now(timezone.utc).isoformat()

    # HeartBeat's own health
    heartbeat_status = "healthy"
    try:
        db = get_blob_database()
        db.execute_query("SELECT 1")
    except Exception:
        heartbeat_status = "degraded"

    services = {
        "heartbeat": {
            "status": heartbeat_status,
            "pid": None,  # HeartBeat is the current process
            "tier": config.tier,
        },
    }

    # Query Keep Alive Manager for managed service statuses
    total_managed = 0
    healthy_managed = 0

    try:
        from ...keepalive.manager import get_keepalive_manager
        keepalive = get_keepalive_manager()
        status_info = await keepalive.get_status()

        for name, svc_status in status_info.get("services", {}).items():
            services[name] = svc_status
            total_managed += 1
            if svc_status.get("status") == "healthy":
                healthy_managed += 1

    except Exception as e:
        logger.debug(f"Keep Alive manager not available: {e}")

    # Platform is ready when:
    # 1. HeartBeat itself is healthy
    # 2. ALL managed auto_start services are healthy
    # (If no managed services, ready=true as long as HeartBeat is healthy)
    if total_managed > 0:
        ready = heartbeat_status == "healthy" and healthy_managed == total_managed
    else:
        ready = heartbeat_status == "healthy"

    return {
        "ready": ready,
        "services": services,
        "tier": config.tier,
        "total_services": total_managed + 1,  # +1 for HeartBeat
        "healthy_services": healthy_managed + (1 if heartbeat_status == "healthy" else 0),
        "timestamp": now,
    }
