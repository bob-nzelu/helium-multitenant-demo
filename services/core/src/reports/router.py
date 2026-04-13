"""
WS7: Report Router — generate, status, download endpoints.

POST /api/v1/reports/generate  → 202 Accepted (async generation)
GET  /api/v1/reports/{id}/status   → Report metadata + download URL
GET  /api/v1/reports/{id}/download → Binary file (PDF or Excel)
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from src.auth.permissions import check_permission, get_user_id
from src.errors import CoreError, CoreErrorCode
from src.reports import repository
from src.reports.models import (
    REPORT_CONTENT_TYPE,
    GenerateReportRequest,
    GenerateReportResponse,
    ReportFormat,
    ReportStatusResponse,
)
from src.reports.service import request_report

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.post("/reports/generate", status_code=202)
async def generate_report(
    request: Request,
    body: GenerateReportRequest,
) -> GenerateReportResponse:
    """
    Initiate async report generation.

    Returns 202 with report_id. Poll /reports/{id}/status for completion.
    """
    check_permission(request, "invoice.read")

    pool = request.app.state.pool
    user_id = get_user_id(request)

    # Get company_id from JWT claims (or default for dev)
    claims = getattr(request.state, "jwt_claims", {})
    company_id = claims.get("company_id", "default")

    report = await request_report(
        pool,
        report_type=body.report_type,
        report_format=body.format,
        filters=body.filters.model_dump(mode="json", exclude_none=True),
        company_id=company_id,
        generated_by=user_id,
        heartbeat_client=request.app.state.heartbeat_client,
        notification_service=request.app.state.notification_service,
        sse_manager=request.app.state.sse_manager,
        audit_logger=request.app.state.audit_logger,
    )

    return GenerateReportResponse(
        report_id=report["report_id"],
        status="generating",
        estimated_seconds=30,
    )


@router.get("/reports/{report_id}/status")
async def report_status(
    request: Request,
    report_id: str,
) -> ReportStatusResponse:
    """Get report generation status and metadata."""
    check_permission(request, "invoice.read")

    pool = request.app.state.pool
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO core")
        report = await repository.get_report(conn, report_id)

    if report is None:
        raise CoreError(
            error_code=CoreErrorCode.REPORT_NOT_FOUND,
            message=f"Report {report_id} not found",
        )

    return ReportStatusResponse.from_row(report)


@router.get("/reports/{report_id}/download")
async def download_report(
    request: Request,
    report_id: str,
) -> Response:
    """
    Download a generated report file.

    Fetches the blob from HeartBeat and streams it as an attachment.
    """
    check_permission(request, "invoice.read")

    pool = request.app.state.pool
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO core")
        report = await repository.get_report(conn, report_id)

    if report is None:
        raise CoreError(
            error_code=CoreErrorCode.REPORT_NOT_FOUND,
            message=f"Report {report_id} not found",
        )

    if report["status"] != "ready":
        if report["status"] == "expired":
            raise CoreError(
                error_code=CoreErrorCode.REPORT_EXPIRED,
                message=f"Report {report_id} has expired",
            )
        raise CoreError(
            error_code=CoreErrorCode.REPORT_NOT_READY,
            message=f"Report {report_id} is not ready (status: {report['status']})",
        )

    blob_uuid = report["blob_uuid"]
    if not blob_uuid:
        raise CoreError(
            error_code=CoreErrorCode.REPORT_GENERATION_FAILED,
            message=f"Report {report_id} has no associated blob",
        )

    # Fetch blob from HeartBeat
    heartbeat_client = request.app.state.heartbeat_client
    blob_response = await heartbeat_client.fetch_blob(blob_uuid)

    # Determine content type and filename
    report_format = ReportFormat(report["format"])
    content_type = REPORT_CONTENT_TYPE[report_format]
    ext = "pdf" if report_format == ReportFormat.PDF else "xlsx"
    filename = f"{report['report_type']}_{report_id[:8]}.{ext}"

    return Response(
        content=blob_response.data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
