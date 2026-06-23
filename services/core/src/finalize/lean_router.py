"""
Lean Finalize Router — Phase 1 reference-based finalize.

Implements the PINNED submit-chain contract:

    POST /api/v1/finalize  {ref, trace_id, ...}  -> {status, irn, qr, ...}

This is the SHORTEST path to Edge ``STUB_ACCEPTED`` for a doc that was
already ingested via Relay ``POST /api/ingest`` (#3 finalize — NO bytes).
Relay's ``CoreClient.finalize_by_reference(ref, trace_id)`` calls this route.

Flow (per the contract):
    ref/trace_id in
      -> IRN  (src.finalize.irn_generator.generate_irn)
      -> QR   (src.finalize.qr_generator.generate_qr_code)
      -> EdgeClient.submit_batch  -> Edge STUB_ACCEPTED
      -> persist HLX result to HeartBeat blob  (doc PERSISTS)
      -> emit lifecycle SSE Scout reduces:
            relay.finalize.accepted  (echoes trace_id — but Relay owns this;
                                       Core emits the Core-side terminal here)
            core.artifact.hlx_available  (backend-originated, no trace_id)
            core.submission.terminal     (CONFIRMATION — echoes trace_id, carries irn)
      -> return {status, irn, qr, ...}

The heavier ``finalize.router.finalize_routes`` (submitted_rows + preview
diff + DB commit) is the bulk/Float flow and is NOT what Relay's
reference-based trigger drives; this lean handler is the Phase-1 path.

Event names + payload shapes are grounded in the canonical Scout-reduced
lifecycle (assembly Core ``src/sse/lifecycle.py`` — ``core.submission.terminal``
carries ``document_id``/``invoice_id``/``trace_id``/``lifecycle_status``/``irn``;
``core.artifact.hlx_available`` carries ``document_id``/``hlx_blob_ref``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.database.pool import get_connection
from src.finalize.edge_client import EdgeClient, EdgeSubmission
from src.finalize.invoice_creator import (
    create_finalize_invoice,
    find_by_ref,
    mark_transmitted,
)
from src.finalize.irn_generator import generate_irn, IRNError
from src.finalize.qr_generator import QRInput, generate_qr_code, QRError
from src.sse.models import SSEEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Finalize"])

# Canonical Scout-reduced lifecycle event_type constants (assembly Core
# src/sse/events.py). The deployed legacy Core's sse/events.py predates the
# lifecycle families, so we pin the wire names here.
EVENT_CORE_SUBMISSION_TERMINAL = "core.submission.terminal"
EVENT_CORE_ARTIFACT_HLX_AVAILABLE = "core.artifact.hlx_available"
EVENT_RELAY_FINALIZE_ACCEPTED = "relay.finalize.accepted"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _safe_qr(qr_input: QRInput) -> tuple[str, bool]:
    """Generate a QR. On any failure, fall back to the raw JSON payload string
    so the chain NEVER breaks on a missing qrcode/Pillow dep.

    Returns (qr_value, is_png_base64).
    """
    try:
        return generate_qr_code(qr_input), True
    except QRError as exc:
        logger.warning("lean_finalize_qr_fallback: %s", exc)
        payload = json.dumps(
            {
                "irn": qr_input.irn,
                "invoice_number": qr_input.invoice_number,
                "total_amount": str(qr_input.total_amount),
                "issue_date": qr_input.issue_date,
                "seller_tin": qr_input.seller_tin,
            },
            separators=(",", ":"),
        )
        return payload, False


async def _emit(sse_manager, event: SSEEvent) -> None:
    """Publish a lifecycle SSE event; never let an SSE failure break finalize."""
    if sse_manager is None:
        return
    try:
        await sse_manager.publish(event)
    except Exception:  # pragma: no cover - defensive
        logger.exception("lean_finalize_sse_publish_failed: %s", event.event_type)


async def _persist_hlx(heartbeat_client, blob_uuid: str, company_id: str | None,
                       result: dict[str, Any]) -> str | None:
    """Persist the finalize HLX result to HeartBeat blob so the doc PERSISTS.

    Returns the blob_uuid on success, None on (non-fatal) failure.
    """
    if heartbeat_client is None:
        return None
    try:
        data = json.dumps(result, separators=(",", ":")).encode("utf-8")
        await heartbeat_client.upload_blob(
            blob_uuid=blob_uuid,
            filename=f"{blob_uuid}.hlx.json",
            data=data,
            content_type="application/x-helium-exchange",
            company_id=company_id,
            metadata={"kind": "finalize_result", "irn": result.get("irn")},
        )
        return blob_uuid
    except Exception as exc:  # non-fatal — finalize already reached Edge
        logger.warning("lean_finalize_blob_persist_failed: %s", exc)
        return None


@router.post("/finalize")
async def finalize_by_reference(request: Request) -> JSONResponse:
    """POST /api/v1/finalize — reference-based finalize (Relay #3 trigger).

    Request body (the contract Relay's CoreClient sends):
        {
            "ref": "<file-sha256 / doc_ref / trace_id>",   // REQUIRED
            "trace_id": "<uuidv7>",                          // echoed on SSE
            // optional invoice fields — when Relay/Scout already extracted them:
            "company_id": "...",
            "service_id": "<8-char FIRS code>",
            "invoice_number": "...",
            "issue_date": "YYYY-MM-DD",
            "total_amount": 0,
            "tax_amount": 0,
            "currency_code": "NGN",
            "seller_tin": "...",
            "seller_name": "...",
            "buyer_tin": "...",
            "buyer_name": "...",
            "direction": "OUTBOUND",
            "line_items": [...]
        }

    Response:
        {
            "ref": "...",
            "trace_id": "...",
            "status": "submitted",          // terminal lifecycle status
            "irn": "<INVNO-SERVICEID-YYYYMMDD>",
            "qr": "<base64-png | json-fallback>",
            "qr_is_png": true,
            "firs_confirmation": "FIRS-STUB-...",
            "edge_status": "STUB_ACCEPTED",
            "document_id": "<blob_uuid>",
            "hlx_blob_ref": "<blob_uuid | null>"
        }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    ref = (body.get("ref") or "").strip()
    trace_id = (body.get("trace_id") or "").strip()
    if not ref:
        return JSONResponse({"error": "ref is required"}, status_code=400)

    # ── Resolve config-driven defaults ────────────────────────────────────
    config = getattr(request.app.state, "config", None)
    config_cache = getattr(request.app.state, "config_cache", None)

    company_id = body.get("company_id")
    service_id = body.get("service_id")

    # service_id (8-char FIRS code) from tenant config when not supplied
    if not service_id and config_cache is not None:
        try:
            service_id = (
                config_cache.get("firs_service_id")
                or config_cache.get("service_id")
            )
        except Exception:
            service_id = None
    if not service_id:
        # Last-resort deterministic 8-char code so IRN generation succeeds in
        # the demo. Real tenants supply service_id from HeartBeat config.
        service_id = "DEMO0001"
    service_id = str(service_id)[:8].rjust(8, "0")

    # ── Build a single invoice row (real fields if Relay supplied them) ────
    invoice_number = (body.get("invoice_number") or "").strip()
    if not invoice_number:
        # Derive a stable, alphanumeric invoice number from the ref.
        invoice_number = "".join(c for c in ref if c.isalnum())[:24] or "DOC"
    clean_number = "".join(c for c in invoice_number if c.isalnum()) or "DOC"

    issue_date = (body.get("issue_date") or "").strip()
    if not issue_date:
        issue_date = date.today().isoformat()

    total_amount = float(body.get("total_amount") or 0)
    tax_amount = float(body.get("tax_amount") or 0)
    seller_tin = body.get("seller_tin") or ""
    direction = body.get("direction") or "OUTBOUND"

    # document_id / blob_uuid: stable per-finalize identifier for SSE + persist
    document_id = body.get("document_id") or f"doc-{uuid.uuid4().hex[:16]}"

    # ── Step 1: IRN ───────────────────────────────────────────────────────
    try:
        irn = generate_irn(clean_number, service_id, issue_date)
    except IRNError as exc:
        return JSONResponse(
            {"error": f"IRN generation failed: {exc}", "ref": ref},
            status_code=422,
        )

    # ── Step 2: QR (non-fatal fallback) ───────────────────────────────────
    qr_value, qr_is_png = _safe_qr(
        QRInput(
            irn=irn,
            invoice_number=invoice_number,
            total_amount=total_amount,
            issue_date=issue_date,
            seller_tin=seller_tin,
        )
    )

    # ── Step 3: Edge submit (the goal — reach STUB_ACCEPTED) ──────────────
    edge_client: EdgeClient | None = getattr(request.app.state, "edge_client", None)
    firs_confirmation = None
    edge_status = "NOT_SUBMITTED"
    edge_errors: list[str] = []

    if edge_client is not None:
        submission = EdgeSubmission(
            invoice_id=document_id,
            irn=irn,
            invoice_number=invoice_number,
            issue_date=issue_date,
            direction=direction,
            transaction_type=body.get("transaction_type", "B2B"),
            total_amount=total_amount,
            tax_amount=tax_amount,
            currency_code=body.get("currency_code", "NGN"),
            seller_tin=seller_tin,
            seller_name=body.get("seller_name", ""),
            buyer_tin=body.get("buyer_tin"),
            buyer_name=body.get("buyer_name"),
            line_items=body.get("line_items", []),
            qr_code_data=qr_value if qr_is_png else None,
            firs_invoice_type_code=body.get("firs_invoice_type_code"),
        )
        try:
            # Use the raw client so we can read Edge's confirmation payload.
            resp = await edge_client._client.post(
                "/api/v1/submit",
                json={
                    "batch_id": ref,
                    "company_id": company_id or "",
                    "invoices": [submission.to_dict()],
                },
            )
            resp.raise_for_status()
            edge_data = resp.json()
            confs = edge_data.get("confirmations") or []
            if confs:
                firs_confirmation = confs[0].get("firs_confirmation")
                edge_status = confs[0].get("status", edge_data.get("status", "completed"))
            else:
                edge_status = edge_data.get("status", "completed")
        except Exception as exc:
            edge_status = "EDGE_ERROR"
            edge_errors.append(str(exc))
            logger.warning("lean_finalize_edge_submit_failed: %s", exc)

    accepted = edge_status in ("STUB_ACCEPTED", "completed", "ACCEPTED")
    lifecycle_status = "submitted" if accepted else "submission_failed"

    # ── Step 3b: Link finalize to the invoice ENTITY row ──────────────────
    # The doc was (usually) pre-created at ingest as a COMMITTED OUTBOUND row
    # keyed on queue_id == invoice_id (which equals the finalize ``ref``).
    # On Edge accept: resolve it and flip to TRANSMITTED + real IRN. If no row
    # exists (direct finalize that skipped ingestion), create one already
    # TRANSMITTED. Non-fatal: a DB hiccup must not break the submit chain.
    linked_invoice_id: str | None = None
    if accepted:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            try:
                async with get_connection(pool, "invoices") as conn:
                    async with conn.transaction():
                        existing = await find_by_ref(conn, ref)
                        if existing is None and document_id:
                            existing = await find_by_ref(conn, document_id)
                        if existing is not None:
                            updated = await mark_transmitted(
                                conn,
                                invoice_pk=existing["id"],
                                irn=irn,
                                total_amount=total_amount or None,
                                tax_amount=tax_amount or None,
                            )
                            linked_invoice_id = (updated or existing).get("invoice_id")
                        else:
                            created = await create_finalize_invoice(
                                conn,
                                ref=ref,
                                company_id=company_id,
                                invoice_number=invoice_number,
                                irn=irn,
                                issue_date=issue_date,
                                total_amount=total_amount,
                                tax_amount=tax_amount,
                                seller_tin=seller_tin,
                                seller_name=body.get("seller_name"),
                                buyer_tin=body.get("buyer_tin"),
                                buyer_name=body.get("buyer_name"),
                                direction=direction,
                                currency_code=body.get("currency_code", "NGN"),
                                blob_uuid=document_id,
                                trace_id=trace_id or None,
                            )
                            if created is not None:
                                linked_invoice_id = created.get("invoice_id")
            except Exception:
                logger.exception("lean_finalize_invoice_link_failed: ref=%s", ref)

    result: dict[str, Any] = {
        "ref": ref,
        "trace_id": trace_id,
        "status": lifecycle_status,
        "irn": irn,
        "qr": qr_value,
        "qr_is_png": qr_is_png,
        "firs_confirmation": firs_confirmation,
        "edge_status": edge_status,
        "document_id": document_id,
        "invoice_id": linked_invoice_id or document_id,
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "service_id": service_id,
        "finalized_at": _now_iso(),
    }
    if edge_errors:
        result["edge_errors"] = edge_errors

    # ── Step 4: Persist HLX result to HeartBeat (doc PERSISTS) ────────────
    heartbeat_client = getattr(request.app.state, "heartbeat_client", None)
    hlx_blob_ref = await _persist_hlx(
        heartbeat_client, document_id, company_id, result
    )
    result["hlx_blob_ref"] = hlx_blob_ref

    # ── Step 5: Emit lifecycle SSE Scout reduces ──────────────────────────
    sse_manager = getattr(request.app.state, "sse_manager", None)
    ts = _now_iso()

    # relay.finalize.accepted is Relay-owned (published into Core's stream);
    # Core emits the Core-side artifact + terminal here.
    if hlx_blob_ref:
        await _emit(
            sse_manager,
            SSEEvent(
                event_type=EVENT_CORE_ARTIFACT_HLX_AVAILABLE,
                data={
                    "document_id": document_id,
                    "hlx_blob_ref": hlx_blob_ref,
                },
                data_uuid=document_id,
                company_id=company_id,
                timestamp=ts,
            ),
        )

    terminal_data: dict[str, Any] = {
        "document_id": document_id,
        "invoice_id": linked_invoice_id or document_id,
        "submission_id": firs_confirmation or "",
        "lifecycle_status": lifecycle_status,
        "irn": irn,
    }
    if trace_id:
        terminal_data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_CORE_SUBMISSION_TERMINAL,
            data=terminal_data,
            data_uuid=document_id,
            company_id=company_id,
            timestamp=ts,
        ),
    )

    status_code = 200 if accepted else 502
    return JSONResponse(result, status_code=status_code)
