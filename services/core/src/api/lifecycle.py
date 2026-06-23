"""
Invoice Lifecycle Handlers — Phase-2 flows 10, 6, 9.

These endpoints act on the invoice ENTITY rows materialised by the Phase-2
substrate (ingestion create + finalize link). They each UPDATE/INSERT the
deployed ``invoices.invoices`` table and publish a Scout-reduced lifecycle
SSE event. company_id is ALWAYS set on the SSE (ledger durability) and the
caller's Scout trace_id is ALWAYS echoed in ``data.trace_id``.

  FLOW 10 — payment status:
      POST /api/v1/invoice/{invoice_id}/payment-status  {payment_status, trace_id}
      -> UPDATE invoices.payment_status (enum-validated)
      -> SSE core.payment.update_confirmed

  FLOW 6 — inbound accept / reject:
      POST /api/v1/inbound/accept  {invoice_id, trace_id, reason?}
      POST /api/v1/inbound/reject  {invoice_id, reason, trace_id}
      -> UPDATE inbound_status + inbound_action_* WHERE direction='INBOUND'
      -> SSE core.inbound.accept_confirmed / core.inbound.rejected

  FLOW 9 — reversal (UAT bridge, NO multi-stage approval gate):
      POST /api/v1/reverse  {invoice_id, reversal_kind, trace_id}
      -> mint a linked CREDIT_NOTE row + invoice_references link
      -> re-run the finalize pipeline (IRN/QR/Edge/blob) on the credit note
      -> SSE core.reversal.pending then core.reversal.transmitted

Design is grounded in the DEPLOYED ``database/schemas/invoices.sql``:
  - payment_status CHECK ('UNPAID','PAID','PARTIAL','DISPUTED','CANCELLED')
  - inbound_status CHECK ('PENDING_REVIEW','ACCEPTED','REJECTED','EXPIRED')
    + inbound_action_at / inbound_action_by_user_id / _by_user_email / _reason
  - document_type CHECK includes 'CREDIT_NOTE'
  - invoice_references(reference_type,reference_invoice_id,reference_irn) with
    a BIGINT FK to invoices(id)
The audit triggers (fn_audit_payment_status / fn_audit_inbound_status /
fn_audit_workflow_status) write invoice_history but do NOT pg_notify, so SSE
MUST be published from each handler.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.database.pool import get_connection
from src.finalize.invoice_creator import (
    add_reference,
    create_credit_note,
    get_by_invoice_id,
)
from src.finalize.irn_generator import IRNError
from src.finalize.lean_router import run_finalize_pipeline
from src.sse.models import SSEEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Lifecycle"])

INVOICES_TABLE = "invoices.invoices"

VALID_PAYMENT_STATUSES = {"UNPAID", "PAID", "PARTIAL", "DISPUTED", "CANCELLED"}

# Scout-reduced lifecycle event names (wire-pinned, like lean_router).
EVENT_PAYMENT_UPDATE_CONFIRMED = "core.payment.update_confirmed"
EVENT_INBOUND_ACCEPT_CONFIRMED = "core.inbound.accept_confirmed"
EVENT_INBOUND_REJECTED = "core.inbound.rejected"
EVENT_REVERSAL_PENDING = "core.reversal.pending"
EVENT_REVERSAL_TRANSMITTED = "core.reversal.transmitted"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _emit(sse_manager, event: SSEEvent) -> None:
    """Publish an SSE event; never let an SSE failure break the handler."""
    if sse_manager is None:
        return
    try:
        await sse_manager.publish(event)
    except Exception:  # pragma: no cover - defensive
        logger.exception("lifecycle_sse_publish_failed: %s", event.event_type)


def _actor(request: Request, body: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve (actor_user_id, actor_user_email) from JWT claims or body.

    Body overrides are honoured for the demo flows that pass an explicit
    actor; otherwise we fall back to the request's JWT claims.
    """
    claims = getattr(request.state, "jwt_claims", {}) or {}
    user_id = (
        body.get("actor_user_id")
        or body.get("user_id")
        or claims.get("sub")
        or "unknown"
    )
    user_email = (
        body.get("actor_user_email")
        or body.get("user_email")
        or claims.get("email")
    )
    return str(user_id), (str(user_email) if user_email else None)


# ── FLOW 10: payment status ────────────────────────────────────────────────


@router.post("/invoice/{invoice_id}/payment-status")
async def update_payment_status(request: Request, invoice_id: str) -> JSONResponse:
    """Flow 10 — set an invoice's payment_status.

    Dedicated endpoint (chosen over the generic entity PUT) so we can emit the
    specific ``core.payment.update_confirmed`` lifecycle SSE the Reader/Scout
    reduce. The generic PUT only emits ``invoice.updated``.

    Body: {"payment_status": "PAID", "trace_id": "...", "actor_user_id"?: "..."}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    new_status = (body.get("payment_status") or body.get("new_payment_status") or "").strip().upper()
    trace_id = body.get("trace_id")
    if new_status not in VALID_PAYMENT_STATUSES:
        return JSONResponse(
            {
                "error": "invalid payment_status",
                "allowed": sorted(VALID_PAYMENT_STATUSES),
            },
            status_code=422,
        )

    actor_user_id, _ = _actor(request, body)
    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET payment_status = %s, updated_at = NOW()
                WHERE invoice_id = %s AND deleted_at IS NULL
                RETURNING company_id, payment_status
                """,
                (new_status, invoice_id),
            )
            row = await cur.fetchone()

    if row is None:
        return JSONResponse(
            {"error": f"invoice {invoice_id} not found"}, status_code=404
        )

    company_id = row[0]
    payment_status = row[1]

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.payment_status_changed",
            entity_type="invoice",
            entity_id=invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={"payment_status": payment_status, "trace_id": trace_id},
        )

    data: dict[str, Any] = {
        "document_id": invoice_id,
        "invoice_id": invoice_id,
        "new_payment_status": payment_status,
        "payment_status": payment_status,
        "actor_user_id": actor_user_id,
        "company_id": company_id,
    }
    if trace_id:
        data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_PAYMENT_UPDATE_CONFIRMED,
            data=data,
            data_uuid=invoice_id,
            company_id=company_id,
            timestamp=_now_iso(),
        ),
    )

    return JSONResponse(
        {
            "invoice_id": invoice_id,
            "payment_status": payment_status,
            "trace_id": trace_id,
        },
        status_code=200,
    )


# ── FLOW 6: inbound accept / reject ────────────────────────────────────────


async def _inbound_action(
    request: Request,
    *,
    new_status: str,  # 'ACCEPTED' | 'REJECTED'
    event_type: str,
    require_reason: bool,
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        return JSONResponse({"error": "invoice_id is required"}, status_code=400)

    reason = body.get("reason")
    if require_reason and not (reason and str(reason).strip()):
        return JSONResponse(
            {"error": "reason is required to reject an inbound invoice"},
            status_code=422,
        )

    trace_id = body.get("trace_id")
    actor_user_id, actor_user_email = _actor(request, body)
    now = _now_iso()

    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET inbound_status = %s,
                    inbound_action_at = %s,
                    inbound_action_by_user_id = %s,
                    inbound_action_by_user_email = %s,
                    inbound_action_reason = %s,
                    updated_at = NOW()
                WHERE invoice_id = %s
                  AND direction = 'INBOUND'
                  AND deleted_at IS NULL
                RETURNING company_id, irn, inbound_status
                """,
                (
                    new_status, now, actor_user_id, actor_user_email,
                    (str(reason) if reason else None), invoice_id,
                ),
            )
            row = await cur.fetchone()

    if row is None:
        return JSONResponse(
            {"error": f"inbound invoice {invoice_id} not found"},
            status_code=404,
        )

    company_id = row[0]
    irn = row[1]
    status = row[2]

    if audit_logger:
        await audit_logger.log(
            event_type=f"invoice.inbound_{new_status.lower()}",
            entity_type="invoice",
            entity_id=invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "inbound_status": status,
                "reason": (str(reason) if reason else None),
                "trace_id": trace_id,
            },
        )

    data: dict[str, Any] = {
        "document_id": invoice_id,
        "invoice_id": invoice_id,
        "irn": irn,
        "actor_user_id": actor_user_id,
        "accepted_at": now,
        "status": status,
        "company_id": company_id,
    }
    if new_status == "REJECTED":
        data["reason"] = str(reason) if reason else None
    if trace_id:
        data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=event_type,
            data=data,
            data_uuid=invoice_id,
            company_id=company_id,
            timestamp=now,
        ),
    )

    return JSONResponse(
        {
            "invoice_id": invoice_id,
            "inbound_status": status,
            "irn": irn,
            "trace_id": trace_id,
        },
        status_code=200,
    )


@router.post("/inbound/accept")
async def inbound_accept(request: Request) -> JSONResponse:
    """Flow 6 — accept an INBOUND invoice. Body: {invoice_id, trace_id, reason?}."""
    return await _inbound_action(
        request,
        new_status="ACCEPTED",
        event_type=EVENT_INBOUND_ACCEPT_CONFIRMED,
        require_reason=False,
    )


@router.post("/inbound/reject")
async def inbound_reject(request: Request) -> JSONResponse:
    """Flow 6 — reject an INBOUND invoice. Body: {invoice_id, reason, trace_id}."""
    return await _inbound_action(
        request,
        new_status="REJECTED",
        event_type=EVENT_INBOUND_REJECTED,
        require_reason=True,
    )


# ── FLOW 9: reversal (UAT bridge) ──────────────────────────────────────────


@router.post("/reverse")
async def reverse_invoice(request: Request) -> JSONResponse:
    """Flow 9 — reverse a transmitted invoice via a linked CREDIT_NOTE.

    UAT BRIDGE: this skips the multi-stage reversal approval gate and goes
    straight to minting + finalizing the credit note. Body:

        {"invoice_id": "<original>", "reversal_kind": "FULL"|"PARTIAL",
         "trace_id": "...", "total_amount"?: 0, "tax_amount"?: 0}

    Steps:
      1. Resolve the original invoice row.
      2. Mint a CREDIT_NOTE row (direction inherited, amounts negated/copied).
      3. INSERT invoice_references(reference_type='CREDIT_NOTE',
         reference_invoice_id=original.invoice_id, reference_irn=original.irn).
      4. Emit core.reversal.pending.
      5. Re-run the finalize pipeline on the credit note (IRN/QR/Edge/blob).
      6. Backfill the credit note's real IRN + emit core.reversal.transmitted.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    original_invoice_id = (body.get("invoice_id") or "").strip()
    if not original_invoice_id:
        return JSONResponse({"error": "invoice_id is required"}, status_code=400)

    reversal_kind = (body.get("reversal_kind") or "FULL").strip().upper()
    trace_id = body.get("trace_id")
    actor_user_id, _ = _actor(request, body)

    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    # ── 1. Resolve the original ───────────────────────────────────────────
    async with get_connection(pool, "invoices") as conn:
        original = await get_by_invoice_id(conn, original_invoice_id)
    if original is None:
        return JSONResponse(
            {"error": f"invoice {original_invoice_id} not found"},
            status_code=404,
        )

    company_id = original.get("company_id")
    direction = original.get("direction") or "OUTBOUND"
    currency_code = original.get("document_currency_code") or "NGN"
    seller_tin = original.get("seller_tin")
    seller_name = original.get("seller_name")
    buyer_tin = original.get("buyer_tin")
    buyer_name = original.get("buyer_name")

    # Credit-note amounts: negate the original (FULL) or take the supplied
    # partial amounts. Credit notes carry negative monetary values.
    if reversal_kind == "PARTIAL" and body.get("total_amount") is not None:
        total_amount = -abs(float(body.get("total_amount") or 0))
        tax_amount = -abs(float(body.get("tax_amount") or 0))
    else:
        total_amount = -abs(float(original.get("total_amount") or 0))
        tax_amount = -abs(float(original.get("tax_amount") or 0))

    cn_invoice_id = f"CN-{original_invoice_id}"
    cn_invoice_number = f"CN{''.join(c for c in (original.get('invoice_number') or original_invoice_id) if c.isalnum())[:22]}"
    issue_date = date.today().isoformat()

    # ── 2-3. Mint the credit note + reference link (single txn) ────────────
    credit_row: dict[str, Any] | None = None
    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            credit_row = await create_credit_note(
                conn,
                company_id=company_id,
                invoice_number=cn_invoice_number,
                irn=f"PENDING-{cn_invoice_id}",  # placeholder; finalize overwrites
                issue_date=issue_date,
                original_invoice_id=original_invoice_id,
                total_amount=total_amount,
                tax_amount=tax_amount,
                seller_tin=seller_tin,
                seller_name=seller_name,
                buyer_tin=buyer_tin,
                buyer_name=buyer_name,
                direction=direction,
                currency_code=currency_code,
                blob_uuid=cn_invoice_id,
                trace_id=trace_id,
            )
            if credit_row is not None:
                await add_reference(
                    conn,
                    invoice_pk=credit_row["id"],
                    reference_type="CREDIT_NOTE",
                    reference_invoice_id=original_invoice_id,
                    reference_irn=original.get("irn"),
                    reference_issue_date=original.get("issue_date"),
                )

    if credit_row is None:
        return JSONResponse(
            {"error": "failed to mint credit note"}, status_code=500
        )

    cn_pk = credit_row["id"]

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.reversal_initiated",
            entity_type="invoice",
            entity_id=cn_invoice_id,
            action="CREATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "original_invoice_id": original_invoice_id,
                "reversal_kind": reversal_kind,
                "trace_id": trace_id,
            },
        )

    # ── 4. core.reversal.pending ──────────────────────────────────────────
    pending_data: dict[str, Any] = {
        "document_id": cn_invoice_id,
        "invoice_id": cn_invoice_id,
        "original_invoice_id": original_invoice_id,
        "reversal_kind": reversal_kind,
        "actor_user_id": actor_user_id,
        "company_id": company_id,
    }
    if trace_id:
        pending_data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_REVERSAL_PENDING,
            data=pending_data,
            data_uuid=cn_invoice_id,
            company_id=company_id,
            timestamp=_now_iso(),
        ),
    )

    # ── 5. Re-run the finalize pipeline on the credit note ────────────────
    try:
        pipe = await run_finalize_pipeline(
            request.app.state,
            ref=cn_invoice_id,
            invoice_number=cn_invoice_number,
            issue_date=issue_date,
            company_id=company_id,
            document_id=cn_invoice_id,
            total_amount=total_amount,
            tax_amount=tax_amount,
            direction=direction,
            document_type="CREDIT_NOTE",
            currency_code=currency_code,
            seller_tin=seller_tin or "",
            seller_name=seller_name or "",
            buyer_tin=buyer_tin,
            buyer_name=buyer_name,
        )
    except IRNError as exc:
        return JSONResponse(
            {
                "error": f"credit note IRN generation failed: {exc}",
                "invoice_id": cn_invoice_id,
                "original_invoice_id": original_invoice_id,
            },
            status_code=422,
        )

    real_irn = pipe["irn"]
    accepted = pipe["accepted"]

    # ── 6. Flip the credit note to TRANSMITTED + real IRN ─────────────────
    now = _now_iso()
    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET irn = %s,
                    workflow_status = 'TRANSMITTED',
                    transmission_status = 'TRANSMITTED',
                    transmission_date = %s,
                    finalized_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (real_irn, now, now, cn_pk),
            )

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.reversal_transmitted",
            entity_type="invoice",
            entity_id=cn_invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "original_invoice_id": original_invoice_id,
                "irn": real_irn,
                "edge_status": pipe.get("edge_status"),
                "trace_id": trace_id,
            },
        )

    # ── core.reversal.transmitted ─────────────────────────────────────────
    transmitted_data: dict[str, Any] = {
        "document_id": cn_invoice_id,
        "invoice_id": cn_invoice_id,
        "original_invoice_id": original_invoice_id,
        "irn": real_irn,
        "company_id": company_id,
    }
    if trace_id:
        transmitted_data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_REVERSAL_TRANSMITTED,
            data=transmitted_data,
            data_uuid=cn_invoice_id,
            company_id=company_id,
            timestamp=now,
        ),
    )

    return JSONResponse(
        {
            "invoice_id": cn_invoice_id,
            "original_invoice_id": original_invoice_id,
            "irn": real_irn,
            "qr": pipe.get("qr"),
            "qr_is_png": pipe.get("qr_is_png"),
            "edge_status": pipe.get("edge_status"),
            "reversal_kind": reversal_kind,
            "trace_id": trace_id,
        },
        status_code=200 if accepted else 502,
    )
