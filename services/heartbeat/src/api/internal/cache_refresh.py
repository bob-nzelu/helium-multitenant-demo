"""
Cache Refresh API (P2-F)

Internal endpoint to push cache invalidation signals to connected services.
When config or reference data changes, HeartBeat notifies registered Relay
instances to refresh their cached data.

Endpoints:
    POST /internal/refresh-cache  — Broadcast refresh signal
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...config import get_config
from ...database import get_blob_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal — Cache Management"])


class RefreshCacheRequest(BaseModel):
    """Request body for cache refresh broadcast."""
    cache_type: str = Field(
        ...,
        description="Type of cache to refresh: config, dedup, limits, all",
    )
    reason: Optional[str] = Field(None, description="Why the refresh was triggered")
    target_services: Optional[List[str]] = Field(
        None,
        description="Specific service instance IDs to notify (None = broadcast to all)",
    )


@router.post("/refresh-cache")
async def refresh_cache(req: RefreshCacheRequest):
    """
    Broadcast cache refresh signal to registered services.

    In the current implementation, this logs the refresh event and records
    it to audit_events. Services poll or subscribe via SSE for changes.
    Future: push notifications via HTTP callbacks or SSE events.
    """
    config = get_config()
    if not config.is_primary:
        raise HTTPException(
            status_code=403,
            detail="Cache refresh is only available in Primary mode",
        )

    valid_types = {"config", "dedup", "limits", "all"}
    if req.cache_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cache_type '{req.cache_type}'. Must be one of: {', '.join(sorted(valid_types))}",
        )

    db = get_blob_database()
    now_iso = datetime.now(timezone.utc).isoformat()
    now_unix = int(time.time())

    # Log the refresh event to audit_events
    import json
    details = json.dumps({
        "cache_type": req.cache_type,
        "reason": req.reason,
        "target_services": req.target_services,
    })

    db.execute_insert(
        """INSERT INTO audit_events
           (service, event_type, user_id, details, trace_id, created_at, created_at_unix)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            "heartbeat",
            "cache.refresh_requested",
            None,
            details,
            None,
            now_iso,
            now_unix,
        ),
    )

    # Publish SSE event for subscribers (P2-D backwards-compat)
    cache_event_data = {
        "cache_type": req.cache_type,
        "reason": req.reason,
        "target_services": req.target_services,
    }
    try:
        from ...events import get_event_bus
        bus = get_event_bus()
        await bus.publish("cache.refresh", cache_event_data)
    except Exception as e:
        logger.warning(f"P2-D SSE publish failed (non-fatal): {e}")

    # SSE Spec: publish to authenticated stream + event ledger
    try:
        from ...sse.publish import publish_event
        await publish_event("cache.refresh", cache_event_data)
    except Exception as e:
        logger.warning(f"SSE ledger publish failed (non-fatal): {e}")

    # Count targeted services
    targeted = "all"
    if req.target_services:
        targeted = ", ".join(req.target_services)

    logger.info(f"Cache refresh requested: type={req.cache_type}, targets={targeted}")

    return {
        "status": "refresh_broadcast",
        "cache_type": req.cache_type,
        "targeted_services": req.target_services or "all",
        "timestamp": now_iso,
    }
