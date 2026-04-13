"""
Tests for WS7 Scheduler Registration — scheduled report jobs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRegisterWs7Jobs:
    @pytest.mark.asyncio
    async def test_registers_three_jobs(self):
        """register_ws7_jobs should register weekly, monthly, and cleanup jobs."""
        with patch("src.scheduler.HAS_APSCHEDULER", True):
            from src.scheduler import register_ws7_jobs

            scheduler = AsyncMock()
            scheduler.add_schedule = AsyncMock()
            pool = AsyncMock()

            await register_ws7_jobs(
                scheduler, pool,
                heartbeat_client=AsyncMock(),
                notification_service=AsyncMock(),
                sse_manager=AsyncMock(),
                audit_logger=AsyncMock(),
            )

            assert scheduler.add_schedule.call_count == 3

            # Verify job IDs
            job_ids = [
                call.kwargs.get("id") or call[1].get("id", "")
                for call in scheduler.add_schedule.call_args_list
            ]
            assert "weekly_compliance_report" in job_ids
            assert "monthly_summary_report" in job_ids
            assert "cleanup_expired_reports" in job_ids

    @pytest.mark.asyncio
    async def test_skips_when_no_apscheduler(self):
        """Should return immediately when APScheduler is not installed."""
        with patch("src.scheduler.HAS_APSCHEDULER", False):
            from src.scheduler import register_ws7_jobs

            scheduler = AsyncMock()
            await register_ws7_jobs(scheduler, AsyncMock())
            scheduler.add_schedule.assert_not_called()
