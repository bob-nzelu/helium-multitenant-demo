"""
Event Publish API — Allows Core/Edge to publish SSE events through HeartBeat.

Endpoint:
    POST /api/internal/events/publish — Push an event to SSE subscribers

This bridges the gap between Core (which processes invoices) and Float
(which receives events via HeartBeat SSE). Core calls this endpoint
after creating/updating invoices so Float sees them in real-time.

Auth: Internal service token (same as other /api/internal/ endpoints).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/events", tags=["Event Publishing"])


class PublishEventRequest(BaseModel):
    event_type: str = Field(..., description="Dotted event type (e.g., 'invoice.created', 'invoice.updated')")
    data: Dict[str, Any] = Field(..., description="Event payload")
    company_id: Optional[str] = Field(None, description="Tenant ID (defaults to HeartBeat config)")
    data_uuid: Optional[str] = Field(None, description="Optional batch/entity group filter key")


@router.post("/publish")
async def publish_event_endpoint(
    body: PublishEventRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Publish an SSE event through HeartBeat's event pipeline.

    Called by Core after processing invoices, by Edge after transmission,
    or by any internal service that needs to notify Float/SDK.

    The event is:
    1. Written to event_ledger (persistent, replayable)
    2. Pushed to all authenticated SSE subscribers
    3. Pushed to P2-D backwards-compatible SSE subscribers
    """
    # Validate internal service token
    config = None
    try:
        from ...config import get_config
        config = get_config()
    except Exception:
        pass

    if config and config.internal_service_token:
        expected = f"Bearer {config.internal_service_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid internal service token")

    # Publish through unified SSE pipeline
    try:
        from ...sse.publish import publish_event
        sequence = await publish_event(
            event_type=body.event_type,
            data=body.data,
            company_id=body.company_id,
            data_uuid=body.data_uuid,
        )

        logger.info(f"Event published via API: type={body.event_type} seq={sequence}")

        return {
            "status": "published",
            "event_type": body.event_type,
            "sequence": sequence,
        }
    except Exception as e:
        logger.error(f"Event publish failed: {e}", exc_info=True)

        # Fallback: try P2-D event bus directly
        try:
            from ...events import get_event_bus
            bus = get_event_bus()
            await bus.publish(body.event_type, body.data)
            logger.info(f"Event published via P2-D fallback: type={body.event_type}")
            return {
                "status": "published_p2d",
                "event_type": body.event_type,
                "sequence": -1,
            }
        except Exception as e2:
            logger.error(f"P2-D fallback also failed: {e2}")
            raise HTTPException(status_code=500, detail=f"Event publish failed: {e}")
