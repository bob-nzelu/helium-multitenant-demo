"""
POST /api/status — external status surface (L30 / DATA_MODEL_CANONICAL §6).

Accepts a JSON body with exactly ONE of:
    transaction_id  — ERP external reference   (HB file_transactions + Core)
    batch_id        — Batch submission id       (HB + Core; returns N entries)
    invoice_number  — Tenant invoice number     (Core only — HB is invoice-blind)
    irn             — Invoice Reference Number   (Core only — HB is invoice-blind)

StatusService orchestrates HeartBeat (pre-invoice: pending/acknowledged/
not_an_invoice from blob.file_transactions) + Core (invoice-level: IRN, FIRS
state) and merges to the flat L29 results[] shape. Both backends are NEEDS-*
stubs until HB ships file_transactions and Core ships the lookup, so a live
query returns results=[] until then.

Unknown ids → HTTP 200, results=[].  Multiple/no selectors → 400.

Auth: combined dispatcher (HMAC / Bearer JWT / service creds),
same as /api/ingest and /api/finalize.

VERB note: POST with all selectors in the body — identifiers (transaction_id,
IRN, invoice_number, batch_id) must not travel in URLs/proxy logs (same
VERB_DELTA rule as the rest of the relay API).
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
            message="status requires a JSON body with one of: transaction_id, batch_id, invoice_number, irn.",
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
    summary="External status surface — by transaction_id/batch_id/invoice_number/irn (L30)",
    responses={
        200: {"description": "Status results (may be empty for unknown ids)"},
        400: {"description": "No selector / multiple selectors / invalid body"},
        401: {"description": "Authentication failed"},
    },
)
async def status(request: Request):
    """
    Look up status by exactly ONE of: transaction_id, batch_id, invoice_number, irn.

    Unknown ids return results=[] (HTTP 200). HB answers the pre-invoice phase
    (transaction_id/batch_id); Core is the sole resolver of invoice_number/irn
    and supplies firs_status. Both backends are stubs until their L30 chips land.
    """
    ctx: CallerContext = await authenticate_request(request)
    body = await _read_status_body(request)

    transaction_id = str(body.get("transaction_id") or "").strip() or None
    batch_id = str(body.get("batch_id") or "").strip() or None
    invoice_number = str(body.get("invoice_number") or "").strip() or None
    irn = str(body.get("irn") or "").strip() or None

    # Exactly one of the four L30 selectors must be present.
    selectors = {
        "transaction_id": transaction_id,
        "batch_id": batch_id,
        "invoice_number": invoice_number,
        "irn": irn,
    }
    present = [k for k, v in selectors.items() if v]
    if len(present) == 0:
        raise ValidationFailedError(
            message=(
                "Provide exactly one of: transaction_id, batch_id, "
                "invoice_number, or irn. No selector found."
            ),
        )
    if len(present) > 1:
        raise ValidationFailedError(
            message=(
                "Provide exactly one of: transaction_id, batch_id, "
                "invoice_number, or irn. Multiple selectors found: "
                + ", ".join(present)
            ),
        )

    trace_state = getattr(request.state, "trace_id", "")
    logger.info(
        "[%s] POST /api/status — selector=%s=%s actor=%s tenant=%s",
        trace_state,
        present[0],
        (selectors[present[0]] or "")[:16],
        ctx.actor_type,
        ctx.tenant_id,
    )

    svc: StatusService = request.app.state.status_service
    result = await svc.query(
        transaction_id=transaction_id,
        batch_id=batch_id,
        invoice_number=invoice_number,
        irn=irn,
        tenant_id=ctx.tenant_id,
    )
    return JSONResponse(content=result.model_dump())
