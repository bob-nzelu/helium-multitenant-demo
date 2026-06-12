"""
POST /api/finalize — the #3 reference-only fiscalize call (R-M2 / §B-Submit).

Reference-only finalize: fiscalizes an ALREADY-ingested doc by reference
(file SHA-256 / ``trace_id`` / ``doc_ref``) with **NO PDF bytes**. Same intent
and same downstream lifecycle/SSE as ``ingest(finalize=true)`` (#2).

Contract (CLAUDE.md §B-Submit L260-266; SCOUT contract §3.3):
    - echoes the client ``trace_id`` on the lifecycle SSE;
    - duplicate / already-finalized ``trace_id`` → **409** (client treats as
      success, idempotent);
    - same ``trace_id`` carried across the #2↔#3 switch.

VERB note: POST-only with the reference in the **body** (never in the URL) —
``ref``/``trace_id`` are correlation handles, kept out of proxy/referrer logs
(debt-map VERB_DELTA, Golden Rule: POST-only except SSE + unauth health/metrics).

Auth: the existing combined dispatcher ``authenticate_request`` (HMAC / Bearer
JWT / service creds). No bytes, so the JSON body is HMAC-signable directly.

Body (JSON):
    {"ref": "<sha256|doc_ref>", "trace_id": "<uuidv7>", "doc_ref": "<optional>"}
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ..models import FinalizeResponse
from ...errors import ValidationFailedError
from ...services.finalize import FinalizeService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _read_finalize_body(request: Request) -> dict:
    """Read + parse the finalize JSON body from the cached raw body.

    BodyCacheMiddleware stashes the raw bytes in ``request.state.raw_body``
    (so HMAC auth and this handler read the same body). Fall back to
    ``request.body()`` if the cache is absent (e.g. unit calls).
    """
    raw = getattr(request.state, "raw_body", None)
    if raw is None:
        raw = await request.body()
    if not raw:
        raise ValidationFailedError(
            message="finalize requires a JSON body with 'ref' and/or 'trace_id'.",
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailedError(
            message=f"Invalid finalize JSON body: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError(
            message="finalize body must be a JSON object.",
        )
    return parsed


@router.post(
    "/api/finalize",
    summary="Finalize an already-ingested doc by reference (#3, no bytes)",
    responses={
        202: {"description": "Finalize accepted"},
        400: {"description": "Missing reference / invalid body"},
        401: {"description": "Authentication failed"},
        409: {"description": "Already finalized (treat as success, idempotent)"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def finalize(request: Request):
    """
    Reference-only fiscalize (#3) for an already-ingested doc — NO PDF bytes.

    Echoes ``trace_id`` on the resulting ``relay.finalize.accepted`` lifecycle
    event. A duplicate / already-finalized ``trace_id`` returns 409
    (ALREADY_FINALIZED), which the client treats as success.
    """
    # Authenticate via the combined dispatcher (HMAC / JWT / service creds).
    # Resolved here (not as a Depends default) so the dispatcher is the SAME
    # one /api/ingest uses, and the cached raw body is read for HMAC before we
    # parse the JSON. ``ctx`` is intentionally NOT a handler parameter so
    # FastAPI doesn't try to validate CallerContext as a request body.
    ctx: CallerContext = await authenticate_request(request)

    trace_state = getattr(request.state, "trace_id", "")
    body = await _read_finalize_body(request)

    ref = str(body.get("ref") or "").strip()
    # CLIENT-supplied trace_id ONLY — never fall back to the auto-generated
    # request trace_id (request.state.trace_id). That fallback would (a) mask the
    # missing-reference 400 (trace_id would always be non-empty) and (b) give
    # every ref-only request a fresh idempotency key, breaking ref-based dedup.
    # The request trace_id stays for logging (`trace_state`) only.
    trace_id = str(body.get("trace_id") or "").strip()
    doc_ref = str(body.get("doc_ref") or "").strip()

    # JWT forwarding (mirrors /api/ingest §3.1): user path forwards the JWT so
    # Core attributes the fiscalize to the user, not to Relay.
    jwt_token: Optional[str] = None
    if ctx.is_user:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            jwt_token = auth_header[7:].strip()

    meta = {
        "queue_mode": "api",
        "connection_type": "api",
        "caller_source": ctx.raw_api_key or ctx.identifier or "unknown",
    }

    logger.info(
        "[%s] POST /api/finalize — ref=%s trace_id=%s actor=%s tenant=%s jwt=%s",
        trace_state,
        (ref or doc_ref or "(none)")[:16],
        trace_id or "(none)",
        ctx.actor_type,
        ctx.tenant_id,
        "yes" if jwt_token else "no",
    )

    svc: FinalizeService = request.app.state.finalize_service
    # AlreadyFinalizedError (409) + FinalizeReferenceMissingError (400) are
    # RelayErrors → handled by relay_error_handler into structured JSON.
    result = await svc.finalize(
        ref=ref,
        trace_id=trace_id,
        doc_ref=doc_ref,
        actor_user_id=ctx.identifier if ctx.is_user else "",
        metadata=meta,
        jwt_token=jwt_token,
    )

    payload = FinalizeResponse(
        status=result.status,
        call="finalize",
        finalize_by_reference=result.finalize_by_reference,
        raw_bytes_sent=result.raw_bytes_sent,
        ref=result.ref,
        doc_ref=result.doc_ref,
        trace_id=result.trace_id,
        event_id=result.event_id,
        event_family=result.event_family,
        idempotent_replay=result.idempotent_replay,
    )
    # SBS returns 202-queued for finalize (relay.py:373-385). We mirror 202.
    return JSONResponse(status_code=202, content=payload.model_dump())
