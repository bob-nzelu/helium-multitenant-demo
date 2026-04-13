"""WS3: Router — POST /api/v1/process_preview endpoint."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from src.orchestrator.models import (
    ProcessPreviewRequest,
    ProcessPreviewResponse200,
    ProcessPreviewResponse202,
    OrchestratorErrorResponse,
)
from src.orchestrator.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["orchestrator"])


@router.post(
    "/process_preview",
    response_model=ProcessPreviewResponse200,
    responses={
        202: {"model": ProcessPreviewResponse202},
        400: {"model": OrchestratorErrorResponse},
        404: {"model": OrchestratorErrorResponse},
        409: {"model": OrchestratorErrorResponse},
        500: {"model": OrchestratorErrorResponse},
    },
)
async def process_preview(request: Request, body: ProcessPreviewRequest):
    """Process a queued file through the full pipeline (Phases 1-7).

    Called by Relay after a successful /api/v1/enqueue. Blocking call —
    returns 200 if completed within 280s, 202 if backgrounded.
    """
    pool = request.app.state.pool
    sse_manager = request.app.state.sse_manager
    config = request.app.state.config
    heartbeat_client = request.app.state.heartbeat_client
    parser_registry = request.app.state.parser_registry

    # Fetch queue entry
    from src.database.pool import get_connection

    try:
        async with get_connection(pool, "core") as conn:
            row = await conn.execute(
                "SELECT * FROM core_queue WHERE queue_id = $1",
                (body.queue_id,),
            )
            queue_entry = await row.fetchone()
    except Exception as exc:
        logger.exception("DB error fetching queue entry %s", body.queue_id)
        raise HTTPException(status_code=500, detail={
            "error_code": "ORCH_004",
            "message": f"Database error: {exc}",
        })

    # Validate queue entry exists
    if not queue_entry:
        raise HTTPException(status_code=404, detail={
            "error_code": "ORCH_002",
            "message": "Queue entry not found",
            "details": {"queue_id": body.queue_id},
        })

    # Convert row to dict if needed
    if not isinstance(queue_entry, dict):
        queue_entry = dict(queue_entry)

    # Validate status — must be PENDING
    entry_status = queue_entry.get("status", "")
    if entry_status not in ("PENDING",):
        raise HTTPException(status_code=409, detail={
            "error_code": "ORCH_003",
            "message": f"Queue entry not in PENDING status (current: {entry_status})",
            "details": {"queue_id": body.queue_id, "current_status": entry_status},
        })

    # Atomic claim: UPDATE only if still PENDING (prevents race condition)
    try:
        async with get_connection(pool, "core") as conn:
            result = await conn.execute(
                "UPDATE core_queue SET status = 'PROCESSING', "
                "processing_started_at = CURRENT_TIMESTAMP "
                "WHERE queue_id = $1 AND status = 'PENDING' "
                "RETURNING queue_id",
                (body.queue_id,),
            )
            claimed = await result.fetchone()
            if not claimed:
                raise HTTPException(status_code=409, detail={
                    "error_code": "ORCH_003",
                    "message": "Queue entry already claimed by another worker",
                    "details": {"queue_id": body.queue_id},
                })
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to claim %s as PROCESSING", body.queue_id)

    # Create orchestrator and run pipeline
    from src.processing.transformer import Transformer
    from src.processing.enricher import Enricher
    from src.processing.resolver import Resolver

    orchestrator = PipelineOrchestrator(
        config=config,
        heartbeat_client=heartbeat_client,
        parser_registry=parser_registry,
        transformer=Transformer(pool, config),
        enricher=Enricher(config=config),
        resolver=Resolver(pool, config),
        sse_manager=sse_manager,
        db_pool=pool,
    )

    try:
        result = await orchestrator.process(body.queue_id, body.data_uuid, queue_entry)
    except Exception as exc:
        logger.exception("Pipeline error for %s", body.queue_id)
        raise HTTPException(status_code=500, detail={
            "error_code": "ORCH_004",
            "message": f"Pipeline failed: {exc}",
            "details": {"queue_id": body.queue_id},
        })

    # Return 200 or 202 based on result type
    if isinstance(result, ProcessPreviewResponse202):
        return JSONResponse(status_code=202, content=result.model_dump())

    return result
