"""
Lifecycle API — Service start/stop/restart and status.

Endpoints:
    GET  /api/lifecycle/services              — List all managed services + status
    GET  /api/lifecycle/services/{name}       — Single service details
    POST /api/lifecycle/services/{name}/start — Start a stopped service
    POST /api/lifecycle/services/{name}/stop  — Stop a service
    POST /api/lifecycle/services/{name}/restart — Restart a service
    GET  /api/lifecycle/startup-order         — View startup priority order

All endpoints require service credentials (admin-level).
Start/stop/restart delegate to KeepAliveManager methods.

Reference: HEARTBEAT_LIFECYCLE_SPEC.md Section 9.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from ...auth.dependencies import verify_service_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lifecycle", tags=["lifecycle"])


@router.get(
    "/services",
    summary="List all managed services and current status",
)
async def list_services(
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    List all managed services with their current runtime status.

    Returns PID, status, restart count, and health endpoint for each service.
    """
    try:
        from ...keepalive.manager import get_keepalive_manager
        manager = get_keepalive_manager()
        result = await manager.get_status()
        return result
    except Exception as e:
        logger.error(f"list_services failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get(
    "/services/{service_name}",
    summary="Get single service details",
)
async def get_service(
    service_name: str,
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Get detailed status of a single managed service.

    Returns PID, status, restart count, startup/stop timestamps,
    and health endpoint.
    """
    try:
        from ...keepalive.manager import get_keepalive_manager
        manager = get_keepalive_manager()
        status_info = await manager.get_status()
        service = status_info.get("services", {}).get(service_name)

        if service is None:
            # Check if it exists in DB but isn't tracked by manager
            from ...database.registry import get_registry_database
            db = get_registry_database()
            svc = db.get_managed_service(service_name)
            if svc is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "status": "not_found",
                        "message": f"No managed service: {service_name}",
                    },
                )
            return {
                "service_name": service_name,
                "status": svc.get("current_status", "stopped"),
                "pid": svc.get("current_pid"),
                "restart_count": svc.get("restart_count", 0),
                "startup_priority": svc.get("startup_priority"),
                "auto_start": bool(svc.get("auto_start", True)),
                "auto_restart": bool(svc.get("auto_restart", True)),
                "restart_policy": svc.get("restart_policy"),
                "health_endpoint": svc.get("health_endpoint"),
                "source": "database",
            }

        return {
            "service_name": service_name,
            **service,
            "source": "manager",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/services/{service_name}/start",
    summary="Start a stopped service",
)
async def start_service(
    service_name: str,
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Start a managed service.

    Requires Owner-level service credentials (step-up auth recommended
    for production — enforced at the auth layer, not here).
    """
    try:
        from ...keepalive.manager import get_keepalive_manager
        manager = get_keepalive_manager()
        result = await manager.start_service(service_name)

        if result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result,
            )

        logger.info(f"Service {service_name} start requested via API")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"start_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/services/{service_name}/stop",
    summary="Gracefully stop a service",
)
async def stop_service(
    service_name: str,
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Gracefully stop a managed service.

    Uses the graceful drain sequence: SIGTERM → wait → SIGKILL.
    """
    try:
        from ...keepalive.manager import get_keepalive_manager
        manager = get_keepalive_manager()
        result = await manager.stop_service(service_name)

        if result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result,
            )

        logger.info(f"Service {service_name} stop requested via API")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"stop_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/services/{service_name}/restart",
    summary="Gracefully restart a service",
)
async def restart_service(
    service_name: str,
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Gracefully restart a managed service (stop → start).
    """
    try:
        from ...keepalive.manager import get_keepalive_manager
        manager = get_keepalive_manager()
        result = await manager.restart_service(service_name)

        if result.get("status") == "error":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result,
            )

        logger.info(f"Service {service_name} restart requested via API")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"restart_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get(
    "/startup-order",
    summary="View defined startup priority order",
)
async def get_startup_order(
    _credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    View the defined startup order for all managed services.

    Returns services ordered by startup_priority (ascending).
    """
    try:
        from ...database.registry import get_registry_database
        db = get_registry_database()
        order = db.get_startup_order()
        return {
            "startup_order": order,
            "total": len(order),
        }
    except Exception as e:
        logger.error(f"get_startup_order failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )
