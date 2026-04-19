"""
Audit Handler — Business logic for immutable audit event logging.

Fire-and-forget: failures logged locally, never block the caller.
Called by api/internal/audit.py router.
"""

import logging
from typing import Any, Dict, Optional

from ..database import get_blob_database

logger = logging.getLogger(__name__)


async def log_audit_event(
    service: str,
    event_type: str,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Log an immutable audit event.

    Matches Relay HeartBeatClient.audit_log() contract.
    Returns: {status: "logged", audit_id: int}
    """
    db = get_blob_database()

    audit_id = db.log_audit_event(
        service=service,
        event_type=event_type,
        user_id=user_id,
        details=details,
        trace_id=trace_id,
        ip_address=ip_address,
    )

    logger.info(f"Audit: {service}/{event_type} (id={audit_id})")

    return {
        "status": "logged",
        "audit_id": audit_id,
    }
