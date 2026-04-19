"""
Limits Handler — Business logic for daily upload limits.

Called by api/internal/limits.py router.
"""

import logging
from typing import Any, Dict

from ..config import get_config
from ..database import get_blob_database

logger = logging.getLogger(__name__)


async def check_daily_limit(
    company_id: str,
    file_count: int = 1,
) -> Dict[str, Any]:
    """
    Check if company has exceeded daily upload limit.

    Does NOT increment — just reads current state.
    Increment happens at register_blob time.

    Matches Relay HeartBeatClient.check_daily_limit() contract:
        Returns: {company_id, files_today, daily_limit, limit_reached: bool, remaining}
    """
    db = get_blob_database()
    config = get_config()

    usage = db.get_daily_usage(company_id)

    if usage:
        files_today = usage["file_count"]
        daily_limit = usage["daily_limit"]
    else:
        files_today = 0
        daily_limit = config.default_daily_limit

    remaining = max(0, daily_limit - files_today)
    limit_reached = (files_today + file_count) > daily_limit

    return {
        "company_id": company_id,
        "files_today": files_today,
        "daily_limit": daily_limit,
        "limit_reached": limit_reached,
        "remaining": remaining,
    }
