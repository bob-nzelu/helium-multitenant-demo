"""
Mock Core Service — Controlled Pipeline for Abbey Demo

Implements the same API contract as the canonical Core Service:
  POST /api/v1/enqueue          — Accept file reference from Relay
  POST /api/v1/process_preview  — Run mock 7-phase pipeline, generate .hlx
  POST /api/v1/finalize         — Write reviewed invoices to invoices.db
  GET  /api/v1/sse/subscribe    — SSE stream for Float progress events
  GET  /api/v1/health           — Health check

Pipeline phases are fully mocked with realistic timing and stdout logging.
HLX output is a real tar.gz with correct internal structure.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .hlx_generator import generate_mock_hlx
from .sse_manager import SSEManager
from .finalize_writer import FinalizeWriter

logger = logging.getLogger("core.mock")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)

# ── State ────────────────────────────────────────────────────────────────────

queue_store: Dict[str, Dict] = {}  # queue_id → queue entry
sse_manager = SSEManager()


# ── App ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = (
        f"postgresql://"
        f"{os.environ.get('CORE_DB_USER', 'helium')}:"
        f"{os.environ.get('CORE_DB_PASSWORD', 'helium_demo_2026')}@"
        f"{os.environ.get('CORE_DB_HOST', 'localhost')}:"
        f"{os.environ.get('CORE_DB_PORT', '5432')}/"
        f"{os.environ.get('CORE_DB_NAME', 'helium')}"
    )
    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
    app.state.pool = pool
    app.state.writer = FinalizeWriter(pool)
    app.state.start_time = time.time()
    logger.info("Mock Core started — pipeline simulation active")
    yield
    await pool.close()


app = FastAPI(title="Helium Core (Mock)", version="0.1.0-mock", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ───────────────────────────────────────────────────────────────────

class EnqueueRequest(BaseModel):
    blob_uuid: str
    data_uuid: str
    original_filename: str
    company_id: str = "abbey"
    uploaded_by: str = ""
    batch_id: Optional[str] = None
    priority: int = 3


class ProcessPreviewRequest(BaseModel):
    queue_id: str
    data_uuid: str


class FinalizeRequest(BaseModel):
    queue_id: str
    data_uuid: str
    hlx_id: str = ""
    hlm_data: Optional[Dict] = None
    is_refinalize: bool = False


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health(request: Request):
    uptime = time.time() - request.app.state.start_time
    return {
        "status": "healthy",
        "version": "0.1.0-mock",
        "mode": "mock_pipeline",
        "uptime_seconds": round(uptime, 2),
        "database": "connected",
        "scheduler": "running",
    }


# ── WS1: Enqueue ─────────────────────────────────────────────────────────────

@app.post("/api/v1/enqueue")
async def enqueue(body: EnqueueRequest):
    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    entry = {
        "queue_id": queue_id,
        "data_uuid": body.data_uuid,
        "blob_uuid": body.blob_uuid,
        "original_filename": body.original_filename,
        "company_id": body.company_id,
        "uploaded_by": body.uploaded_by,
        "batch_id": body.batch_id,
        "status": "PENDING",
        "created_at": now,
    }
    queue_store[queue_id] = entry

    logger.info(f"ENQUEUE | queue_id={queue_id} file={body.original_filename} data_uuid={body.data_uuid}")

    return {
        "queue_id": queue_id,
        "status": "PENDING",
        "data_uuid": body.data_uuid,
        "created_at": now,
    }


# ── WS3: Process Preview (Mock 7-Phase Pipeline) ─────────────────────────────

PHASES = [
    ("FETCH",       "Fetching file from blob store...",           1.0),
    ("PARSE",       "Parsing file — detecting format...",         1.5),
    ("TRANSFORM",   "Transforming to FIRS-compliant format...",   2.0),
    ("ENRICH",      "Enriching with HSN codes & addresses...",    1.5),
    ("RESOLVE",     "Resolving customers & products...",          1.0),
    ("PORTO_BELLO", "Classifying invoices (duplicate, late, FOC)...", 0.8),
    ("BRANCH",      "Generating HLX document...",                 1.2),
]


@app.post("/api/v1/process_preview")
async def process_preview(body: ProcessPreviewRequest, request: Request):
    entry = queue_store.get(body.queue_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Queue entry not found: {body.queue_id}")

    entry["status"] = "PROCESSING"
    data_uuid = body.data_uuid
    filename = entry.get("original_filename", "unknown.xlsx")
    company_id = entry.get("company_id", "abbey")

    # Determine mock invoice count based on filename
    if "clean" in filename.lower():
        total_invoices = 12
        failed_count = 0
        duplicate_count = 0
    elif "mixed" in filename.lower():
        total_invoices = 8
        failed_count = 2
        duplicate_count = 2
    else:
        total_invoices = 10
        failed_count = 1
        duplicate_count = 1

    valid_count = total_invoices - failed_count - duplicate_count
    start_time = time.time()

    logger.info(f"")
    logger.info(f"{'='*70}")
    logger.info(f"PROCESSING PIPELINE START | data_uuid={data_uuid}")
    logger.info(f"  File: {filename}")
    logger.info(f"  Invoices: {total_invoices} (expected: {valid_count} valid, {failed_count} failed, {duplicate_count} dupes)")
    logger.info(f"{'='*70}")

    # Run mock phases with SSE events
    phase_timings = {}
    for i, (phase_name, phase_msg, phase_delay) in enumerate(PHASES):
        phase_start = time.time()

        # SSE: processing.log
        await sse_manager.publish({
            "event_type": "processing.log",
            "data_uuid": data_uuid,
            "data": {"message": phase_msg, "level": "info", "phase": phase_name},
        })

        logger.info(f"  Phase {i+1}/7: {phase_name} — {phase_msg}")

        await asyncio.sleep(phase_delay)

        # SSE: processing.progress
        invoices_ready = min((i + 1) * (total_invoices // 7 + 1), total_invoices)
        await sse_manager.publish({
            "event_type": "processing.progress",
            "data_uuid": data_uuid,
            "data": {
                "invoices_ready": invoices_ready,
                "invoices_total": total_invoices,
                "phase": phase_name,
                "phases_completed": i + 1,
                "phases_total": 7,
            },
        })

        phase_elapsed = round((time.time() - phase_start) * 1000)
        phase_timings[phase_name.lower()] = phase_elapsed
        logger.info(f"  Phase {i+1}/7: {phase_name} — DONE ({phase_elapsed}ms)")

    # Generate mock HLX
    hlx_bytes, hlx_blob_uuid = generate_mock_hlx(
        data_uuid=data_uuid,
        company_id=company_id,
        total_invoices=total_invoices,
        valid_count=valid_count,
        failed_count=failed_count,
        duplicate_count=duplicate_count,
        filename=filename,
    )

    total_time = round((time.time() - start_time) * 1000)

    # SSE: processing.complete
    await sse_manager.publish({
        "event_type": "processing.complete",
        "data_uuid": data_uuid,
        "data": {
            "status": "preview_ready",
            "hlx_blob_uuid": hlx_blob_uuid,
            "statistics": {
                "total_invoices": total_invoices,
                "valid_count": valid_count,
                "failed_count": failed_count,
                "duplicate_count": duplicate_count,
                "processing_time_ms": total_time,
                "confidence": 0.94,
            },
        },
    })

    entry["status"] = "PREVIEW_READY"
    entry["hlx_blob_uuid"] = hlx_blob_uuid

    logger.info(f"")
    logger.info(f"{'='*70}")
    logger.info(f"PIPELINE COMPLETE | data_uuid={data_uuid}")
    logger.info(f"  Total time: {total_time}ms")
    logger.info(f"  Valid: {valid_count} | Failed: {failed_count} | Duplicates: {duplicate_count}")
    logger.info(f"  HLX: {hlx_blob_uuid}")
    logger.info(f"{'='*70}")
    logger.info(f"")

    return {
        "queue_id": body.queue_id,
        "data_uuid": data_uuid,
        "status": "preview_ready",
        "statistics": {
            "total_invoices": total_invoices,
            "valid_count": valid_count,
            "failed_count": failed_count,
            "duplicate_count": duplicate_count,
            "processing_time_ms": total_time,
            "confidence": 0.94,
        },
        "red_flags": [],
        "hlx_blob_uuid": hlx_blob_uuid,
        "phase_timings": phase_timings,
    }


# ── WS5: Finalize ────────────────────────────────────────────────────────────

@app.post("/api/v1/finalize")
async def finalize(body: FinalizeRequest, request: Request):
    entry = queue_store.get(body.queue_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Queue entry not found: {body.queue_id}")

    writer: FinalizeWriter = request.app.state.writer
    data_uuid = body.data_uuid

    logger.info(f"")
    logger.info(f"FINALIZE START | data_uuid={data_uuid} queue_id={body.queue_id}")

    # SSE: hlx.finalize_started
    await sse_manager.publish({
        "event_type": "hlx.finalize_started",
        "data_uuid": data_uuid,
        "data": {"queue_id": body.queue_id},
    })

    try:
        result = await writer.finalize_invoices(
            data_uuid=data_uuid,
            company_id=entry.get("company_id", "abbey"),
            hlm_data=body.hlm_data,
        )

        # SSE: hlx.finalized + invoice.created events
        await sse_manager.publish({
            "event_type": "hlx.finalized",
            "data_uuid": data_uuid,
            "data": result,
        })

        for irn in result.get("irn_list", []):
            await sse_manager.publish({
                "event_type": "invoice.created",
                "data_uuid": data_uuid,
                "data": {"irn": irn},
            })

        entry["status"] = "FINALIZED"

        logger.info(f"FINALIZE COMPLETE | invoices_created={result.get('statistics', {}).get('invoices_created', 0)}")

        return {
            "queue_id": body.queue_id,
            "data_uuid": data_uuid,
            "hlx_id": body.hlx_id,
            "status": "finalized",
            **result,
        }

    except Exception as e:
        logger.error(f"FINALIZE FAILED | {e}")
        await sse_manager.publish({
            "event_type": "hlx.finalize_failed",
            "data_uuid": data_uuid,
            "data": {"error": str(e)},
        })
        raise HTTPException(status_code=500, detail=str(e))


# ── SSE: Subscribe ────────────────────────────────────────────────────────────

@app.get("/api/v1/sse/subscribe")
async def sse_subscribe(request: Request, data_uuid: str = "", last_event_id: int = 0):
    """SSE stream — Float SDK subscribes here for real-time progress."""
    async def event_generator():
        queue = asyncio.Queue()
        client_id = sse_manager.add_client(queue, data_uuid_filter=data_uuid or None)
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"event: {event['event_type']}\ndata: {json.dumps(event.get('data', {}))}\nid: {event.get('id', 0)}\n\n"
        except asyncio.TimeoutError:
            yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.remove_client(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Queue Status ──────────────────────────────────────────────────────────────

@app.get("/api/v1/core_queue/status")
async def queue_status(queue_id: str = ""):
    if queue_id:
        entry = queue_store.get(queue_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")
        return entry
    return {"queue": list(queue_store.values()), "total": len(queue_store)}
