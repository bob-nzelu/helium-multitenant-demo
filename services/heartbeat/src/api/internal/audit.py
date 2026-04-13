"""
Audit API Endpoint (Internal Service)

POST /api/audit/log — Log immutable audit event (fire-and-forget)

Matches Relay HeartBeatClient's expected URL exactly.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...handlers import audit_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditLogRequest(BaseModel):
    service: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    user_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None
    ip_address: Optional[str] = None


class AuditLogResponse(BaseModel):
    status: str
    audit_id: int


@router.post(
    "/log",
    response_model=AuditLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_audit_event(request: AuditLogRequest):
    """
    Log an immutable audit event.

    Fire-and-forget from Relay's perspective — Relay never blocks on this.
    Events are append-only and cannot be modified or deleted.
    """
    try:
        result = await audit_handler.log_audit_event(
            service=request.service,
            event_type=request.event_type,
            user_id=request.user_id,
            details=request.details,
            trace_id=request.trace_id,
            ip_address=request.ip_address,
        )
        return result
    except Exception as e:
        logger.error(f"audit_log failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )
