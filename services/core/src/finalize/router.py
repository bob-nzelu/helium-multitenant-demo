"""
Finalize Router — HTTP endpoints for WS5.

Routes:
  POST /api/v1/finalize          — Finalize an HLX batch (.hlm submission)
  GET  /api/v1/finalize/{batch_id}/status  — Check finalization status
  POST /api/v1/finalize/validate  — Dry-run edit validation only (no commit)

See: HLX_FORMAT.md v1.1, WS5_HANDOFF_NOTE.md
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.finalize.edit_validator import EditValidator
from src.finalize.errors import FinalizeError
from src.finalize.pipeline import FinalizePipeline, FinalizeResult

logger = logging.getLogger(__name__)


async def finalize_batch(request: Request) -> JSONResponse:
    """POST /api/v1/finalize — Finalize an HLX batch.

    Request body:
        {
            "batch_id": "HLX-...",
            "company_id": "COMP-...",
            "service_id": "94ND90NR",       // 8-char FIRS code
            "direction": "OUTBOUND",
            "submitted_rows": [...],          // .hlm data from SDK
            "created_by": "user@example.com"  // optional
        }

    Preview .hlx is fetched from HeartBeat using batch_id.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400
        )

    batch_id = body.get("batch_id")
    company_id = body.get("company_id")
    service_id = body.get("service_id")
    submitted_rows = body.get("submitted_rows", [])
    direction = body.get("direction", "OUTBOUND")
    created_by = body.get("created_by")

    if not batch_id or not company_id or not service_id:
        return JSONResponse(
            {"error": "batch_id, company_id, and service_id are required"},
            status_code=400,
        )

    if not submitted_rows:
        return JSONResponse(
            {"error": "submitted_rows cannot be empty"},
            status_code=400,
        )

    if len(service_id) != 8:
        return JSONResponse(
            {"error": "service_id must be exactly 8 characters"},
            status_code=400,
        )

    # Fetch preview from HeartBeat
    heartbeat = request.app.state.heartbeat_client
    preview_rows = await heartbeat.get_preview(batch_id)
    if preview_rows is None:
        return JSONResponse(
            {"error": f"No preview found for batch {batch_id}"},
            status_code=404,
        )

    # Get pipeline and DB connection
    pipeline: FinalizePipeline = request.app.state.finalize_pipeline
    pool = request.app.state.db_pool

    async with pool.connection() as conn:
        result = await pipeline.finalize(
            submitted_rows=submitted_rows,
            preview_rows=preview_rows,
            conn=conn,
            company_id=company_id,
            batch_id=batch_id,
            service_id=service_id,
            direction=direction,
            created_by=created_by,
        )

    status_code = 200 if result.success else 422
    return JSONResponse(result.to_dict(), status_code=status_code)


async def validate_edits(request: Request) -> JSONResponse:
    """POST /api/v1/finalize/validate — Dry-run validation only.

    Same body as /finalize but only runs the edit diff — no IRN, no DB, no Edge.
    Useful for Float to pre-validate before showing the Finalize button.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body"}, status_code=400
        )

    batch_id = body.get("batch_id")
    submitted_rows = body.get("submitted_rows", [])
    direction = body.get("direction", "OUTBOUND")

    if not batch_id or not submitted_rows:
        return JSONResponse(
            {"error": "batch_id and submitted_rows are required"},
            status_code=400,
        )

    heartbeat = request.app.state.heartbeat_client
    preview_rows = await heartbeat.get_preview(batch_id)
    if preview_rows is None:
        return JSONResponse(
            {"error": f"No preview found for batch {batch_id}"},
            status_code=404,
        )

    validator = EditValidator()
    result = validator.validate(submitted_rows, preview_rows, direction)

    response = {
        "is_valid": result.is_valid,
        "violations": [v.to_dict() for v in result.violations],
        "accepted_changes": len(result.accepted_changes),
        "warnings": result.warnings,
    }
    status_code = 200 if result.is_valid else 422
    return JSONResponse(response, status_code=status_code)


async def finalize_status(request: Request) -> JSONResponse:
    """GET /api/v1/finalize/{batch_id}/status — Check finalization status."""
    batch_id = request.path_params["batch_id"]

    # Check queue status
    queue_repo = request.app.state.queue_repository
    entry = await queue_repo.get_entry(batch_id)

    if not entry:
        return JSONResponse(
            {"error": f"Batch {batch_id} not found"},
            status_code=404,
        )

    # Check Edge status if available
    edge_status = None
    edge_client = getattr(request.app.state, "edge_client", None)
    if edge_client and entry.get("status") == "FINALIZED":
        edge_status_data = await edge_client.get_batch_status(batch_id)
        if edge_status_data:
            edge_status = {
                "status": edge_status_data.status,
                "submitted": edge_status_data.submitted,
                "failed": edge_status_data.failed,
            }

    return JSONResponse({
        "batch_id": batch_id,
        "status": entry.get("status"),
        "edge": edge_status,
    })


# ── Route table ──────────────────────────────────────────────────────────

finalize_routes = [
    Route("/api/v1/finalize", finalize_batch, methods=["POST"]),
    Route("/api/v1/finalize/validate", validate_edits, methods=["POST"]),
    Route("/api/v1/finalize/{batch_id}/status", finalize_status, methods=["GET"]),
]
