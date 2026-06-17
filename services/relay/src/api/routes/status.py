"""
POST /api/status — Q37 Gap #5: invoice/batch status lookup.

Accepts a JSON body with exactly ONE of:
    transaction_id  — ERP external reference
    irn             — Invoice Reference Number
    batch_id        — Batch submission id (returns N entries, one per invoice)

Calls StatusService which fans out to HeartBeat (live) and Core (null stub
until Core builds the external_transaction_id lookup column — Bob's ruling
2026-06-17). Returns {"results": [...]} with a flat StatusEntry per record.

Unknown ids → HTTP 200, results=[].  Multiple/no selectors → 400.

Auth: combined dispatcher (HMAC / Bearer JWT / service creds),
same as /api/ingest and /api/finalize.

VERB note: POST with all selectors in the body — identifiers (transaction_id,
IRN, batch_id) must not travel in URLs/proxy logs (same VERB_DELTA rule as
the rest of the relay API).
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ...errors import ValidationFailedError
from ...services.status_service import StatusService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _read_status_body(request: Request) -> dict:
    """Read + parse the status JSON body from the cached raw body.

    BodyCacheMiddleware stashes the raw bytes in ``request.state.raw_body``
    (same pattern as finalize.py).
    """
    raw = getattr(request.state, "raw_body", None)
    if raw is None:
        raw = await request.body()
    if not raw:
        raise ValidationFailedError(
            message="status requires a JSON body with one of: transaction_id, irn, batch_id.",
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailedError(
            message=f"Invalid status JSON body: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError(
            message="status body must be a JSON object.",
        )
    return parsed


@router.post(
    "/api/status",
    summary="Check invoice/batch status (Q37 Gap #5)",
    responses={
        200: {"description": "Status results (may be empty for unknown ids)"},
        400: {"description": "No selector / multiple selectors / invalid body"},
        401: {"description": "Authentication failed"},
    },
)
async def status(request: Request):
    """
    Look up invoice or batch status by one of: transaction_id, irn, or batch_id.

    Exactly ONE selector must be present.  Unknown ids return results=[] (HTTP
    200).  HB data is live; Core data (firs_status, invoice_number) is null
    until Core builds the external_transaction_id lookup (Bob's ruling
    2026-06-17).
    """
    ctx: CallerContext = await authenticate_request(request)
    body = await _read_status_body(request)

    transaction_id = str(body.get("transaction_id") or "").strip() or None
    irn = str(body.get("irn") or "").strip() or None
    batch_id = str(body.get("batch_id") or "").strip() or None

    # Exactly one selector must be present.
    selectors_present = sum(bool(s) for s in (transaction_id, irn, batch_id))
    if selectors_present == 0:
        raise ValidationFailedError(
            message=(
                "Provide exactly one of: transaction_id, irn, or batch_id. "
                "No selector found."
            ),
        )
    if selectors_present > 1:
        raise ValidationFailedError(
            message=(
                "Provide exactly one of: transaction_id, irn, or batch_id. "
                f"Multiple selectors found: "
                + ", ".join(
                    k for k, v in {
                        "transaction_id": transaction_id,
                        "irn": irn,
                        "batch_id": batch_id,
                    }.items()
                    if v
                )
            ),
        )

    trace_state = getattr(request.state, "trace_id", "")
    logger.info(
        "[%s] POST /api/status — selector=%s actor=%s tenant=%s",
        trace_state,
        ("batch_id=" + batch_id) if batch_id
        else ("irn=" + (irn or "")[:16]) if irn
        else ("transaction_id=" + (transaction_id or "")[:16]),
        ctx.actor_type,
        ctx.tenant_id,
    )

    svc: StatusService = request.app.state.status_service
    result = await svc.query(
        transaction_id=transaction_id,
        irn=irn,
        batch_id=batch_id,
        tenant_id=ctx.tenant_id,
    )
    return JSONResponse(content=result.model_dump())
