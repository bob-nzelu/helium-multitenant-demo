"""
Helium Simulator Service — Shell

See SIMULATOR_CONTRACT.md for full API specification and boundaries.
This file contains the endpoint stubs. Implementation is done in a dedicated session.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# from .catalog import CatalogManager
# from .generators import OutboundGenerator, InboundGenerator
# from .hmac_signer import HMACSigner
# from .stream_manager import StreamManager
# from .bad_calls import BadCallGenerator


@asynccontextmanager
async def lifespan(app: FastAPI):
    # TODO: Load catalogs, init signer, init stream manager
    # app.state.catalog = CatalogManager(data_dir, config_dir)
    # app.state.signer = HMACSigner(app.state.catalog)
    # app.state.streams = StreamManager()
    yield
    # TODO: Stop all active streams


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
        "active_streams": [],  # TODO: from StreamManager
    }


# ── 1. Single outbound call ─────────────────────────────────────────────────

@app.post("/api/single")
async def send_single(request: Request):
    """Generate and send one outbound invoice to Relay."""
    # body = await request.json()
    # tenant_id = body.get("tenant_id", "abbey")
    # TODO: implement per SIMULATOR_CONTRACT.md §1
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §1")


# ── 2. Burst (multiple at once) ─────────────────────────────────────────────

@app.post("/api/burst")
async def send_burst(request: Request):
    """Generate and send N outbound invoices as a batch to Relay."""
    # body = await request.json()
    # tenant_id = body.get("tenant_id", "abbey")
    # count = body.get("count", 10)
    # TODO: implement per SIMULATOR_CONTRACT.md §2
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §2")


# ── 3. Continuous stream ��────────────────────────────────────────────────────

@app.post("/api/stream/start")
async def stream_start(request: Request):
    """Start continuous simulation at configurable cadence."""
    # body = await request.json()
    # cadence = body.get("cadence", "1m")
    # include_inbound = body.get("include_inbound", True)
    # TODO: implement per SIMULATOR_CONTRACT.md §3
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §3")


@app.post("/api/stream/stop")
async def stream_stop(request: Request):
    """Stop a running simulation stream."""
    # body = await request.json()
    # stream_id = body.get("stream_id")
    # TODO: implement per SIMULATOR_CONTRACT.md §3
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §3")


@app.get("/api/stream/status")
async def stream_status(stream_id: str = ""):
    """Get status of a simulation stream."""
    # TODO: implement per SIMULATOR_CONTRACT.md §3
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §3")


# ── 4. Single inbound call ───────��────────────────────────────��─────────────

@app.post("/api/inbound")
async def send_inbound(request: Request):
    """Generate and send one inbound invoice (supplier → Abbey) to Core."""
    # body = await request.json()
    # supplier_index = body.get("supplier_index")
    # TODO: implement per SIMULATOR_CONTRACT.md §4
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §4")


# ── 5. Bad calls (error testing) ────────────────────────────────────────────

@app.post("/api/bad")
async def send_bad_call(request: Request):
    """Send intentionally bad calls to test error handling."""
    # body = await request.json()
    # error_type = body.get("error_type", "auth_failure")
    # TODO: implement per SIMULATOR_CONTRACT.md §5
    raise HTTPException(status_code=501, detail="Not implemented — see SIMULATOR_CONTRACT.md §5")


# ── 6. Attack simulation (future) ─────��─────────────────────────────────────

@app.post("/api/attack")
async def simulate_attack(request: Request):
    """Future: Simulate nefarious calls for security testing."""
    raise HTTPException(status_code=501, detail="Reserved for future implementation — see SIMULATOR_CONTRACT.md §6")
