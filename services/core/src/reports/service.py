"""
WS7: Report Generation Service — orchestrates async report generation.

Flow:
1. Creates core.reports record (status=generating)
2. Launches asyncio.create_task for background generation
3. Returns 202 immediately with report_id

Background task:
1. Calls appropriate generator by report_type
2. Uploads output bytes to HeartBeat blob
3. Updates core.reports (status=ready, blob_uuid, etc.)
4. Sends notification + SSE event
5. On error: updates status=failed
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool
from uuid6 import uuid7

from src.reports import repository
from src.reports.models import (
    REPORT_CONTENT_TYPE,
    REPORT_FORMAT_MAP,
    ReportFormat,
    ReportType,
)
from src.sse.models import SSEEvent

logger = structlog.get_logger()


# Generator dispatch — imported lazily to avoid circular imports
_GENERATORS: dict[ReportType, Any] = {}


def _get_generator(report_type: ReportType):
    """Lazy-load the generator module for a report type."""
    if report_type not in _GENERATORS:
        if report_type == ReportType.COMPLIANCE:
            from src.reports.generators import compliance
            _GENERATORS[report_type] = compliance
        elif report_type == ReportType.TRANSMISSION:
            from src.reports.generators import transmission
            _GENERATORS[report_type] = transmission
        elif report_type == ReportType.CUSTOMER:
            from src.reports.generators import customer
            _GENERATORS[report_type] = customer
        elif report_type == ReportType.AUDIT_TRAIL:
            from src.reports.generators import audit_trail
            _GENERATORS[report_type] = audit_trail
        elif report_type == ReportType.MONTHLY_SUMMARY:
            from src.reports.generators import monthly_summary
            _GENERATORS[report_type] = monthly_summary
    return _GENERATORS[report_type]


# Report type → human title
_REPORT_TITLES: dict[ReportType, str] = {
    ReportType.COMPLIANCE: "Compliance Report",
    ReportType.TRANSMISSION: "Transmission Report",
    ReportType.CUSTOMER: "Customer Report",
    ReportType.AUDIT_TRAIL: "Audit Trail Report",
    ReportType.MONTHLY_SUMMARY: "Monthly Summary Report",
}


async def request_report(
    pool: AsyncConnectionPool,
    *,
    report_type: ReportType,
    report_format: ReportFormat | None,
    filters: dict[str, Any],
    company_id: str,
    generated_by: str | None,
    heartbeat_client: Any,
    notification_service: Any,
    sse_manager: Any,
    audit_logger: Any,
) -> dict[str, Any]:
    """
    Initiate report generation.

    Creates the DB record and launches the background task.
    Returns the initial report record (status=generating).
    """
    # Auto-determine format from report type if not specified
    if report_format is None:
        report_format = REPORT_FORMAT_MAP[report_type]

    report_id = str(uuid7())
    title = _REPORT_TITLES.get(report_type, report_type.value)

    # Create DB record
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO core")
        report = await repository.create_report(
            conn,
            report_id=report_id,
            company_id=company_id,
            report_type=report_type.value,
            format=report_format.value,
            filters=filters,
            generated_by=generated_by,
            title=title,
        )

    # Launch background generation
    asyncio.create_task(
        _generate_report(
            pool=pool,
            report_id=report_id,
            report_type=report_type,
            report_format=report_format,
            filters=filters,
            company_id=company_id,
            generated_by=generated_by,
            title=title,
            heartbeat_client=heartbeat_client,
            notification_service=notification_service,
            sse_manager=sse_manager,
            audit_logger=audit_logger,
        ),
        name=f"report_{report_id}",
    )

    return report


async def _generate_report(
    pool: AsyncConnectionPool,
    report_id: str,
    report_type: ReportType,
    report_format: ReportFormat,
    filters: dict[str, Any],
    company_id: str,
    generated_by: str | None,
    title: str,
    heartbeat_client: Any,
    notification_service: Any,
    sse_manager: Any,
    audit_logger: Any,
) -> None:
    """
    Background task: generate report, upload to HeartBeat, update status.
    """
    start = datetime.now(timezone.utc)
    blob_uuid = str(uuid7())

    try:
        # Run the generator
        generator = _get_generator(report_type)
        file_bytes, content_type = await generator.generate(
            pool, filters, company_id
        )

        # Upload to HeartBeat blob
        ext = "pdf" if report_format == ReportFormat.PDF else "xlsx"
        filename = f"{report_type.value}_{company_id}_{start.strftime('%Y%m%d_%H%M%S')}.{ext}"

        await heartbeat_client.upload_blob(
            blob_uuid=blob_uuid,
            filename=filename,
            data=file_bytes,
            content_type=content_type,
            company_id=company_id,
            metadata={
                "report_type": report_type.value,
                "report_id": report_id,
                "filters": filters,
            },
        )

        generated_at = datetime.now(timezone.utc)
        size_bytes = len(file_bytes)

        # Update DB record
        async with pool.connection() as conn:
            await conn.execute("SET search_path TO core")
            await repository.update_status(
                conn,
                report_id,
                status="ready",
                blob_uuid=blob_uuid,
                size_bytes=size_bytes,
                generated_at=generated_at,
            )

        elapsed_ms = int((generated_at - start).total_seconds() * 1000)

        # Notification
        if notification_service:
            await notification_service.send(
                company_id=company_id,
                notification_type="system",
                category="report_ready",
                title=f"{title} is ready",
                body=f"Your {title.lower()} has been generated and is available for download.",
                recipient_id=generated_by,
                priority="normal",
                data={
                    "report_id": report_id,
                    "report_type": report_type.value,
                    "download_url": f"/api/v1/reports/{report_id}/download",
                },
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )

        # SSE event
        if sse_manager:
            await sse_manager.publish(SSEEvent(
                event_type="report.ready",
                data={
                    "report_id": report_id,
                    "report_type": report_type.value,
                    "status": "ready",
                    "download_url": f"/api/v1/reports/{report_id}/download",
                    "size_bytes": size_bytes,
                },
            ))

        # Audit
        if audit_logger:
            await audit_logger.log(
                event_type="report.generated",
                entity_type="report",
                entity_id=report_id,
                action="CREATE",
                company_id=company_id,
                actor_id=generated_by,
                metadata={
                    "report_type": report_type.value,
                    "format": report_format.value,
                    "size_bytes": size_bytes,
                    "duration_ms": elapsed_ms,
                    "blob_uuid": blob_uuid,
                },
            )

        logger.info(
            "report_generated",
            report_id=report_id,
            report_type=report_type.value,
            size_bytes=size_bytes,
            duration_ms=elapsed_ms,
        )

    except Exception as exc:
        logger.error(
            "report_generation_failed",
            report_id=report_id,
            report_type=report_type.value,
            error=str(exc),
            traceback=traceback.format_exc(),
        )

        try:
            async with pool.connection() as conn:
                await conn.execute("SET search_path TO core")
                await repository.update_status(
                    conn,
                    report_id,
                    status="failed",
                    error_message=str(exc)[:500],
                )
        except Exception as db_err:
            logger.error("report_status_update_failed", error=str(db_err))

        # SSE error event
        if sse_manager:
            try:
                await sse_manager.publish(SSEEvent(
                    event_type="report.failed",
                    data={
                        "report_id": report_id,
                        "report_type": report_type.value,
                        "status": "failed",
                        "error": str(exc)[:200],
                    },
                ))
            except Exception:
                pass
