"""
Webhook Endpoint — Helium Cross-Service Contract

Every Helium service (Core, Edge, Relay, HIS) exposes:
    POST /api/v1/webhook/config_changed

HeartBeat calls this single endpoint whenever config.db changes.
The payload tells the service WHAT changed. The service then
re-fetches the full config from HeartBeat.

Payload:
    {
        "changed": ["tier_settings", "eic_config", ...],
        "timestamp": "2026-03-30T12:00:00Z",
        "source": "config.db"
    }

This is a trigger, not a data transport. The actual config values
are fetched from HeartBeat's GET /api/v1/heartbeat/config.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])


class ConfigChangedRequest(BaseModel):
    """Payload from HeartBeat when config.db changes."""

    changed: list[str] = Field(
        ..., description="List of changed config categories"
    )
    timestamp: str | None = Field(
        default=None, description="ISO timestamp of the change"
    )
    source: str = Field(
        default="config.db", description="Source of the change"
    )


class ConfigChangedResponse(BaseModel):
    """Acknowledgement response."""

    status: str = "ok"
    refreshed: bool = False
    message: str = ""


@router.post("/config_changed", response_model=ConfigChangedResponse)
async def config_changed(request: Request, body: ConfigChangedRequest):
    """Handle config change notification from HeartBeat.

    Re-fetches the full tenant config from HeartBeat and updates
    the in-memory cache. Non-blocking — returns immediately.
    """
    config_cache = getattr(request.app.state, "config_cache", None)
    if not config_cache:
        logger.warning("config_changed_no_cache", changed=body.changed)
        return ConfigChangedResponse(
            status="ok",
            refreshed=False,
            message="Config cache not initialized",
        )

    success = await config_cache.refresh(changed=body.changed)

    # WS6: Audit config change
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if audit_logger:
        await audit_logger.log(
            event_type="config.changed",
            entity_type="system",
            action="UPDATE",
            metadata={
                "changed": body.changed,
                "source": body.source,
                "refreshed": success,
            },
        )

    logger.info(
        "config_changed_handled",
        changed=body.changed,
        refreshed=success,
    )

    return ConfigChangedResponse(
        status="ok",
        refreshed=success,
        message=f"Config refreshed ({', '.join(body.changed)})" if success
            else "Refresh failed — using previous config",
    )
