"""
WS1 Ingestion Router

POST /api/v1/enqueue       — Accept file from Relay, create queue entry, start processing
GET  /api/v1/core_queue/status — Query queue state (for HeartBeat reconciliation)
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, Request
from uuid6 import uuid7

from src.database.pool import get_connection
from src.errors import DuplicateError, ValidationError
from src.ingestion.dedup import DedupChecker
from src.ingestion.file_detector import detect_file_type
from src.ingestion.models import (
    EnqueueRequest,
    EnqueueResponse,
    QueueStatusEntry,
    QueueStatusResponse,
    RedFlag,
)
from src.ingestion.parsers.registry import ParserRegistry
from src.sse.models import SSEEvent

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


# ── POST /enqueue ──────────────────────────────────────────────────────────


@router.post("/enqueue", response_model=EnqueueResponse, status_code=201)
async def enqueue(request: Request, body: EnqueueRequest) -> EnqueueResponse:
    """Accept a file processing request from Relay."""
    pool = request.app.state.pool
    sse_manager = request.app.state.sse_manager
    config = request.app.state.config

    # Idempotency: check if blob_uuid already queued
    async with get_connection(pool, "core") as conn:
        cur = await conn.execute(
            "SELECT queue_id, status, data_uuid FROM core_queue WHERE blob_uuid = %s",
            (body.blob_uuid,),
        )
        existing = await cur.fetchone()

    if existing:
        raise DuplicateError(
            f"Blob {body.blob_uuid} already queued",
            details=[{
                "existing_queue_id": existing[0],
                "existing_status": existing[1],
            }],
        )

    # Create queue entry
    queue_id = str(uuid7())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    async with get_connection(pool, "core") as conn:
        await conn.execute(
            """INSERT INTO core_queue
               (queue_id, blob_uuid, data_uuid, original_filename, company_id,
                uploaded_by, batch_id, priority, status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', %s, %s)""",
            (
                queue_id, body.blob_uuid, body.data_uuid, body.original_filename,
                body.company_id, body.uploaded_by, body.batch_id, body.priority,
                now, now,
            ),
        )

    logger.info(
        "queue_entry_created",
        queue_id=queue_id,
        blob_uuid=body.blob_uuid,
        company_id=body.company_id,
    )

    # WS6: Audit file.received + queue.enqueued
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if audit_logger:
        await audit_logger.log(
            event_type="file.received",
            entity_type="queue",
            entity_id=queue_id,
            action="CREATE",
            company_id=body.company_id,
            actor_id=body.uploaded_by,
            metadata={
                "blob_uuid": body.blob_uuid,
                "data_uuid": body.data_uuid,
                "original_filename": body.original_filename,
            },
        )
        await audit_logger.log(
            event_type="queue.enqueued",
            entity_type="queue",
            entity_id=queue_id,
            action="CREATE",
            company_id=body.company_id,
            actor_id=body.uploaded_by,
            metadata={
                "data_uuid": body.data_uuid,
                "priority": body.priority,
                "batch_id": body.batch_id,
            },
        )

    return EnqueueResponse(
        queue_id=queue_id,
        status="PENDING",
        data_uuid=body.data_uuid,
        created_at=now_iso,
    )


# ── GET /core_queue/status ─────────────────────────────────────────────────


@router.get("/core_queue/status", response_model=QueueStatusResponse)
async def get_queue_status(
    request: Request,
    company_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    since: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> QueueStatusResponse:
    """Query queue status (used by HeartBeat reconciliation)."""
    pool = request.app.state.pool

    # Build dynamic WHERE clause
    conditions: list[str] = []
    params: list = []

    if company_id:
        conditions.append("company_id = %s")
        params.append(company_id)
    if status_filter:
        conditions.append("status = %s")
        params.append(status_filter)
    if since:
        conditions.append("created_at >= %s")
        params.append(since)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with get_connection(pool, "core") as conn:
        # Count total
        cur = await conn.execute(
            f"SELECT COUNT(*) FROM core_queue {where}", params
        )
        row = await cur.fetchone()
        total = row[0] if row else 0

        # Fetch page
        cur = await conn.execute(
            f"""SELECT queue_id, blob_uuid, data_uuid, status, company_id,
                       original_filename, created_at, updated_at, processed_at, error_message
                FROM core_queue {where}
                ORDER BY priority DESC, created_at ASC
                LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    entries = [
        QueueStatusEntry(
            queue_id=r[0],
            blob_uuid=r[1],
            data_uuid=r[2] or "",
            status=r[3],
            company_id=r[4],
            original_filename=r[5],
            created_at=r[6].isoformat() if r[6] else "",
            updated_at=r[7].isoformat() if r[7] else "",
            processed_at=r[8].isoformat() if r[8] else None,
            error_message=r[9],
        )
        for r in rows
    ]

    return QueueStatusResponse(
        entries=entries,
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Processing Logic ───────────────────────────────────────────────────────


async def process_entry(
    *,
    queue_id: str,
    blob_uuid: str,
    data_uuid: str,
    original_filename: str,
    company_id: str,
    pool,
    sse_manager,
    heartbeat_client,
    parser_registry: ParserRegistry,
    audit_logger=None,
) -> None:
    """
    Process a single queue entry: fetch blob → detect type → parse → dedup → update status.

    Called by the safety-net scanner or WS3's /process_preview trigger.
    """
    try:
        # 1. Mark as PROCESSING
        async with get_connection(pool, "core") as conn:
            await conn.execute(
                """UPDATE core_queue
                   SET status = 'PROCESSING', processing_started_at = NOW(), updated_at = NOW()
                   WHERE queue_id = %s AND status = 'PENDING'""",
                (queue_id,),
            )

        # 2. Fetch blob from HeartBeat
        blob_resp = await heartbeat_client.fetch_blob(blob_uuid)
        file_bytes = blob_resp.content

        # 3. Detect file type
        file_type = detect_file_type(file_bytes, original_filename)

        # WS6: Audit file.type_detected
        if audit_logger:
            await audit_logger.log(
                event_type="file.type_detected",
                entity_type="queue",
                entity_id=queue_id,
                action="PROCESS",
                company_id=company_id,
                metadata={"detected_type": file_type, "original_filename": original_filename},
            )

        # 4. Parse
        parser = parser_registry.get(file_type)
        parse_result = await parser.parse(file_bytes, original_filename)

        # WS6: Audit file.parsed
        if audit_logger:
            await audit_logger.log(
                event_type="file.parsed",
                entity_type="queue",
                entity_id=queue_id,
                action="PROCESS",
                company_id=company_id,
                metadata={
                    "file_type": parse_result.file_type,
                    "row_count": parse_result.metadata.row_count,
                    "is_hlm": parse_result.is_hlm,
                },
            )

        # 5. Compute hash and set on result
        file_hash = DedupChecker.compute_hash(file_bytes)
        parse_result.file_hash = file_hash

        # 6. Dedup check
        dedup_result = await DedupChecker.check(file_hash, pool)

        # WS6: Audit file.dedup_checked
        if audit_logger:
            await audit_logger.log(
                event_type="file.dedup_checked",
                entity_type="queue",
                entity_id=queue_id,
                action="PROCESS",
                company_id=company_id,
                metadata={
                    "file_hash": file_hash,
                    "is_duplicate": dedup_result.is_duplicate,
                },
            )

        if dedup_result.is_duplicate:
            parse_result.red_flags.append(RedFlag(
                field_name="file_hash",
                message=f"Duplicate file detected (previously processed as {dedup_result.existing_filename})",
                severity="warning",
            ))

        # 7. Mark as PROCESSED
        async with get_connection(pool, "core") as conn:
            await conn.execute(
                """UPDATE core_queue
                   SET status = 'PROCESSED', processed_at = NOW(), updated_at = NOW()
                   WHERE queue_id = %s""",
                (queue_id,),
            )

        # 8. Emit SSE event
        await sse_manager.publish(SSEEvent(
            event_type="queue.entry_processed",
            data={
                "queue_id": queue_id,
                "data_uuid": data_uuid,
                "blob_uuid": blob_uuid,
                "file_type": parse_result.file_type,
                "is_hlm": parse_result.is_hlm,
                "row_count": parse_result.metadata.row_count,
                "red_flag_count": len(parse_result.red_flags),
                "is_duplicate": dedup_result.is_duplicate,
            },
            data_uuid=data_uuid,
        ))

        logger.info(
            "entry_processed",
            queue_id=queue_id,
            file_type=parse_result.file_type,
            rows=parse_result.metadata.row_count,
        )

    except Exception as e:
        logger.error("entry_processing_failed", queue_id=queue_id, error=str(e))

        # Mark as FAILED, increment retry_count
        try:
            async with get_connection(pool, "core") as conn:
                await conn.execute(
                    """UPDATE core_queue
                       SET status = 'FAILED',
                           error_message = %s,
                           retry_count = retry_count + 1,
                           updated_at = NOW()
                       WHERE queue_id = %s""",
                    (str(e)[:1000], queue_id),
                )
        except Exception:
            logger.exception("failed_to_update_queue_status", queue_id=queue_id)
