"""
Invoice Approval Handlers — Phase-2 stage 3, flows 7 & 8 (APPROVAL).

These endpoints drive the multi-stage approval gate on the invoice ENTITY rows
materialised by the Phase-2 substrate. They are a sibling of the lifecycle
handlers (flows 6/9/10 in ``src/api/lifecycle.py``) and CLONE that module's
style: each UPDATEs the deployed ``invoices.invoices`` row, INSERTs an audit
row into the ratified ``invoices.approval_events`` ledger, and publishes a
Scout-reduced approval SSE. company_id is ALWAYS read off the invoice row and
set on the SSE (ledger durability); the caller's Scout trace_id is ALWAYS
echoed in ``data.trace_id`` (optimistic->confirmed §B-EventLog correlation).

  FLOW 7 — request approval:
      POST /api/v1/approval/request
        {invoice_id, target_actor_id?, request_type?, trace_id, actor_user_id?}
      -> UPDATE invoices.approval_status = 'PENDING_APPROVAL'
      -> INSERT approval_events(action='requested', confirmation_status='confirmed')
      -> SSE core.approval.requested

  FLOW 8 — approve / reject:
      POST /api/v1/approval/approve  {invoice_id, trace_id, reason?, actor_user_id?}
        guard: only if approval_status='PENDING_APPROVAL' (else 409 already-actioned)
      -> UPDATE invoices.approval_status = 'APPROVED'
      -> INSERT approval_events(action='approved')
      -> SSE core.approval.action_confirmed

      POST /api/v1/approval/reject   {invoice_id, reason(required), trace_id, actor_user_id?}
      -> UPDATE invoices.approval_status = 'REJECTED'
      -> INSERT approval_events(action='rejected', reason)
      -> SSE core.approval.action_failed

Grounded in the RATIFIED DDL appended to ``database/schemas/invoices.sql``:
  - invoices.approval_status CHECK ('NONE','PENDING_APPROVAL','APPROVED','REJECTED')
  - invoices.approval_events(event_id, invoice_id, action, actor_user_id,
    target_actor_id, request_type, reason, trace_id, confirmation_status,
    created_at, confirmed_at)
404 if the invoice_id is absent; 422 if a reject reason is missing; 409 if an
approve is attempted on a row not in PENDING_APPROVAL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.database.pool import get_connection
from src.finalize.invoice_creator import get_by_invoice_id
from src.sse.models import SSEEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Approval"])

INVOICES_TABLE = "invoices.invoices"
APPROVAL_EVENTS_TABLE = "invoices.approval_events"

# Scout-reduced approval event names (wire-pinned, like lifecycle.py).
EVENT_APPROVAL_REQUESTED = "core.approval.requested"
EVENT_APPROVAL_ACTION_CONFIRMED = "core.approval.action_confirmed"
EVENT_APPROVAL_ACTION_FAILED = "core.approval.action_failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _emit(sse_manager, event: SSEEvent) -> None:
    """Publish an SSE event; never let an SSE failure break the handler."""
    if sse_manager is None:
        return
    try:
        await sse_manager.publish(event)
    except Exception:  # pragma: no cover - defensive
        logger.exception("approval_sse_publish_failed: %s", event.event_type)


def _actor(request: Request, body: dict[str, Any]) -> str:
    """Resolve actor_user_id from the body override or the request's JWT claims.

    Body overrides are honoured for the demo flows that pass an explicit actor;
    otherwise we fall back to the request's JWT ``sub`` claim.
    """
    claims = getattr(request.state, "jwt_claims", {}) or {}
    user_id = (
        body.get("actor_user_id")
        or body.get("user_id")
        or claims.get("sub")
        or "unknown"
    )
    return str(user_id)


async def _record_event(
    conn,
    *,
    invoice_id: str,
    action: str,  # 'requested' | 'approved' | 'rejected'
    actor_user_id: str,
    target_actor_id: str | None = None,
    request_type: str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
) -> str | None:
    """INSERT a confirmed approval_events row; return its event_id (UUID str)."""
    cur = await conn.execute(
        f"""
        INSERT INTO {APPROVAL_EVENTS_TABLE} (
            invoice_id, action, actor_user_id, target_actor_id,
            request_type, reason, trace_id,
            confirmation_status, confirmed_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            'confirmed', now()
        )
        RETURNING event_id
        """,
        (
            invoice_id, action, actor_user_id, target_actor_id,
            request_type, reason, trace_id,
        ),
    )
    row = await cur.fetchone()
    return str(row[0]) if row is not None else None


# ── FLOW 7: request approval ───────────────────────────────────────────────


@router.post("/approval/request")
async def request_approval(request: Request) -> JSONResponse:
    """Flow 7 — request approval for an invoice.

    Body: {"invoice_id": "...", "target_actor_id"?: "...", "request_type"?: "...",
           "trace_id": "...", "actor_user_id"?: "..."}

    UPDATE approval_status -> 'PENDING_APPROVAL', INSERT a 'requested' audit
    row, publish core.approval.requested.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        return JSONResponse({"error": "invoice_id is required"}, status_code=400)

    target_actor_id = body.get("target_actor_id")
    request_type = body.get("request_type")
    trace_id = body.get("trace_id")
    actor_user_id = _actor(request, body)

    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    approval_id: str | None = None
    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET approval_status = 'PENDING_APPROVAL', updated_at = NOW()
                WHERE invoice_id = %s AND deleted_at IS NULL
                RETURNING company_id, approval_status
                """,
                (invoice_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return JSONResponse(
                    {"error": f"invoice {invoice_id} not found"}, status_code=404
                )
            company_id = row[0]
            approval_id = await _record_event(
                conn,
                invoice_id=invoice_id,
                action="requested",
                actor_user_id=actor_user_id,
                target_actor_id=(str(target_actor_id) if target_actor_id else None),
                request_type=(str(request_type) if request_type else None),
                trace_id=trace_id,
            )

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.approval_requested",
            entity_type="invoice",
            entity_id=invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "approval_id": approval_id,
                "target_actor_id": target_actor_id,
                "request_type": request_type,
                "trace_id": trace_id,
            },
        )

    data: dict[str, Any] = {
        "document_id": invoice_id,
        "invoice_id": invoice_id,
        "approval_id": approval_id,
        "actor_user_id": actor_user_id,
        "target_actor_id": target_actor_id,
        "request_type": request_type,
        "company_id": company_id,
    }
    if trace_id:
        data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_APPROVAL_REQUESTED,
            data=data,
            data_uuid=invoice_id,
            company_id=company_id,
            timestamp=_now_iso(),
        ),
    )

    return JSONResponse(
        {
            "invoice_id": invoice_id,
            "approval_status": "PENDING_APPROVAL",
            "approval_id": approval_id,
            "trace_id": trace_id,
        },
        status_code=200,
    )


# ── FLOW 8: approve ────────────────────────────────────────────────────────


@router.post("/approval/approve")
async def approve(request: Request) -> JSONResponse:
    """Flow 8 (approve) — approve a PENDING_APPROVAL invoice.

    Body: {"invoice_id": "...", "trace_id": "...", "reason"?: "...",
           "actor_user_id"?: "..."}

    Guard: the UPDATE only matches a row whose approval_status is currently
    'PENDING_APPROVAL'. If the invoice exists but is not pending (already
    approved/rejected/never requested) -> 409 already-actioned. Publishes
    core.approval.action_confirmed.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        return JSONResponse({"error": "invoice_id is required"}, status_code=400)

    reason = body.get("reason")
    trace_id = body.get("trace_id")
    actor_user_id = _actor(request, body)

    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    approval_id: str | None = None
    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET approval_status = 'APPROVED', updated_at = NOW()
                WHERE invoice_id = %s
                  AND approval_status = 'PENDING_APPROVAL'
                  AND deleted_at IS NULL
                RETURNING company_id
                """,
                (invoice_id,),
            )
            row = await cur.fetchone()
            if row is None:
                # Distinguish 404 (absent) from 409 (present but not pending).
                existing = await get_by_invoice_id(conn, invoice_id)
                if existing is None:
                    return JSONResponse(
                        {"error": f"invoice {invoice_id} not found"},
                        status_code=404,
                    )
                return JSONResponse(
                    {
                        "error": "invoice is not pending approval",
                        "invoice_id": invoice_id,
                        "approval_status": existing.get("approval_status"),
                    },
                    status_code=409,
                )
            company_id = row[0]
            approval_id = await _record_event(
                conn,
                invoice_id=invoice_id,
                action="approved",
                actor_user_id=actor_user_id,
                reason=(str(reason) if reason else None),
                trace_id=trace_id,
            )

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.approval_approved",
            entity_type="invoice",
            entity_id=invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "approval_id": approval_id,
                "reason": (str(reason) if reason else None),
                "trace_id": trace_id,
            },
        )

    data: dict[str, Any] = {
        "document_id": invoice_id,
        "invoice_id": invoice_id,
        "action": "approved",
        "approval_id": approval_id,
        "actor_user_id": actor_user_id,
        "company_id": company_id,
    }
    if trace_id:
        data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_APPROVAL_ACTION_CONFIRMED,
            data=data,
            data_uuid=invoice_id,
            company_id=company_id,
            timestamp=_now_iso(),
        ),
    )

    return JSONResponse(
        {
            "invoice_id": invoice_id,
            "approval_status": "APPROVED",
            "approval_id": approval_id,
            "trace_id": trace_id,
        },
        status_code=200,
    )


# ── FLOW 8: reject ─────────────────────────────────────────────────────────


@router.post("/approval/reject")
async def reject(request: Request) -> JSONResponse:
    """Flow 8 (reject) — reject an invoice's approval. ``reason`` is required.

    Body: {"invoice_id": "...", "reason": "...", "trace_id": "...",
           "actor_user_id"?: "..."}

    422 if reason is missing. UPDATE approval_status -> 'REJECTED', INSERT a
    'rejected' audit row carrying the reason, publish
    core.approval.action_failed with failure_reason=reason.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        return JSONResponse({"error": "invoice_id is required"}, status_code=400)

    reason = body.get("reason")
    if not (reason and str(reason).strip()):
        return JSONResponse(
            {"error": "reason is required to reject an approval"},
            status_code=422,
        )
    reason = str(reason).strip()

    trace_id = body.get("trace_id")
    actor_user_id = _actor(request, body)

    pool = request.app.state.pool
    sse_manager = getattr(request.app.state, "sse_manager", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)

    approval_id: str | None = None
    async with get_connection(pool, "invoices") as conn:
        async with conn.transaction():
            cur = await conn.execute(
                f"""
                UPDATE {INVOICES_TABLE}
                SET approval_status = 'REJECTED', updated_at = NOW()
                WHERE invoice_id = %s AND deleted_at IS NULL
                RETURNING company_id
                """,
                (invoice_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return JSONResponse(
                    {"error": f"invoice {invoice_id} not found"}, status_code=404
                )
            company_id = row[0]
            approval_id = await _record_event(
                conn,
                invoice_id=invoice_id,
                action="rejected",
                actor_user_id=actor_user_id,
                reason=reason,
                trace_id=trace_id,
            )

    if audit_logger:
        await audit_logger.log(
            event_type="invoice.approval_rejected",
            entity_type="invoice",
            entity_id=invoice_id,
            action="UPDATE",
            company_id=company_id,
            actor_id=actor_user_id,
            metadata={
                "approval_id": approval_id,
                "reason": reason,
                "trace_id": trace_id,
            },
        )

    data: dict[str, Any] = {
        "document_id": invoice_id,
        "invoice_id": invoice_id,
        "action": "rejected",
        "approval_id": approval_id,
        "failure_reason": reason,
        "actor_user_id": actor_user_id,
        "company_id": company_id,
    }
    if trace_id:
        data["trace_id"] = trace_id
    await _emit(
        sse_manager,
        SSEEvent(
            event_type=EVENT_APPROVAL_ACTION_FAILED,
            data=data,
            data_uuid=invoice_id,
            company_id=company_id,
            timestamp=_now_iso(),
        ),
    )

    return JSONResponse(
        {
            "invoice_id": invoice_id,
            "approval_status": "REJECTED",
            "approval_id": approval_id,
            "reason": reason,
            "trace_id": trace_id,
        },
        status_code=200,
    )
