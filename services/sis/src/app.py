"""
SIS Service — Stub (Submission Intelligence Service)

Anomaly detection, submission pattern analytics, compliance scoring.
Real implementation will analyse invoice patterns, detect fraud,
and block suspicious submissions.
"""

import logging
import random
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("sis.stub")

app = FastAPI(title="Helium SIS (Stub)", version="0.1.0-stub")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "sis", "version": "0.1.0-stub", "mode": "stub"}


@app.post("/api/v1/analyse")
async def analyse_submission(request: Request):
    """Analyse an invoice batch for anomalies. Stub always returns clean."""
    body = await request.json()
    invoice_count = body.get("invoice_count", 0)
    tenant_id = body.get("tenant_id", "unknown")

    logger.info(f"ANALYSE (stub) | tenant={tenant_id} invoices={invoice_count}")

    return {
        "tenant_id": tenant_id,
        "verdict": "CLEAN",
        "risk_score": round(random.uniform(0.01, 0.15), 3),
        "anomalies_detected": 0,
        "checks_run": 12,
        "checks_passed": 12,
        "analysed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/compliance/score")
async def compliance_score(request: Request):
    """Compute compliance score for a tenant. Stub returns plausible score."""
    body = await request.json()
    tenant_id = body.get("tenant_id", "unknown")

    return {
        "tenant_id": tenant_id,
        "compliance_score": random.randint(78, 98),
        "components": {
            "tin_valid": random.randint(16, 20),
            "address_complete": random.randint(14, 20),
            "mbs_registered": random.randint(15, 20),
            "invoice_activity": random.randint(16, 20),
            "rejection_rate": random.randint(17, 20),
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/pattern/check")
async def pattern_check(request: Request):
    """Check for suspicious patterns. Stub always returns no flags."""
    body = await request.json()

    return {
        "flags": [],
        "pattern_score": 0.0,
        "message": "No suspicious patterns detected (stub)",
    }
