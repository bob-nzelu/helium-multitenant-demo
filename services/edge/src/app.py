"""
Edge Service — Stub (FIRS Submission Proxy)

Accepts finalized invoices from Core, returns mock FIRS acceptance.
Real implementation will submit to FIRS API and handle retries.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("edge.stub")

app = FastAPI(title="Helium Edge (Stub)", version="0.1.0-stub")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "edge", "version": "0.1.0-stub", "mode": "stub"}


@app.post("/api/v1/submit")
async def submit(request: Request):
    """Accept finalized invoices from Core. Always returns STUB_ACCEPTED."""
    body = await request.json()
    batch_id = body.get("batch_id", f"edge-{uuid.uuid4().hex[:8]}")
    invoice_count = len(body.get("invoices", []))

    logger.info(f"FIRS SUBMIT (stub) | batch_id={batch_id} invoices={invoice_count}")

    confirmations = []
    for inv in body.get("invoices", []):
        conf_id = f"FIRS-STUB-{uuid.uuid4().hex[:10].upper()}"
        confirmations.append({
            "irn": inv.get("irn", ""),
            "firs_confirmation": conf_id,
            "status": "STUB_ACCEPTED",
            "transmitted_at": datetime.now(timezone.utc).isoformat(),
        })

    return {
        "batch_id": batch_id,
        "status": "completed",
        "total": invoice_count,
        "accepted": invoice_count,
        "rejected": 0,
        "confirmations": confirmations,
    }


@app.get("/api/v1/status/{batch_id}")
async def batch_status(batch_id: str):
    """Return completed status for any batch."""
    return {
        "batch_id": batch_id,
        "status": "completed",
        "transmitted_at": datetime.now(timezone.utc).isoformat(),
    }
