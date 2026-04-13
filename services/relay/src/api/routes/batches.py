"""
Batch history + lookup endpoints.

GET    /api/batches         — fetch all stored batch results (initial sync)
GET    /api/batches/stream  — SSE real-time stream (no auth — EventSource limitation)
DELETE /api/batches         — clear all stored batches (requires API key)
GET    /api/lookup          — look up a single transaction by ID (requires API key)
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...core.tenant import TenantConfig
from ...services.batch_store import BatchStore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/batches", summary="Fetch all stored batch results")
async def get_batches(
    request: Request,
    tenant: str = Query(default="", description="Filter by tenant_id (empty = all tenants)"),
):
    """
    Returns all batch results. Optional tenant filter for scoped views.
    The dashboard calls this on load to sync historical data before opening SSE.
    """
    store: BatchStore = request.app.state.batch_store
    return JSONResponse(content={"batches": await store.all(tenant_id=tenant or None)})


@router.delete(
    "/api/batches",
    summary="Clear all stored batch results",
)
async def clear_batches(
    request: Request,
    x_api_key: str = Header(..., description="API key"),
    tenant: str = Query(default="", description="Tenant ID to clear (empty = all)"),
):
    """
    Clears batch results. Called by the dashboard Clear History action.
    Requires a valid API key header. Optional tenant scoping.
    """
    tenant_registry = request.app.state.tenant_registry
    if x_api_key not in tenant_registry:
        raise HTTPException(status_code=401, detail="Invalid API key")

    store: BatchStore = request.app.state.batch_store
    await store.clear(tenant_id=tenant or None)
    logger.info(f"BatchStore cleared by api_key={x_api_key[:8]}... tenant={tenant or 'ALL'}")
    return JSONResponse(content={"status": "cleared"})


@router.get("/api/batches/stream", summary="SSE real-time stream of batch results")
async def stream_batches(request: Request):
    """
    Server-Sent Events stream. Dashboard connects once and receives every
    completed batch in real time — whether from AB MFB or the test dashboard.

    No auth header — browser EventSource cannot send custom headers.
    Data contains only operational results (no credentials).

    Sends a keepalive comment every 25 seconds to prevent proxy/browser timeout.
    """
    store: BatchStore = request.app.state.batch_store
    queue = store.subscribe()

    async def event_generator():
        # Immediate handshake — confirms to the browser that the stream is live
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        try:
            while True:
                try:
                    batch = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(batch)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive comment — prevents nginx / browser from closing idle stream
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass  # clean shutdown
        except Exception as e:
            logger.debug("SSE stream ended: %s", e)
        finally:
            store.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/api/lookup",
    summary="Look up a transaction by ID",
    responses={
        200: {"description": "Transaction found — full record with IRN, QR, status"},
        404: {"description": "No record found for this transaction ID"},
        401: {"description": "Invalid API key"},
    },
)
async def lookup_transaction(
    request: Request,
    transaction_id: str = Query(..., description="Transaction ID to look up"),
    x_api_key: str = Header(..., description="API key"),
):
    """
    Look up a single transaction by its transaction_id.

    Searches the calling tenant's batch results (newest first) and returns
    the first match with its processing result, IRN, QR code, and batch metadata.

    Auth: API key only (no HMAC — GET has no body to sign).
    Scoped: results limited to the tenant identified by the API key.
    """
    tenant_registry = request.app.state.tenant_registry
    tenant_cfg = tenant_registry.get(x_api_key)
    if tenant_cfg is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    store: BatchStore = request.app.state.batch_store
    result = await store.lookup(transaction_id, tenant_id=tenant_cfg.tenant_id)

    if result is None:
        return JSONResponse(
            status_code=404,
            content={
                "status": "not_found",
                "transaction_id": transaction_id,
                "message": "No record found for this transaction ID",
            },
        )

    return JSONResponse(content=result)
