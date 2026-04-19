"""
Security Events API (P2-B — Wazuh Integration)

Query endpoints for security events logged by the WazuhEventEmitter.

Endpoints:
    GET /api/security/events       — List recent security events
    GET /api/security/events/stats — Event count by class/severity
"""

import logging
import sqlite3
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query

from ...config import get_config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security", tags=["Security Events"])


@router.get("/events")
async def list_security_events(
    event_class: Optional[str] = Query(None, description="Filter by event class"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    limit: int = Query(50, ge=1, le=500),
):
    """List recent security events (newest first)."""
    config = get_config()
    db_path = config.get_blob_db_path()

    conditions = []
    params = []

    if event_class:
        conditions.append("event_class = ?")
        params.append(event_class)
    if severity:
        conditions.append("severity = ?")
        params.append(severity)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            f"SELECT * FROM security_events {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return {"events": rows, "count": len(rows)}
    except sqlite3.OperationalError:
        return {"events": [], "count": 0, "note": "security_events table not yet created"}


@router.get("/events/stats")
async def security_event_stats():
    """Get event count grouped by class and severity."""
    config = get_config()
    db_path = config.get_blob_db_path()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        by_class = conn.execute(
            "SELECT event_class, COUNT(*) as count FROM security_events GROUP BY event_class"
        ).fetchall()

        by_severity = conn.execute(
            "SELECT severity, COUNT(*) as count FROM security_events GROUP BY severity"
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) as count FROM security_events"
        ).fetchone()

        conn.close()

        return {
            "total": total["count"] if total else 0,
            "by_class": {r["event_class"]: r["count"] for r in by_class},
            "by_severity": {r["severity"]: r["count"] for r in by_severity},
        }
    except sqlite3.OperationalError:
        return {"total": 0, "by_class": {}, "by_severity": {}, "note": "security_events table not yet created"}
