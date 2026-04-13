"""
HIS Service — Stub (Helium Intelligence Service)

Returns plausible classification data for Core's enrichment phase.
Real implementation will use ML models for HS code, service code,
and address classification.
"""

import logging
import random

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("his.stub")

app = FastAPI(title="Helium Intelligence Service (Stub)", version="0.1.0-stub")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Plausible HS codes for financial services
HS_CODES = ["9971.11", "9971.12", "9971.19", "9972.11", "9972.12", "6601.10"]
SERVICE_CODES = ["S-FIN-001", "S-FIN-002", "S-FIN-003", "S-INS-001", "S-INS-002"]
CATEGORIES = ["Financial Services", "Insurance", "Legal Services", "IT Services", "Healthcare"]


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "his", "version": "0.1.0-stub", "mode": "stub"}


@app.post("/api/v1/classify/hsn")
async def classify_hsn(request: Request):
    body = await request.json()
    desc = body.get("description", "")
    logger.info(f"HSN classify (stub): {desc[:50]}")
    return {
        "description": desc,
        "hsn_code": random.choice(HS_CODES),
        "confidence": round(random.uniform(0.75, 0.98), 2),
        "source": "his-stub",
    }


@app.post("/api/v1/classify/category")
async def classify_category(request: Request):
    body = await request.json()
    desc = body.get("description", "")
    return {
        "description": desc,
        "category": random.choice(CATEGORIES),
        "confidence": round(random.uniform(0.80, 0.95), 2),
        "source": "his-stub",
    }


@app.post("/api/v1/classify/service")
async def classify_service(request: Request):
    body = await request.json()
    desc = body.get("description", "")
    return {
        "description": desc,
        "service_code": random.choice(SERVICE_CODES),
        "confidence": round(random.uniform(0.70, 0.95), 2),
        "source": "his-stub",
    }


@app.post("/api/v1/validate/address")
async def validate_address(request: Request):
    body = await request.json()
    raw = body.get("address", "")
    return {
        "raw_input": raw,
        "resolved_address": raw,
        "resolved_city": body.get("city", "Lagos"),
        "resolved_state": body.get("state", "Lagos"),
        "resolved_lga": "Ikeja",
        "resolved_lga_code": "25001",
        "resolved_state_code": "25",
        "confidence": 0.85,
        "source": "his-stub",
    }


@app.post("/api/v1/feedback")
async def feedback(request: Request):
    body = await request.json()
    logger.info(f"HIS feedback received (stub): {len(body.get('corrections', []))} corrections")
    return {"status": "accepted", "corrections_received": len(body.get("corrections", []))}
