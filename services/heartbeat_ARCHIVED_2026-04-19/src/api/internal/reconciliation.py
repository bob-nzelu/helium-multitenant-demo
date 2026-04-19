"""
Reconciliation API (P2-E)

Endpoints to trigger and query blob reconciliation checks.

Endpoints:
    POST /api/reconciliation/trigger   — Run reconciliation now
    GET  /api/reconciliation/history   — List past reconciliation findings
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from ...config import get_config
from ...database import get_blob_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reconciliation", tags=["Reconciliation"])


@router.post("/trigger")
async def trigger_reconciliation():
    """
    Trigger a reconciliation run.

    Executes all 5 phases synchronously and returns the report.
    Primary mode only — Satellite has no local storage to reconcile.
    """
    config = get_config()
    if not config.is_primary:
        raise HTTPException(
            status_code=403,
            detail="Reconciliation is only available in Primary mode",
        )

    db = get_blob_database()
    filesystem_root = config.get_blob_storage_root()

    from ...handlers.reconciliation_handler import ReconciliationEngine
    engine = ReconciliationEngine(db=db, filesystem_root=filesystem_root)
    report = engine.run()

    return report.to_dict()


@router.get("/history")
async def reconciliation_history(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    finding_type: Optional[str] = Query(None, description="Filter by finding type"),
    unresolved_only: bool = Query(False, description="Only unresolved findings"),
):
    """
    List past reconciliation findings from the notifications table.

    Filters by severity, finding_type, and resolution status.
    """
    db = get_blob_database()

    conditions = ["created_by_service LIKE 'reconciliation/%'"]
    params = []

    if severity:
        conditions.append("severity = ?")
        params.append(severity)

    if finding_type:
        conditions.append("notification_type = ?")
        params.append(finding_type)

    if unresolved_only:
        conditions.append("is_resolved = 0")

    where = " AND ".join(conditions)
    params.append(limit)

    rows = db.execute_query(
        f"""SELECT id, notification_type, severity, blob_uuid, blob_path,
                   message, details, is_resolved, created_at_iso, created_by_service
            FROM notifications
            WHERE {where}
            ORDER BY created_at_unix DESC
            LIMIT ?""",
        tuple(params),
    )

    return {"findings": rows, "count": len(rows)}
