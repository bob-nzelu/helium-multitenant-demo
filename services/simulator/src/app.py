"""
Helium Simulator Service

Generates realistic demo invoice data and sends it through the real
Helium pipeline via Relay API. See SIMULATOR_CONTRACT.md for full spec.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .bad_calls import BadCallGenerator
from .catalog import CatalogManager
from .generators import InboundGenerator, OutboundGenerator
from .hmac_signer import HMACSigner
from .stream_manager import StreamManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

RELAY_URL = os.getenv("SIMULATOR_RELAY_URL", "http://localhost:8082")
CORE_URL = os.getenv("SIMULATOR_CORE_URL", "http://localhost:8080")
DATA_DIR = os.getenv("SIMULATOR_DATA_DIR", "./data")
CONFIG_DIR = os.getenv("SIMULATOR_CONFIG_DIR", "./config")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init catalog
    catalog = CatalogManager(DATA_DIR, CONFIG_DIR)
    app.state.catalog = catalog

    # Init generators
    app.state.outbound = OutboundGenerator(catalog)
    app.state.inbound = InboundGenerator(catalog)

    # Init signer
    signer = HMACSigner(catalog)
    app.state.signer = signer

    # Init stream manager
    app.state.streams = StreamManager(
        app.state.outbound, app.state.inbound, signer, RELAY_URL,
    )

    # Init bad call generator
    app.state.bad_calls = BadCallGenerator(
        catalog, app.state.outbound, signer, RELAY_URL,
    )

    logger.info(f"Simulator ready — relay={RELAY_URL}, data={DATA_DIR}")
    yield

    # Shutdown: stop all streams
    await app.state.streams.stop_all()


app = FastAPI(title="Helium Simulator", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "simulator",
        "version": "0.1.0",
        "relay_url": RELAY_URL,
        "active_streams": app.state.streams.active_stream_ids,
    }


# ── 1. Single outbound call ───────────────────────────────────────────────


@app.post("/api/single")
async def send_single(request: Request):
    """Generate and send one outbound invoice to Relay."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")

    invoice = request.app.state.outbound.generate(tenant_id)

    try:
        relay_resp = await request.app.state.signer.send_to_relay(
            RELAY_URL, tenant_id, invoice,
        )
    except Exception as e:
        logger.error(f"Relay call failed: {e}")
        return JSONResponse(status_code=502, content={
            "status": "relay_error",
            "error": str(e),
            "generated_invoice": {
                "invoice_number": invoice["invoice_number"],
                "buyer": invoice["buyer_business_name"],
                "total": invoice["total_amount"],
            },
        })

    return {
        "status": "sent",
        "relay_response": relay_resp,
        "generated_invoice": {
            "invoice_number": invoice["invoice_number"],
            "buyer": invoice["buyer_business_name"],
            "fees": [li["description"] for li in invoice["line_items"]],
            "subtotal": invoice["tax_exclusive_amount"],
            "vat": invoice["total_tax_amount"],
            "total": invoice["total_amount"],
        },
    }


# ── 2. Burst (multiple at once) ───────────────────────────────────────────


@app.post("/api/burst")
async def send_burst(request: Request):
    """Generate and send N outbound invoices sequentially (1/sec) to Relay."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")
    count = body.get("count", 10)

    if count < 1 or count > 100:
        raise HTTPException(400, "count must be 1-100")

    results = []
    for i in range(count):
        invoice = request.app.state.outbound.generate(tenant_id)
        try:
            relay_resp = await request.app.state.signer.send_to_relay(
                RELAY_URL, tenant_id, invoice,
            )
            results.append({
                "invoice_number": invoice["invoice_number"],
                "buyer": invoice["buyer_business_name"],
                "total": invoice["total_amount"],
                "relay_status_code": relay_resp["status_code"],
            })
        except Exception as e:
            logger.error(f"Burst invoice {i+1}/{count} failed: {e}")
            results.append({
                "invoice_number": invoice["invoice_number"],
                "buyer": invoice["buyer_business_name"],
                "total": invoice["total_amount"],
                "relay_status_code": 0,
                "error": str(e),
            })

        # 1-second spacing between calls
        if i < count - 1:
            await asyncio.sleep(1)

    sent = sum(1 for r in results if r["relay_status_code"] in (200, 201))
    failed = count - sent

    return {
        "status": "completed",
        "tenant_id": tenant_id,
        "total": count,
        "sent": sent,
        "failed": failed,
        "results": results,
    }


# ── 3. Continuous stream ──────────────────────────────────────────────────


@app.post("/api/stream/start")
async def stream_start(request: Request):
    """Start continuous simulation at configurable cadence."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")
    cadence = body.get("cadence", "1m")
    include_inbound = body.get("include_inbound", True)
    inbound_ratio = body.get("inbound_ratio", 0.2)

    if cadence not in ("1m", "5m", "30m"):
        raise HTTPException(400, "cadence must be '1m', '5m', or '30m'")

    state = request.app.state.streams.start(
        tenant_id, cadence, include_inbound, inbound_ratio,
    )

    return {
        "status": "started",
        "stream_id": state.stream_id,
        "cadence": cadence,
        "include_inbound": include_inbound,
        "inbound_ratio": inbound_ratio,
    }


@app.post("/api/stream/stop")
async def stream_stop(request: Request):
    """Stop a running simulation stream."""
    body = await request.json()
    stream_id = body.get("stream_id", "")

    if not stream_id:
        raise HTTPException(400, "stream_id required")

    stopped = request.app.state.streams.stop(stream_id)
    if not stopped:
        raise HTTPException(404, f"Stream '{stream_id}' not found or already stopped")

    return {"status": "stopped", "stream_id": stream_id}


@app.get("/api/stream/status")
async def stream_status(stream_id: str = ""):
    """Get status of a simulation stream."""
    if not stream_id:
        raise HTTPException(400, "stream_id query param required")

    status = app.state.streams.get_status(stream_id)
    if not status:
        raise HTTPException(404, f"Stream '{stream_id}' not found")

    return status


# ── 4. Single inbound call ────────────────────────────────────────────────


@app.post("/api/inbound")
async def send_inbound(request: Request):
    """Generate and send one inbound invoice (supplier -> Abbey) to Relay."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")
    supplier_index = body.get("supplier_index")

    invoice = request.app.state.inbound.generate(tenant_id, supplier_index)

    try:
        relay_resp = await request.app.state.signer.send_to_relay(
            RELAY_URL, tenant_id, invoice,
        )
    except Exception as e:
        logger.error(f"Relay call failed: {e}")
        return JSONResponse(status_code=502, content={
            "status": "relay_error",
            "error": str(e),
            "generated_invoice": {
                "invoice_number": invoice["invoice_number"],
                "supplier": invoice["seller_business_name"],
                "total": invoice["total_amount"],
            },
        })

    return {
        "status": "sent",
        "relay_response": relay_resp,
        "generated_invoice": {
            "invoice_number": invoice["invoice_number"],
            "supplier": invoice["seller_business_name"],
            "description": invoice["line_items"][0]["description"],
            "subtotal": invoice["tax_exclusive_amount"],
            "vat": invoice["total_tax_amount"],
            "total": invoice["total_amount"],
        },
    }


# ── 5. Bad calls (error testing) ──────────────────────────────────────────


@app.post("/api/bad")
async def send_bad_call(request: Request):
    """Send intentionally bad calls to test error handling."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")
    error_type = body.get("error_type", "auth_failure")

    try:
        result = await request.app.state.bad_calls.generate(tenant_id, error_type)
        return result
    except Exception as e:
        logger.error(f"Bad call '{error_type}' failed: {e}")
        return JSONResponse(status_code=502, content={
            "error_type": error_type,
            "error": str(e),
        })


# ── 6. Attack simulation (future) ─────────────────────────────────────────


@app.post("/api/attack")
async def simulate_attack(request: Request):
    """Future: Simulate nefarious calls for security testing."""
    raise HTTPException(
        status_code=501,
        detail="Reserved for future implementation — see SIMULATOR_CONTRACT.md §6",
    )
