"""
Daily Limits API Endpoint (Internal Service)

GET /api/limits/daily — Check daily usage limit

Matches Relay HeartBeatClient's expected URL exactly.
"""

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...handlers import limits_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/limits", tags=["limits"])


class DailyLimitResponse(BaseModel):
    company_id: str
    files_today: int
    daily_limit: int
    limit_reached: bool
    remaining: int


@router.get("/daily", response_model=DailyLimitResponse)
async def check_daily_limit(
    company_id: str = Query(..., min_length=1),
    file_count: int = Query(default=1, ge=1),
):
    """
    Check if company has exceeded daily upload limit.

    Does NOT increment the counter — just reads current state.
    Relay calls this before starting file processing.
    """
    result = await limits_handler.check_daily_limit(
        company_id=company_id,
        file_count=file_count,
    )
    return result
