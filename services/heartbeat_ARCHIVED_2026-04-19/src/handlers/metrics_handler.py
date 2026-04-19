"""
Metrics Handler — Business logic for operational metrics reporting.

Fire-and-forget: failures logged locally, never block the caller.
Called by api/internal/metrics.py router.
"""

import logging
from typing import Any, Dict, Optional

from ..database import get_blob_database

logger = logging.getLogger(__name__)


async def log_metric(
    metric_type: str,
    values: Dict[str, Any],
    reported_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record an operational metric.

    Matches Relay HeartBeatClient.report_metrics() contract.
    Returns: {status: "recorded"}
    """
    db = get_blob_database()

    db.log_metric(
        metric_type=metric_type,
        values=values,
        reported_by=reported_by,
    )

    logger.debug(f"Metric: {metric_type} — {values}")

    return {
        "status": "recorded",
    }
