"""
Helium Core Service — Demo Build (Mock Processing + Real SSE)

Provides transformation pipeline for the Abbey Mortgage demo:
  - Receives files from Relay (via queue/direct call)
  - Mock-processes with realistic stdout logs
  - Writes invoices to canonical PostgreSQL tables
  - Publishes invoice.created events through HeartBeat SSE
  - Serves sync/full endpoint for Float bulk-load

Endpoints:
  POST /api/v1/process     — Process queued file (Relay → Core)
  POST /api/v1/ingest      — Direct inbound invoice (Simulator → Core)
  POST /api/v1/finalize    — Finalize preview (Float → Core)
  GET  /api/sync/full      — Bulk-load invoices for Float sync
  GET  /api/v1/invoices    — Paginated invoice list
  GET  /api/v1/invoice/:id — Single invoice
  GET  /health
"""

import asyncio
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("helium.core")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CORE] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def _uuid7_hex() -> str:
    return uuid.uuid4().hex[:16].upper()


def _generate_irn(invoice_number: str, service_id: str, issue_date: str) -> str:
    """IRN per IQC spec: {invoice_number}-{service_id}-{YYYYMMDD}"""
    date_part = issue_date.replace("-", "") if issue_date else datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{invoice_number}-{service_id}-{date_part}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ.get("CORE_DATABASE_URL", "postgresql://helium:helium_demo_2026@localhost:5432/helium")
    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
    app.state.pool = pool
    app.state.heartbeat_url = os.environ.get("CORE_HEARTBEAT_URL", "http://heartbeat:9000")
    app.state.internal_token = os.environ.get("CORE_INTERNAL_TOKEN", "dev-token-123")
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    logger.info("Core service started — DB pool ready")
    yield
    await app.state.http_client.aclose()
    await pool.close()


app = FastAPI(title="Helium Core (Demo)", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def _publish_event(app_state, event_type: str, data: dict, company_id: str = "abbey"):
    """Push an event through HeartBeat's SSE pipeline."""
    try:
        resp = await app_state.http_client.post(
            f"{app_state.heartbeat_url}/api/internal/events/publish",
            json={
                "event_type": event_type,
                "data": data,
                "company_id": company_id,
            },
            headers={"Authorization": f"Bearer {app_state.internal_token}"},
        )
        if resp.status_code == 200:
            logger.info(f"  SSE → {event_type} published (seq={resp.json().get('sequence', '?')})")
        else:
            logger.warning(f"  SSE → {event_type} failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"  SSE → {event_type} error: {e}")


async def _update_blob_status(app_state, blob_uuid: str, status: str, stats: dict = None):
    """Update blob status in HeartBeat (triggers blob.status_changed SSE)."""
    try:
        payload = {"status": status}
        if stats:
            payload["processing_stats"] = stats
        resp = await app_state.http_client.post(
            f"{app_state.heartbeat_url}/api/v1/heartbeat/blob/{blob_uuid}/status",
            json=payload,
        )
        logger.info(f"  Blob {blob_uuid[:8]}... → {status}")
    except Exception as e:
        logger.warning(f"  Blob status update failed: {e}")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "core", "version": "0.1.0"}


# ── POST /api/v1/process — Receive file from Relay queue ────────────────────

@app.post("/api/v1/process")
async def process_file(request: Request):
    """
    Process a queued file from Relay.

    Mock processing flow with realistic stdout logging:
    1. Receive blob reference
    2. Log: "Extracting invoice data..."
    3. Wait 1-2s (simulating OCR/parsing)
    4. Log: "Transforming to FIRS schema..."
    5. Wait 1s
    6. Generate mock invoice data
    7. Write to invoices table
    8. Update blob status in HeartBeat (processing → preview_pending)
    9. Publish invoice.created via HeartBeat SSE
    10. Return preview data
    """
    body = await request.json()
    blob_uuid = body.get("blob_uuid") or body.get("data_uuid") or _uuid7_hex()
    tenant_id = body.get("tenant_id", "abbey")
    filenames = body.get("filenames", ["unknown.json"])
    trace_id = body.get("trace_id", _uuid7_hex())

    logger.info(f"{'='*60}")
    logger.info(f"PROCESSING REQUEST — trace={trace_id[:12]}...")
    logger.info(f"  Blob: {blob_uuid[:12]}...")
    logger.info(f"  Files: {filenames}")
    logger.info(f"  Tenant: {tenant_id}")
    logger.info(f"{'='*60}")

    # Step 1: Update blob status → processing
    await _update_blob_status(request.app.state, blob_uuid, "processing")

    # Step 2: Simulate extraction
    logger.info(f"  [1/4] Extracting invoice data from {filenames[0]}...")
    await asyncio.sleep(1.5)
    logger.info(f"  [1/4] Extraction complete — 1 invoice found")

    # Step 3: Simulate transformation
    logger.info(f"  [2/4] Transforming to FIRS schema...")
    await asyncio.sleep(1.0)
    logger.info(f"  [2/4] Transformation complete")

    # Step 4: Generate invoice
    logger.info(f"  [3/4] Generating IRN and writing to database...")

    invoice_id = _uuid7_hex()
    invoice_number = f"ABB-{int(datetime.now(timezone.utc).timestamp()) % 10000000:07d}"
    service_id = "A8BM72KQ"  # Abbey's FIRS service_id
    issue_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    irn = _generate_irn(invoice_number, service_id, issue_date)
    helium_no = f"PRO-ABBEY-{invoice_id}"

    # Mock extracted data (in real Core, this comes from Transforma extraction)
    subtotal = 250000.00
    tax_amount = 18750.00
    total_amount = 268750.00

    pool: asyncpg.Pool = request.app.state.pool
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO invoices (
                    tenant_id, invoice_id, helium_invoice_no, invoice_number, irn,
                    csid, csid_status, direction, document_type, firs_invoice_type_code,
                    transaction_type, issue_date, subtotal, tax_amount, total_amount,
                    payment_means, workflow_status, transmission_status, payment_status,
                    company_id, seller_name, seller_tin, seller_address, seller_city,
                    buyer_name, buyer_tin,
                    product_summary, line_items_count, source, source_id,
                    schema_version_applied, sign_date,
                    invoice_trace_id, blob_uuid
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                    $20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34
                )
            """,
                tenant_id, invoice_id, helium_no, invoice_number, irn,
                irn[:32].upper(), 'ISSUED', 'OUTBOUND', 'COMMERCIAL_INVOICE', '380',
                'B2C', issue_date, subtotal, tax_amount, total_amount,
                'BANK_TRANSFER', 'COMMITTED', 'NOT_REQUIRED', 'UNPAID',
                tenant_id, 'Abbey Mortgage Bank PLC', '02345678-0001',
                '3 Abiola Segun Akinola Crescent, off Obafemi Awolowo Way', 'Ikeja',
                'Demo Customer', '00100001-0001',
                'Mortgage Origination Fee', 1, 'bulk_upload', trace_id,
                '2.1.3.0', issue_date,
                trace_id, blob_uuid
            )

        logger.info(f"  [3/4] Invoice {invoice_number} written (IRN: {irn})")
    except Exception as e:
        logger.error(f"  [3/4] DB write failed: {e}")
        raise HTTPException(status_code=500, detail=f"DB write failed: {e}")

    # Step 5: Update blob status → preview_pending
    await _update_blob_status(request.app.state, blob_uuid, "preview_pending", {
        "extracted_invoice_count": 1,
        "rejected_invoice_count": 0,
    })

    # Step 6: Publish invoice.created SSE event
    logger.info(f"  [4/4] Publishing invoice.created event...")

    invoice_event_data = {
        "invoice_id": invoice_id,
        "helium_invoice_no": helium_no,
        "invoice_number": invoice_number,
        "irn": irn,
        "direction": "OUTBOUND",
        "issue_date": issue_date,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "seller_name": "Abbey Mortgage Bank PLC",
        "buyer_name": "Demo Customer",
        "workflow_status": "COMMITTED",
        "payment_status": "UNPAID",
        "product_summary": "Mortgage Origination Fee",
        "line_items_count": 1,
        "source": "bulk_upload",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await _publish_event(request.app.state, "invoice.created", invoice_event_data, tenant_id)

    logger.info(f"  DONE — Invoice {invoice_number} processed successfully")
    logger.info(f"{'='*60}")

    return {
        "status": "processed",
        "queue_id": trace_id,
        "invoice_id": invoice_id,
        "helium_invoice_no": helium_no,
        "invoice_number": invoice_number,
        "irn": irn,
        "preview_data": {
            "invoice_count": 1,
            "total_amount": total_amount,
            "currency": "NGN",
            "items": [{
                "invoice_number": invoice_number,
                "irn": irn,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_amount": total_amount,
                "description": "Mortgage Origination Fee",
            }],
        },
    }


# ── POST /api/v1/ingest — Direct inbound invoice (Simulator → Core) ────────

@app.post("/api/v1/ingest")
async def ingest_invoice(request: Request):
    """
    Receive and store an invoice (inbound or outbound).
    Called by Simulator for inbound (FIRS delivery) or direct API.
    """
    body = await request.json()
    tenant_id = body.get("tenant_id", "abbey")
    direction = body.get("direction", "OUTBOUND")
    source = body.get("source", "Core API")
    source_id = body.get("source_id", "core-direct")

    # Extract from UBL or flat format
    supplier = body.get("accountingSupplierParty") or body.get("accounting_supplier_party", {})
    customer = body.get("accountingCustomerParty") or body.get("accounting_customer_party", {})

    invoice_number = body.get("invoiceTypeCode") or body.get("invoice_type_code") or f"CORE-{_uuid7_hex()[:8]}"
    issue_date = body.get("issueDate") or body.get("issue_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    service_id = "A8BM72KQ"
    invoice_id = _uuid7_hex()
    irn = _generate_irn(invoice_number, service_id, issue_date)
    helium_no = f"PRO-ABBEY-{invoice_id}"

    # Compute totals from line items
    lines = body.get("invoiceLine") or body.get("invoice_line", [])
    subtotal = 0.0
    for line in lines:
        price_info = line.get("price", {})
        price = float(price_info.get("priceAmount") or price_info.get("price_amount", 0))
        qty = float(line.get("invoicedQuantity") or line.get("invoiced_quantity", 1))
        subtotal += price * qty

    tax_totals = body.get("taxTotal") or body.get("tax_total", [])
    tax_amount = sum(float(t.get("taxAmount") or t.get("tax_amount", 0)) for t in tax_totals)
    if tax_amount == 0 and subtotal > 0:
        tax_amount = round(subtotal * 0.075, 2)
    total_amount = round(subtotal + tax_amount, 2)

    seller_name = supplier.get("partyName") or supplier.get("party_name", "")
    seller_tin = supplier.get("tin", "")
    buyer_name = customer.get("partyName") or customer.get("party_name", "")

    line_names = [l.get("item", {}).get("name", "Item") for l in lines]
    product_summary = line_names[0] if len(line_names) == 1 else f"{line_names[0]} (+{len(line_names)-1} more)" if line_names else ""

    logger.info(f"INGEST — {direction} invoice {invoice_number} from {seller_name or 'unknown'}")

    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO invoices (
                tenant_id, invoice_id, helium_invoice_no, invoice_number, irn,
                csid, csid_status, direction, document_type, firs_invoice_type_code,
                transaction_type, issue_date, subtotal, tax_amount, total_amount,
                payment_means, workflow_status, payment_status,
                company_id, seller_name, seller_tin, buyer_name,
                product_summary, line_items_count, source, source_id,
                schema_version_applied
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                $19,$20,$21,$22,$23,$24,$25,$26,$27
            )
        """,
            tenant_id, invoice_id, helium_no, invoice_number, irn,
            irn[:32].upper(), 'ISSUED', direction, 'COMMERCIAL_INVOICE', '380',
            'B2B' if direction == 'INBOUND' else 'B2C',
            issue_date, round(subtotal, 2), round(tax_amount, 2), total_amount,
            'BANK_TRANSFER', 'COMMITTED', 'UNPAID',
            tenant_id, seller_name, seller_tin, buyer_name,
            product_summary, len(lines), source, source_id,
            '2.1.3.0'
        )

    # Publish SSE event
    await _publish_event(request.app.state, "invoice.created", {
        "invoice_id": invoice_id,
        "helium_invoice_no": helium_no,
        "invoice_number": invoice_number,
        "irn": irn,
        "direction": direction,
        "issue_date": issue_date,
        "subtotal": round(subtotal, 2),
        "tax_amount": round(tax_amount, 2),
        "total_amount": total_amount,
        "seller_name": seller_name,
        "buyer_name": buyer_name,
        "workflow_status": "COMMITTED",
        "payment_status": "UNPAID",
        "product_summary": product_summary,
        "line_items_count": len(lines),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, tenant_id)

    logger.info(f"  → Invoice {invoice_number} stored (IRN: {irn}, direction: {direction})")

    return JSONResponse(status_code=201, content={
        "status": "created",
        "invoice_id": invoice_id,
        "helium_invoice_no": helium_no,
        "irn": irn,
        "direction": direction,
        "total_amount": total_amount,
    })


# ── GET /api/sync/full — Bulk-load for Float SyncClient ─────────────────────

@app.get("/api/sync/full")
async def sync_full(
    request: Request,
    tenant_id: str = Query("abbey"),
    page: int = Query(1, ge=1),
    per_page: int = Query(500, ge=1, le=500),
):
    """
    Return all invoices for a tenant — used by Float's full_sync().
    Each invoice is delivered as an invoice.created event structure.
    """
    pool: asyncpg.Pool = request.app.state.pool
    offset = (page - 1) * per_page

    count_row = await pool.fetchrow(
        "SELECT COUNT(*) as total FROM invoices WHERE tenant_id = $1 AND deleted_at IS NULL", tenant_id
    )
    total = count_row["total"]

    rows = await pool.fetch("""
        SELECT invoice_id, helium_invoice_no, invoice_number, irn,
               direction, document_type, transaction_type,
               issue_date, subtotal, tax_amount, total_amount,
               workflow_status, transmission_status, payment_status,
               seller_name, seller_tin, buyer_name, buyer_tin,
               product_summary, line_items_count, source, created_at
        FROM invoices
        WHERE tenant_id = $1 AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
    """, tenant_id, per_page, offset)

    items = []
    for row in rows:
        item = dict(row)
        for k, v in item.items():
            if hasattr(v, 'isoformat'):
                item[k] = v.isoformat()
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total > 0 else 0,
        "has_more": (page * per_page) < total,
    }


# ── GET /api/v1/invoices — Paginated list ───────────────────────────────────

@app.get("/api/v1/invoices")
async def list_invoices(
    request: Request,
    tenant_id: str = Query("abbey"),
    direction: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    pool: asyncpg.Pool = request.app.state.pool
    where = ["tenant_id = $1", "deleted_at IS NULL"]
    params = [tenant_id]
    idx = 2

    if direction:
        where.append(f"direction = ${idx}")
        params.append(direction)
        idx += 1
    if status:
        where.append(f"workflow_status = ${idx}")
        params.append(status)
        idx += 1

    where_clause = " AND ".join(where)
    offset = (page - 1) * per_page

    total = (await pool.fetchrow(f"SELECT COUNT(*) as c FROM invoices WHERE {where_clause}", *params))["c"]
    rows = await pool.fetch(f"""
        SELECT * FROM invoices WHERE {where_clause}
        ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx+1}
    """, *params, per_page, offset)

    items = []
    for row in rows:
        item = dict(row)
        for k, v in item.items():
            if hasattr(v, 'isoformat'):
                item[k] = v.isoformat()
        items.append(item)

    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ── GET /api/v1/invoice/:id ─────────────────────────────────────────────────

@app.get("/api/v1/invoice/{invoice_id}")
async def get_invoice(invoice_id: str, request: Request):
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow("SELECT * FROM invoices WHERE invoice_id = $1", invoice_id)
    if not row:
        raise HTTPException(status_code=404, detail="Invoice not found")
    result = dict(row)
    for k, v in result.items():
        if hasattr(v, 'isoformat'):
            result[k] = v.isoformat()
    return result
