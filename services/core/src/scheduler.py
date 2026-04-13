"""
APScheduler Setup

Per D-WS0-006: PostgreSQL job store for persistence across restarts.
WS0 defines 4 job types as stubs — real implementations come from WS3/WS5.

APScheduler v4 requires async context manager initialization before adding schedules.
The lifespan in app.py uses the scheduler as a context manager.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# Module-level references for APScheduler (closures can't be serialized)
_pool = None
_event_ledger = None
_heartbeat_client = None
_notification_service = None
_sse_manager = None
_audit_logger = None


def set_scheduler_deps(pool, event_ledger=None, heartbeat_client=None,
                       notification_service=None, sse_manager=None, audit_logger=None):
    """Store references for scheduled job functions (avoids closures)."""
    global _pool, _event_ledger, _heartbeat_client, _notification_service, _sse_manager, _audit_logger
    _pool = pool
    _event_ledger = event_ledger
    _heartbeat_client = heartbeat_client
    _notification_service = notification_service
    _sse_manager = sse_manager
    _audit_logger = audit_logger


# ── Stub job functions (real implementations in WS3/WS5) ─────────────────


async def cleanup_processed_entries() -> None:
    """Delete PROCESSED queue entries older than 24 hours."""
    logger.info("scheduler_job_stub", job="cleanup_processed_entries")


async def cleanup_preview_data() -> None:
    """Delete PREVIEW_READY entries older than 7 days."""
    logger.info("scheduler_job_stub", job="cleanup_preview_data")


async def cleanup_failed_entries() -> None:
    """Delete FAILED entries older than 30 days."""
    logger.info("scheduler_job_stub", job="cleanup_failed_entries")


async def recover_orphaned_entries() -> None:
    """Reset stuck PROCESSING entries back to PENDING on startup."""
    logger.info("scheduler_job_stub", job="recover_orphaned_entries")


# ── Scheduler lifecycle ──────────────────────────────────────────────────

try:
    from apscheduler import AsyncScheduler, ConflictPolicy
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False


def create_scheduler() -> "AsyncScheduler | None":
    """
    Create an APScheduler instance (not yet started).

    Returns None if APScheduler is not available.
    Schedules are added after __aenter__ in register_jobs().
    """
    if not HAS_APSCHEDULER:
        logger.warning("apscheduler_not_available")
        return None

    scheduler = AsyncScheduler()
    logger.info("scheduler_created")
    return scheduler


async def register_jobs(scheduler: "AsyncScheduler") -> None:
    """
    Register periodic job schedules.

    Must be called AFTER scheduler.__aenter__() (inside async with block).
    """
    # Preview cleanup: daily at 03:00 UTC
    await scheduler.add_schedule(
        cleanup_preview_data,
        CronTrigger(hour=3, minute=0),
        id="cleanup_preview_data",
        conflict_policy=ConflictPolicy.replace,
    )

    # Failed entry cleanup: daily at 04:00 UTC
    await scheduler.add_schedule(
        cleanup_failed_entries,
        CronTrigger(hour=4, minute=0),
        id="cleanup_failed_entries",
        conflict_policy=ConflictPolicy.replace,
    )

    logger.info("scheduler_jobs_registered", jobs=["cleanup_preview_data", "cleanup_failed_entries"])


# ── Module-level job functions (APScheduler v4 needs serializable refs) ──


async def job_cleanup_deleted() -> None:
    """WS4: Soft-delete cleanup."""
    from src.jobs.cleanup_deleted import cleanup_expired_soft_deletes
    await cleanup_expired_soft_deletes(_pool)


async def job_cleanup_completed_queue() -> None:
    """Queue maintenance: 24h cleanup."""
    from src.jobs.cleanup_completed_queue import cleanup_completed_queue_entries
    await cleanup_completed_queue_entries(_pool)


async def job_prune_ledger() -> None:
    """SSE ledger pruning."""
    try:
        deleted = await _event_ledger.prune(_pool)
        if deleted > 0:
            logger.info("ledger_prune_completed", rows_deleted=deleted)
    except Exception as e:
        logger.error("ledger_prune_failed", error=str(e))


async def job_weekly_compliance() -> None:
    """WS7: Weekly compliance report."""
    from src.reports.models import ReportFormat, ReportType
    from src.reports.service import request_report
    logger.info("scheduled_report_starting", report_type="compliance")
    try:
        await request_report(
            _pool,
            report_type=ReportType.COMPLIANCE,
            report_format=ReportFormat.PDF,
            filters={},
            company_id="default",
            generated_by="scheduler",
            heartbeat_client=_heartbeat_client,
            notification_service=_notification_service,
            sse_manager=_sse_manager,
            audit_logger=_audit_logger,
        )
    except Exception as e:
        logger.error("scheduled_report_failed", report_type="compliance", error=str(e))


async def job_monthly_summary() -> None:
    """WS7: Monthly summary report."""
    from src.reports.models import ReportFormat, ReportType
    from src.reports.service import request_report
    logger.info("scheduled_report_starting", report_type="monthly_summary")
    try:
        await request_report(
            _pool,
            report_type=ReportType.MONTHLY_SUMMARY,
            report_format=ReportFormat.PDF,
            filters={},
            company_id="default",
            generated_by="scheduler",
            heartbeat_client=_heartbeat_client,
            notification_service=_notification_service,
            sse_manager=_sse_manager,
            audit_logger=_audit_logger,
        )
    except Exception as e:
        logger.error("scheduled_report_failed", report_type="monthly_summary", error=str(e))


async def job_cleanup_expired_reports() -> None:
    """WS7: Report cleanup."""
    from src.reports import repository as report_repo
    try:
        async with _pool.connection() as conn:
            await conn.execute("SET search_path TO core")
            deleted = await report_repo.cleanup_expired(conn)
        if deleted > 0:
            logger.info("expired_reports_cleaned", count=deleted)
    except Exception as e:
        logger.error("report_cleanup_failed", error=str(e))


# ── Registration functions ───────────────────────────────────────────────


async def register_ws4_jobs(scheduler: "AsyncScheduler", pool) -> None:
    if not HAS_APSCHEDULER:
        return
    await scheduler.add_schedule(
        job_cleanup_deleted,
        IntervalTrigger(hours=1),
        id="cleanup_expired_soft_deletes",
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("scheduler_ws4_jobs_registered", jobs=["cleanup_expired_soft_deletes"])


async def register_queue_jobs(scheduler: "AsyncScheduler", pool) -> None:
    if not HAS_APSCHEDULER:
        return
    await scheduler.add_schedule(
        job_cleanup_completed_queue,
        IntervalTrigger(hours=1),
        id="cleanup_completed_queue",
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("scheduler_queue_jobs_registered", jobs=["cleanup_completed_queue"])


async def register_sse_jobs(scheduler: "AsyncScheduler", pool, event_ledger) -> None:
    if not HAS_APSCHEDULER:
        return
    await scheduler.add_schedule(
        job_prune_ledger,
        IntervalTrigger(hours=6),
        id="prune_event_ledger",
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info("scheduler_sse_jobs_registered", jobs=["prune_event_ledger"])


async def register_ws7_jobs(
    scheduler: "AsyncScheduler", pool,
    heartbeat_client=None, notification_service=None,
    sse_manager=None, audit_logger=None,
) -> None:
    if not HAS_APSCHEDULER:
        return
    await scheduler.add_schedule(
        job_weekly_compliance,
        CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="weekly_compliance_report",
        conflict_policy=ConflictPolicy.replace,
    )
    await scheduler.add_schedule(
        job_monthly_summary,
        CronTrigger(day=1, hour=6, minute=0),
        id="monthly_summary_report",
        conflict_policy=ConflictPolicy.replace,
    )
    await scheduler.add_schedule(
        job_cleanup_expired_reports,
        IntervalTrigger(hours=6),
        id="cleanup_expired_reports",
        conflict_policy=ConflictPolicy.replace,
    )
    logger.info(
        "scheduler_ws7_jobs_registered",
        jobs=["weekly_compliance_report", "monthly_summary_report", "cleanup_expired_reports"],
    )
