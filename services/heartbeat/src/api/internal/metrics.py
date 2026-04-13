"""
Metrics API Endpoint (Internal Service)

POST /api/metrics/report — Report operational metrics (fire-and-forget)

Matches Relay HeartBeatClient's expected URL exactly.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...handlers import metrics_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


class MetricsReportRequest(BaseModel):
    metric_type: str = Field(..., min_length=1)
    values: Dict[str, Any]
    reported_by: Optional[str] = None


class MetricsReportResponse(BaseModel):
    status: str


@router.post(
    "/report",
    response_model=MetricsReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_metrics(request: MetricsReportRequest):
    """
    Report operational metrics to HeartBeat.

    Fire-and-forget from Relay's perspective.
    HeartBeat aggregates these for the monitoring dashboard.
    """
    try:
        result = await metrics_handler.log_metric(
            metric_type=request.metric_type,
            values=request.values,
            reported_by=request.reported_by,
        )
        return result
    except Exception as e:
        logger.error(f"report_metrics failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )
