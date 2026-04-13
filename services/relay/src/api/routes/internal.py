"""
Internal endpoints — HeartBeat pushes config/cache updates.

POST /internal/refresh-cache         — Legacy: Transforma module refresh
POST /api/v1/webhook/config_changed  — Standard webhook: any config change

Both protected by service bearer token. Not exposed via tunnel.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..deps import verify_internal_token
from ..models import RefreshCacheResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Webhook Models ────────────────────────────────────────────────────────

class ConfigChangedPayload(BaseModel):
    """Standard webhook payload per WEBHOOK_CONFIG_CONTRACT.md"""
    changed: List[str]  # e.g. ["transforma_config", "tier_settings", "tenant_details"]
    timestamp: str
    source: str = "heartbeat"


class ConfigChangedResponse(BaseModel):
    status: str
    actions_taken: List[str]


# ── Standard Webhook Endpoint ─────────────────────────────────────────────

@router.post(
    "/api/v1/webhook/config_changed",
    response_model=ConfigChangedResponse,
    summary="Config change webhook (standard pattern)",
    dependencies=[Depends(verify_internal_token)],
)
async def webhook_config_changed(
    payload: ConfigChangedPayload,
    request: Request,
):
    """
    Standard webhook receiver per WEBHOOK_CONFIG_CONTRACT.md.

    HeartBeat calls this when config.db changes. The `changed` list
    tells Relay WHAT changed so it can selectively refresh.

    Changed categories Relay cares about:
        transforma_config  — IRN/QR modules or FIRS service keys updated
        tier_settings      — Upload limits, rate limits changed
        tenant_details     — Company info updated (informational)
        eic_config         — FIRS endpoint config changed

    Relay ignores: rbac_roles, notification_settings (not relevant)
    """
    trace_id = getattr(request.state, "trace_id", "")
    actions = []

    logger.info(
        f"[{trace_id}] Webhook config_changed: {payload.changed} "
        f"(source={payload.source})"
    )

    config_cache = request.app.state.config_cache
    module_cache = request.app.state.module_cache

    # Always refresh full config cache on any change
    config_refreshed = await config_cache.refresh(changed=payload.changed)
    if config_refreshed:
        actions.append("config_cache_refreshed")
    else:
        actions.append("config_cache_refresh_failed")

    # Selective: Transforma modules need separate refresh (code + keys)
    if "transforma_config" in payload.changed or "eic_config" in payload.changed:
        result = await module_cache.refresh()
        actions.append(
            f"transforma_refreshed (modules={result['modules_updated']}, "
            f"keys={result['keys_updated']})"
        )
        logger.info(f"[{trace_id}] Transforma cache refreshed: {result}")

    if "registry" in payload.changed:
        # Service URLs or API keys may have changed
        actions.append("registry_refreshed_via_config")
        logger.info(f"[{trace_id}] Registry changed — URLs/keys updated via config cache")

    return ConfigChangedResponse(
        status="ok",
        actions_taken=actions,
    )


# ── Legacy Endpoint (backward compat) ────────────────────────────────────

@router.post(
    "/internal/refresh-cache",
    response_model=RefreshCacheResponse,
    summary="Legacy: Trigger Transforma module cache refresh",
    dependencies=[Depends(verify_internal_token)],
)
async def refresh_cache(request: Request):
    """
    Legacy cache refresh endpoint. Kept for backward compatibility.

    New code should use POST /api/v1/webhook/config_changed with
    changed=["transforma_config"] instead.
    """
    trace_id = getattr(request.state, "trace_id", "")
    logger.info(f"[{trace_id}] POST /internal/refresh-cache (legacy)")

    module_cache = request.app.state.module_cache
    result = await module_cache.refresh()

    logger.info(
        f"[{trace_id}] Cache refreshed — "
        f"modules={result['modules_updated']}, "
        f"keys={result['keys_updated']}"
    )

    return RefreshCacheResponse(
        status="ok",
        modules_updated=result["modules_updated"],
        keys_updated=result["keys_updated"],
    )
