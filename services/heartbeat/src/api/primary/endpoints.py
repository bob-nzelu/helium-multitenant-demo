"""
Primary API — Satellite Management (Q6)

Endpoints exposed by HeartBeat in Primary mode to manage Satellite instances.
Satellites self-register, send heartbeats, and can be revoked by admin.

Endpoints:
    POST /primary/satellites/register       — Satellite self-registration
    GET  /primary/satellites                — List all satellites
    GET  /primary/satellites/{satellite_id} — Get specific satellite
    POST /primary/satellites/{satellite_id}/heartbeat — Receive heartbeat
    POST /primary/satellites/{satellite_id}/revoke    — Revoke a satellite
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/primary/satellites", tags=["Primary — Satellite Management"])


# ── Request/Response Models ────────────────────────────────────────────

class SatelliteRegisterRequest(BaseModel):
    satellite_id: str
    display_name: str
    base_url: str
    region: Optional[str] = None
    version: str = "2.0.0"


class SatelliteHeartbeatRequest(BaseModel):
    status: str = "ok"


# ── Helper: Get registry DB with satellite support ─────────────────────

def _get_registry():
    """Get registry database, raising 503 if unavailable."""
    from ...database.registry import get_registry_database
    try:
        return get_registry_database()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Registry database not available")


def _require_primary():
    """Raise 403 if not in Primary mode."""
    config = get_config()
    if not config.is_primary:
        raise HTTPException(
            status_code=403,
            detail="This endpoint is only available in Primary mode",
        )


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/register")
async def register_satellite(request: SatelliteRegisterRequest):
    """
    Satellite self-registration.

    Creates or updates a satellite registration. Satellites call this
    on startup to announce themselves to Primary.
    """
    _require_primary()
    reg_db = _get_registry()
    now = datetime.now(timezone.utc).isoformat()

    with reg_db.get_connection() as conn:
        conn.execute(
            """INSERT INTO satellite_registrations
               (satellite_id, display_name, base_url, status,
                last_heartbeat_at, region, version, registered_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
               ON CONFLICT(satellite_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   base_url = excluded.base_url,
                   status = 'active',
                   last_heartbeat_at = excluded.last_heartbeat_at,
                   region = excluded.region,
                   version = excluded.version,
                   updated_at = excluded.updated_at""",
            (
                request.satellite_id, request.display_name, request.base_url,
                now, request.region, request.version, now, now,
            ),
        )
        conn.commit()

    logger.info(f"Satellite registered: {request.satellite_id} at {request.base_url}")

    return {
        "status": "registered",
        "satellite_id": request.satellite_id,
        "primary_url": f"http://{get_config().host}:{get_config().port}",
    }


@router.get("")
async def list_satellites(active_only: bool = True):
    """List all registered satellites."""
    _require_primary()
    reg_db = _get_registry()

    if active_only:
        rows = reg_db.execute_query(
            "SELECT * FROM satellite_registrations WHERE status = 'active' ORDER BY satellite_id"
        )
    else:
        rows = reg_db.execute_query(
            "SELECT * FROM satellite_registrations ORDER BY satellite_id"
        )

    return {"satellites": rows, "count": len(rows)}


@router.get("/{satellite_id}")
async def get_satellite(satellite_id: str):
    """Get a specific satellite's registration details."""
    _require_primary()
    reg_db = _get_registry()

    rows = reg_db.execute_query(
        "SELECT * FROM satellite_registrations WHERE satellite_id = ?",
        (satellite_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Satellite '{satellite_id}' not found")

    return rows[0]


@router.post("/{satellite_id}/heartbeat")
async def receive_heartbeat(satellite_id: str, request: SatelliteHeartbeatRequest):
    """
    Receive heartbeat from a Satellite.

    Updates last_heartbeat_at and status. If satellite was 'unreachable',
    transitions back to 'active'.
    """
    _require_primary()
    reg_db = _get_registry()
    now = datetime.now(timezone.utc).isoformat()

    rowcount = reg_db.execute_update(
        """UPDATE satellite_registrations
           SET last_heartbeat_at = ?,
               last_heartbeat_status = ?,
               status = CASE WHEN status = 'revoked' THEN 'revoked' ELSE 'active' END,
               updated_at = ?
           WHERE satellite_id = ?""",
        (now, request.status, now, satellite_id),
    )

    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Satellite '{satellite_id}' not found")

    return {"status": "acknowledged", "satellite_id": satellite_id, "timestamp": now}


@router.post("/{satellite_id}/revoke")
async def revoke_satellite(satellite_id: str):
    """
    Revoke a Satellite registration.

    Revoked satellites are rejected on future heartbeats and blob forwards.
    """
    _require_primary()
    reg_db = _get_registry()
    now = datetime.now(timezone.utc).isoformat()

    rowcount = reg_db.execute_update(
        "UPDATE satellite_registrations SET status = 'revoked', updated_at = ? WHERE satellite_id = ?",
        (now, satellite_id),
    )

    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Satellite '{satellite_id}' not found")

    logger.warning(f"Satellite revoked: {satellite_id}")

    return {"status": "revoked", "satellite_id": satellite_id}
