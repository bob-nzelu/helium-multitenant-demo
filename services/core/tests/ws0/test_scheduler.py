"""Tests for scheduler — creation, job registration, stub functions."""

import pytest

from src.scheduler import (
    HAS_APSCHEDULER,
    cleanup_failed_entries,
    cleanup_preview_data,
    cleanup_processed_entries,
    create_scheduler,
    recover_orphaned_entries,
    register_jobs,
)


class TestSchedulerCreation:
    """Test scheduler creation (synchronous)."""

    def test_create_scheduler_returns_instance(self):
        if not HAS_APSCHEDULER:
            pytest.skip("APScheduler not installed")
        scheduler = create_scheduler()
        assert scheduler is not None

    def test_create_scheduler_without_apscheduler(self, monkeypatch):
        import src.scheduler as mod
        original = mod.HAS_APSCHEDULER
        monkeypatch.setattr(mod, "HAS_APSCHEDULER", False)
        result = create_scheduler()
        assert result is None
        monkeypatch.setattr(mod, "HAS_APSCHEDULER", original)


@pytest.mark.asyncio
class TestSchedulerJobs:
    """Test job registration requires initialized scheduler."""

    async def test_register_jobs_with_initialized_scheduler(self):
        if not HAS_APSCHEDULER:
            pytest.skip("APScheduler not installed")
        scheduler = create_scheduler()
        async with scheduler:
            await register_jobs(scheduler)
            # If we get here without error, jobs registered successfully


@pytest.mark.asyncio
class TestStubJobs:
    """Test stub job functions don't raise."""

    async def test_cleanup_processed(self):
        await cleanup_processed_entries()

    async def test_cleanup_preview(self):
        await cleanup_preview_data()

    async def test_cleanup_failed(self):
        await cleanup_failed_entries()

    async def test_recover_orphaned(self):
        await recover_orphaned_entries()
