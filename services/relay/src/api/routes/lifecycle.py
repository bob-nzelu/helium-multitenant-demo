"""
Phase-2 invoice lifecycle gateways (flows 10 / 6 / 9).

Reader-initiated lifecycle actions on an ALREADY-ingested invoice — payment
status (#10), inbound accept/reject (#6), and reversal-via-credit-note (#9).
Each route CLONES the /api/finalize pattern exactly (services/relay/src/api/
routes/finalize.py):

  - POST-only, IDs carried in the **body** (never in the URL) — invoice_id /
    trace_id are correlation handles kept out of proxy/referrer logs
    (VERB_DELTA, Golden Rule: POST-only except SSE + unauth health/metrics);
  - ``dependencies=[Depends(version_drift_guard)]`` so the version-drift gateway
    runs BEFORE the handler body (409 {code,axis,expected,got} on a stale axis,
    NOT forwarded);
  - ``authenticate_request(request)`` re-resolved INSIDE the handler (the SAME
    combined dispatcher /api/ingest uses; reads the cached raw body for HMAC
    before parsing JSON) — ``ctx`` is intentionally NOT a handler parameter so
    FastAPI doesn't validate CallerContext as a request body;
  - JWT-forward to Core (Bearer for user attribution) on the user path;
  - 202 ack shape (Relay is ingress-only — Core owns the work + emits the
    lifecycle SSE that Scout reduces).

Relay holds no per-invoice store — it forwards the lifecycle trigger + trace_id
to Core (HTTP) and surfaces Core's verbatim result (the lifecycle event id/family
Core emitted). Errors from Core (404 absent / 422 bad enum-or-missing-reason /
502 reversal failure) surface as RelayErrors via the global handler.

Route → Core endpoint mapping:
    POST /api/payment-status   → POST /api/v1/invoice/{invoice_id}/payment-status
    POST /api/inbound/accept   → POST /api/v1/inbound/accept
    POST /api/inbound/reject   → POST /api/v1/inbound/reject
    POST /api/reverse          → POST /api/v1/reverse
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ..version_drift import version_drift_guard
from ...errors import ValidationFailedError

logger = logging.getLogger(__name__)

router = APIRouter()


async def _read_json_body(request: Request) -> dict:
    """Read + parse the JSON body from the cached raw body.

    BodyCacheMiddleware stashes the raw bytes in ``request.state.raw_body``
    (so HMAC auth and this handler read the same body). Fall back to
    ``request.body()`` if the cache is absent (e.g. unit calls). Mirrors
    finalize._read_finalize_body.
    """
    raw = getattr(request.state, "raw_body", None)
    if raw is None:
        raw = await request.body()
    if not raw:
        raise ValidationFailedError(
            message="Request requires a JSON body.",
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailedError(
            message=f"Invalid JSON body: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError(
            message="Request body must be a JSON object.",
        )
    return parsed


def _user_jwt(request: Request, ctx: CallerContext) -> Optional[str]:
    """Extract the Bearer JWT for forwarding to Core (user path only).

    Mirrors /api/finalize §3.1: the user path forwards the JWT so Core
    attributes the action to the user, not to Relay.
    """
    if not ctx.is_user:
        return None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


# ── Flow 10 — payment status ────────────────────────────────────────────────


@router.post(
    "/api/payment-status",
    summary="Set an invoice's payment status (flow 10, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Payment-status update accepted"},
        400: {"description": "Missing invoice_id/payment_status / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version drift"},
        422: {"description": "Invalid payment_status enum"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def payment_status(request: Request):
    """
    Flow 10 — forward a payment-status change to Core.

    Body: {"invoice_id": <id>, "payment_status": <enum>, "trace_id": <uuidv7>}
    Core enum-validates payment_status (UNPAID/PAID/PARTIAL/DISPUTED/CANCELLED)
    and emits ``core.payment.update_confirmed``.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    payment_status_value = str(body.get("payment_status") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="payment-status requires 'invoice_id'.")
    if not payment_status_value:
        raise ValidationFailedError(message="payment-status requires 'payment_status'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/payment-status — invoice_id=%s status=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        payment_status_value,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.update_payment_status(
        invoice_id=invoice_id,
        payment_status=payment_status_value,
        trace_id=trace_id,
        actor_user_id=ctx.identifier if ctx.is_user else "",
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "payment-status",
            "invoice_id": invoice_id,
            "payment_status": payment_status_value,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 6 — inbound accept ─────────────────────────────────────────────────


@router.post(
    "/api/inbound/accept",
    summary="Accept an inbound invoice (flow 6, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Accept accepted"},
        400: {"description": "Missing invoice_id / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found / not inbound"},
        409: {"description": "Version drift"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def inbound_accept(request: Request):
    """
    Flow 6 (accept) — forward an inbound-invoice acceptance to Core.

    Body: {"invoice_id": <id>, "trace_id": <uuidv7>, "reason": <optional>}
    Core flips inbound_status='ACCEPTED' (guarding direction='INBOUND' AND
    deleted_at IS NULL) and emits the lifecycle SSE.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()
    reason = str(body.get("reason") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="inbound/accept requires 'invoice_id'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/inbound/accept — invoice_id=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.inbound_accept(
        invoice_id=invoice_id,
        trace_id=trace_id,
        reason=reason,
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "inbound-accept",
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 6 — inbound reject ─────────────────────────────────────────────────


@router.post(
    "/api/inbound/reject",
    summary="Reject an inbound invoice (flow 6, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Reject accepted"},
        400: {"description": "Missing invoice_id / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found / not inbound"},
        409: {"description": "Version drift"},
        422: {"description": "Missing reason"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def inbound_reject(request: Request):
    """
    Flow 6 (reject) — forward an inbound-invoice rejection to Core.

    Body: {"invoice_id": <id>, "reason": <required>, "trace_id": <uuidv7>}
    ``reason`` is required (422 from Core if missing — Relay also guards 400
    early). Core flips inbound_status='REJECTED' and emits the lifecycle SSE.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    reason = str(body.get("reason") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="inbound/reject requires 'invoice_id'.")
    if not reason:
        raise ValidationFailedError(message="inbound/reject requires a non-empty 'reason'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/inbound/reject — invoice_id=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.inbound_reject(
        invoice_id=invoice_id,
        reason=reason,
        trace_id=trace_id,
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "inbound-reject",
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 11 — inbound arrival (receive/seed) ────────────────────────────────


@router.post(
    "/api/inbound_arrival",
    summary="Receive/seed an inbound invoice for the tenant (flow 11)",
    responses={
        200: {"description": "Inbound invoice created"},
        400: {"description": "Invalid JSON body"},
        401: {"description": "Authentication failed"},
    },
)
async def inbound_arrival(request: Request):
    """
    Flow 11 — an INBOUND invoice ARRIVED for the tenant.

    Forwards the inbound facts to Core, which INSERTs a direction='INBOUND',
    inbound_status='PENDING_REVIEW' row and emits ``core.inbound.received`` so the
    entitled actors' Reader Inbound tab surfaces it (accept/reject then act on
    it). In production this is the cross-tenant transmit landing; on the demo it
    seeds an inbound doc. ``invoice_id`` optional (Core mints one if absent).
    Matches the path the Reader client's ``report_inbound_arrival`` already posts.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    jwt_token = _user_jwt(request, ctx)
    logger.info(
        "[%s] POST /api/inbound_arrival — invoice_id=%s actor=%s jwt=%s",
        trace_state,
        body.get("invoice_id") or "(mint)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.inbound_arrival(payload=body, jwt_token=jwt_token)

    return JSONResponse(
        status_code=200,
        content={
            "status": "accepted",
            "call": "inbound-arrival",
            "invoice_id": result.get("invoice_id"),
            "inbound_status": result.get("inbound_status"),
            "trace_id": body.get("trace_id"),
            "core": result,
        },
    )


# ── Flow 9 — reverse (credit note) ──────────────────────────────────────────


@router.post(
    "/api/reverse",
    summary="Reverse an invoice via a credit note (flow 9, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Reversal accepted"},
        400: {"description": "Missing invoice_id/reversal_kind / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version drift"},
        429: {"description": "Rate limit exceeded"},
        503: {"description": "Core/Edge reversal failed"},
    },
)
async def reverse(request: Request):
    """
    Flow 9 — forward a reversal (credit-note) trigger to Core.

    Body: {"invoice_id": <orig id>, "reversal_kind": "FULL"|"PARTIAL",
           "trace_id": <uuidv7>}
    Core mints a CREDIT_NOTE row (negated amounts), re-runs the finalize
    pipeline, flips it to TRANSMITTED + real IRN, and emits core.reversal.pending
    then core.reversal.transmitted.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    reversal_kind = str(body.get("reversal_kind") or "").strip().upper()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="reverse requires 'invoice_id'.")
    if reversal_kind not in ("FULL", "PARTIAL"):
        raise ValidationFailedError(
            message="reverse requires 'reversal_kind' of FULL or PARTIAL.",
        )

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/reverse — invoice_id=%s reversal_kind=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        reversal_kind,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.reverse(
        invoice_id=invoice_id,
        reversal_kind=reversal_kind,
        trace_id=trace_id,
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "reverse",
            "invoice_id": invoice_id,
            "reversal_kind": reversal_kind,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 7 — request approval ───────────────────────────────────────────────


@router.post(
    "/api/approval/request",
    summary="Request approval for an invoice (flow 7, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Approval request accepted"},
        400: {"description": "Missing invoice_id / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version drift"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def approval_request(request: Request):
    """
    Flow 7 — forward an approval request to Core.

    Body: {"invoice_id": <id>, "target_actor_id": <opt>, "request_type": <opt>,
           "trace_id": <uuidv7>}
    Core flips approval_status='PENDING_APPROVAL', writes an approval_events
    'requested' row, and emits ``core.approval.requested``.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    target_actor_id = str(body.get("target_actor_id") or "").strip()
    request_type = str(body.get("request_type") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="approval/request requires 'invoice_id'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/approval/request — invoice_id=%s target=%s type=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        target_actor_id or "(none)",
        request_type or "(none)",
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.approval_request(
        invoice_id=invoice_id,
        trace_id=trace_id,
        target_actor_id=target_actor_id,
        request_type=request_type,
        actor_user_id=ctx.identifier if ctx.is_user else "",
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "approval-request",
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 8 — approve ────────────────────────────────────────────────────────


@router.post(
    "/api/approval/approve",
    summary="Approve a pending invoice (flow 8, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Approve accepted"},
        400: {"description": "Missing invoice_id / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version drift / not pending (already actioned)"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def approval_approve(request: Request):
    """
    Flow 8 (approve) — forward an approval to Core.

    Body: {"invoice_id": <id>, "trace_id": <uuidv7>, "reason": <opt>}
    Core guards approval_status='PENDING_APPROVAL' (409 already-actioned),
    flips it to 'APPROVED', and emits ``core.approval.action_confirmed``.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    reason = str(body.get("reason") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="approval/approve requires 'invoice_id'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/approval/approve — invoice_id=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.approval_approve(
        invoice_id=invoice_id,
        trace_id=trace_id,
        reason=reason,
        actor_user_id=ctx.identifier if ctx.is_user else "",
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "approval-approve",
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "core": result,
        },
    )


# ── Flow 8 — reject ─────────────────────────────────────────────────────────


@router.post(
    "/api/approval/reject",
    summary="Reject an invoice's approval (flow 8, no bytes)",
    dependencies=[Depends(version_drift_guard)],
    responses={
        202: {"description": "Reject accepted"},
        400: {"description": "Missing invoice_id / invalid body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version drift"},
        422: {"description": "Missing reason"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def approval_reject(request: Request):
    """
    Flow 8 (reject) — forward an approval rejection to Core.

    Body: {"invoice_id": <id>, "reason": <required>, "trace_id": <uuidv7>}
    ``reason`` is required (422 from Core if missing — Relay also guards 400
    early). Core flips approval_status='REJECTED' and emits
    ``core.approval.action_failed``.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_json_body(request)

    invoice_id = str(body.get("invoice_id") or "").strip()
    reason = str(body.get("reason") or "").strip()
    trace_id = str(body.get("trace_id") or "").strip()

    if not invoice_id:
        raise ValidationFailedError(message="approval/reject requires 'invoice_id'.")
    if not reason:
        raise ValidationFailedError(message="approval/reject requires a non-empty 'reason'.")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/approval/reject — invoice_id=%s trace_id=%s actor=%s jwt=%s",
        trace_state,
        invoice_id,
        trace_id or "(none)",
        ctx.actor_type,
        "yes" if jwt_token else "no",
    )

    core = request.app.state.core
    result = await core.approval_reject(
        invoice_id=invoice_id,
        reason=reason,
        trace_id=trace_id,
        actor_user_id=ctx.identifier if ctx.is_user else "",
        jwt_token=jwt_token,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "call": "approval-reject",
            "invoice_id": invoice_id,
            "trace_id": trace_id,
            "core": result,
        },
    )
