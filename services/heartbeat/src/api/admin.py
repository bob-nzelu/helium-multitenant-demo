"""
Admin API Router — Update Engine (Foundation Stubs)

Placeholder endpoints for the update engine.
Full implementation is a separate session.

Endpoints:
    POST /api/admin/updates/apply          -- 501 Not Implemented
    GET  /api/admin/updates/status         -- 501 Not Implemented
    GET  /api/admin/updates/history        -- 200 (empty list)
    POST /api/admin/updates/rollback       -- 501 Not Implemented
"""

import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/updates/apply")
async def apply_update():
    """Upload + apply an update package (not yet implemented)."""
    raise HTTPException(
        status_code=501,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": "Update engine not yet implemented",
        },
    )


@router.get("/updates/status")
async def update_status():
    """Get current update progress (not yet implemented)."""
    raise HTTPException(
        status_code=501,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": "Update engine not yet implemented",
        },
    )


@router.get("/updates/history")
async def update_history():
    """Get past updates."""
    return {"updates": []}


@router.post("/updates/rollback")
async def rollback_update():
    """Rollback to previous version (not yet implemented)."""
    raise HTTPException(
        status_code=501,
        detail={
            "error_code": "NOT_IMPLEMENTED",
            "message": "Update engine not yet implemented",
        },
    )
