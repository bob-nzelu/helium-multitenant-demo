"""
Audit Verification API (Q4 — Demo Question)

Endpoint to verify the integrity of the audit event checksum chain.
Used in demos to prove that audit logs cannot be tampered with.

Endpoints:
    GET /api/audit/verify           — Verify full chain or a range
    GET /api/audit/chain/status     — Quick chain health summary
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from ...config import get_config
from ...database.audit_guard import verify_chain, get_last_checksum, GENESIS_HASH


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["Audit Verification"])


@router.get("/verify")
async def verify_audit_chain(
    from_id: Optional[int] = Query(None, description="Start ID (inclusive)"),
    to_id: Optional[int] = Query(None, description="End ID (inclusive)"),
):
    """
    Verify audit event checksum chain integrity.

    Walks through all audit events with checksum_chain values and
    recomputes each checksum. If any row has been tampered with,
    the checksum will not match.

    Query params:
        from_id: Start verification from this event ID
        to_id: End verification at this event ID

    Returns:
        {
            "verified": true/false,
            "chain_length": 50,
            "tampered_rows": [],
            "first_chained_id": 8,
            "last_chained_id": 57
        }
    """
    config = get_config()
    db_path = config.get_blob_db_path()

    result = verify_chain(
        db_path=db_path,
        from_id=from_id,
        to_id=to_id,
    )

    return result


@router.get("/chain/status")
async def audit_chain_status():
    """
    Quick health check of the audit checksum chain.

    Returns the last checksum in the chain and whether
    the genesis hash is being used (no chained events yet).
    """
    config = get_config()
    db_path = config.get_blob_db_path()

    last_checksum = get_last_checksum(db_path)
    is_genesis = (last_checksum == GENESIS_HASH)

    return {
        "status": "active" if not is_genesis else "genesis",
        "last_checksum": last_checksum[:16] + "...",
        "has_chained_events": not is_genesis,
    }
