"""
Reader read-side bridge — my_documents list, events SSE, blob fetch.

The Reader's tenant replicator (scout_tenant_replicator.py via
scout_backend_http_client.py) reads three surfaces off the Relay base_url:

    GET  /api/my_documents   -> {"documents": [ <row>, ... ]}   (document list)
    GET  /api/events         -> text/event-stream (live lifecycle SSE)
    POST /api/blobs/fetch     -> raw blob bytes (original PDF byte fetch)

Historically these 404'd because the read+SSE surface lives in CORE, not
Relay (Relay is write-only), and at different paths:
    - Core list:   GET /api/v1/invoices  (PaginatedEnvelope, NOT {documents:[...]},
                   and — before this cutover — NOT tenant/actor scoped).
    - Core SSE:    GET /api/sse/stream    (envelope {sequence,event_type,data,...},
                   JWT-company-scoped; does NOT emit ``documents_changed``).
    - HB blobs:    POST /api/blobs/fetch  (Relay already proxies via
                   HeartBeatClient.fetch_blob).

This module bridges Core's surfaces to the EXACT shape the replicator parses
(scout_backend_http_client.py BackendDocument.from_row + _iter_sse):

  1. /api/my_documents — introspect the user JWT (the shared
     ``authenticate_request`` dispatcher), resolve company + actor + role-tier,
     call Core's list scoped to that company AND actor-visibility (flow 11),
     and map each Core invoice row onto the BackendDocument row shape
     (doc_id / creator / status / created_at / payment_status / approval_status
     / inbound_status / doc_type / original_pdf_ref / nested invoice.*).
     Empty entitled slice -> {"documents": []} (200, never 404).

  2. /api/events — proxy Core's /api/sse/stream (Bearer JWT forwarded so Core
     enforces company isolation), RE-FRAME each Core envelope into the event
     name the replicator routes (the core.* family) with Core's inner ``data``
     object inlined on the ``data:`` line (so ``data.trace_id`` /
     ``data.invoice_id`` are where the reducers look), and SYNTHESISE a
     ``documents_changed`` frame after every entitled mutation so the
     replicator re-reads /api/my_documents. Standard SSE framing; correlation
     rides on ``data.trace_id`` (the SSE ``id:`` line is discarded client-side).

  3. /api/blobs/fetch — POST {blob_ref} -> raw bytes, proxied to HB blob
     storage via HeartBeatClient.fetch_blob (the ``original_pdf_ref`` byte
     fetch the replicator does in Stage 4). ``blob_ref`` is a bearer
     capability: POST-body only, never a URL.

Auth on all three: the shared ``authenticate_request`` dispatcher (Bearer user
JWT via HB introspect, optional X-Deployment-Token already handled upstream).
The user JWT is forwarded downstream for attribution + (for SSE) Core's own
company-scope enforcement.
"""

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ...errors import ArtifactNotFoundError, ValidationFailedError

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Role tier (flow-11 actor visibility) ─────────────────────────────────────
#
# A privileged actor (admin / approver / supervisor) sees the whole company
# slice; a line user sees own/created + inbound only. We derive the tier from
# the introspected permission strings (the same permissions the dispatcher put
# on CallerContext). ``*`` (platform-admin) and any approval/admin permission
# grant the see-all tier; anything else is the restricted tier.
_SEE_ALL_PERMISSIONS = frozenset(
    {
        "*",
        "invoices.view_all",
        "invoice.view_all",
        "documents.view_all",
        "approval.approve",
        "approval.action",
        "admin",
        "tenant.admin",
    }
)


def _actor_can_see_all(ctx: CallerContext) -> bool:
    """True if this actor is entitled to the whole company slice."""
    perms = set(ctx.permissions or [])
    if perms & _SEE_ALL_PERMISSIONS:
        return True
    # Heuristic fallback: an admin/approver role often appears as a permission
    # token containing 'admin' or 'approve'.
    for p in perms:
        pl = p.lower()
        if "admin" in pl or "approve" in pl or "view_all" in pl:
            return True
    return False


def _user_jwt(request: Request, ctx: CallerContext) -> Optional[str]:
    """Extract the Bearer JWT for forwarding to Core (user path only)."""
    if not ctx.is_user:
        return None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


# ── Core invoice row -> BackendDocument row shape ────────────────────────────

def _epoch_seconds(value: Any) -> Optional[float]:
    """Coerce Core's created_at (ISO-8601 str / epoch / None) to epoch seconds.

    The replicator's BackendDocument parses ``created_at`` via _opt_float as
    epoch seconds. Core stores it as an ISO-8601 string; convert. Non-parseable
    -> None (the replicator tolerates None).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # Pure numeric string -> epoch already.
    try:
        return float(s)
    except ValueError:
        pass
    # ISO-8601 -> epoch.
    from datetime import datetime

    try:
        iso = s.replace("Z", "+00:00")
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def _str(value: Any) -> str:
    """str-coerce, treating None as empty (mirrors BackendDocument coercion)."""
    if value is None:
        return ""
    return str(value)


def map_core_invoice_to_document(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map one Core invoice list row onto the replicator's document row shape.

    Field provenance (Core INVOICE_LIST_FIELDS -> BackendDocument.from_row):
        doc_id            <- invoice_id
        creator           <- created_by | helium_user_id | user_name
        status            <- workflow_status (backend lifecycle enum)
        file_sha256       <- (not a column on invoices.invoices) -> ""
        created_at        <- created_at (ISO -> epoch seconds)
        approval_status   <- approval_status
        payment_status    <- payment_status
        inbound_status    <- inbound_status
        reversal_status   <- (not a column) -> ""
        rejection_reason  <- inbound_action_reason (best-effort) | ""
        doc_type          <- direction lowercased (outbound/inbound DIRECTION)
        original_pdf_ref  <- blob_uuid (the stored source-PDF blob key)
        invoice.*         <- seller_name/buyer_name/total_amount/tax_amount/
                             wht_amount/invoice_number/helium_invoice_no/irn
    """
    direction = _str(row.get("direction")).lower()  # OUTBOUND/INBOUND -> lower

    invoice: Dict[str, Any] = {
        "seller_name": _str(row.get("seller_name")),
        "customer_name": _str(row.get("buyer_name")),
        "buyer": _str(row.get("buyer_name")),
        "total_amount": _str(row.get("total_amount")),
        "vat_amount": _str(row.get("tax_amount")),
        "withholding_tax": _str(row.get("wht_amount")),
        "invoice_id": _str(row.get("invoice_id")),
        "invoice_number": _str(row.get("invoice_number")),
        "invoice_no": _str(row.get("invoice_number")),
        "helium_invoice_no": _str(row.get("helium_invoice_no")),
        # ``irn`` is not in the list-field subset; carried when present.
        "irn": _str(row.get("irn")),
    }

    creator = (
        row.get("created_by")
        or row.get("helium_user_id")
        or row.get("user_name")
        or ""
    )

    return {
        "doc_id": _str(row.get("invoice_id")),
        "creator": _str(creator),
        "status": _str(row.get("workflow_status")),
        "file_sha256": _str(row.get("file_sha256")),
        "created_at": _epoch_seconds(row.get("created_at")),
        "approval_status": _str(row.get("approval_status")),
        "current_user_approval_status": _str(row.get("current_user_approval_status")),
        "pending_approval_level": _str(row.get("pending_approval_level")),
        "rejection_reason": _str(row.get("inbound_action_reason")),
        "doc_type": direction,
        "inbound_status": _str(row.get("inbound_status")),
        "payment_status": _str(row.get("payment_status")),
        "reversal_status": _str(row.get("reversal_status")),
        "reversal_reason": _str(row.get("reversal_reason")),
        "original_pdf_ref": _str(row.get("blob_uuid")),
        "invoice": invoice,
    }


# ── GET /api/my_documents ────────────────────────────────────────────────────


@router.get(
    "/api/my_documents",
    summary="Reader document list — JWT-actor-scoped invoice projection",
    responses={
        200: {"description": '{"documents": [ <row>, ... ]}'},
        401: {"description": "Authentication failed"},
    },
)
async def my_documents(request: Request) -> JSONResponse:
    """Return the entitled document slice for the introspected user.

    Auth via the shared dispatcher (resolved inside the handler, never a
    Depends body param). Scoping is SERVER-SIDE: company from the JWT's tenant,
    actor-visibility from the JWT's user id + role tier. The client never
    filters. An empty slice returns ``{"documents": []}`` (200, NOT 404).
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")

    company_id = ctx.tenant_id or ""
    actor_user_id = ctx.identifier or ""
    can_see_all = _actor_can_see_all(ctx)
    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] GET /api/my_documents — company=%s actor=%s see_all=%s",
        trace_id,
        company_id,
        actor_user_id or "(none)",
        can_see_all,
    )

    core = request.app.state.core
    envelope = await core.list_invoices(
        company_id=company_id,
        actor_user_id=actor_user_id,
        actor_can_see_all=can_see_all,
        jwt_token=jwt_token,
    )

    items = envelope.get("items") if isinstance(envelope, dict) else None
    if not isinstance(items, list):
        items = []

    documents = [
        map_core_invoice_to_document(row)
        for row in items
        if isinstance(row, dict)
    ]

    return JSONResponse(content={"documents": documents})


# ── GET /api/events (SSE) ────────────────────────────────────────────────────

# Core lifecycle event families the replicator forwards to Scout reducers.
# Each of these, when seen on Core's stream for the caller's company, ALSO
# triggers a synthesised ``documents_changed`` (the replicator's primary
# re-read signal). Config-axis events pass through verbatim.
_CORE_LIFECYCLE_PREFIX = "core."


def _sse_frame(event: str, data_obj: Dict[str, Any]) -> str:
    """Render one standard SSE frame: ``event:`` + ``data:`` + blank line.

    ``data`` is a single-line JSON OBJECT (the replicator's _iter_sse requires
    json.loads(data) to be a dict; correlation rides on ``data.trace_id``).
    """
    payload = json.dumps(data_obj, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _reframe_core_envelope(
    core_event: str, envelope: Dict[str, Any]
) -> Optional[str]:
    """Translate one parsed Core SSE envelope into the replicator's frame(s).

    Core sends ``event: <core.x.y>`` with ``data:`` =
    ``{sequence, event_type, data, timestamp, source}``. The replicator routes
    on the event NAME and reads ``data`` (the WHOLE object) directly for the
    reducer fields. So we inline Core's INNER ``data`` object (which already
    carries invoice_id/company_id/trace_id/payment_status/etc) onto our
    ``data:`` line, keeping the same event name.

    For a ``core.*`` lifecycle event we ALSO append a ``documents_changed``
    frame so the replicator re-reads /api/my_documents.

    Returns the rendered SSE text (one or two frames), or None to drop the
    frame (Core control frames: connected / error / heartbeat).
    """
    inner = envelope.get("data")
    if not isinstance(inner, dict):
        inner = {}

    # Carry the event_type + sequence inside data for any .payload consumer /
    # debugging, without disturbing the inline reducer fields.
    inner.setdefault("event_type", core_event)
    if envelope.get("sequence") is not None:
        inner.setdefault("sequence", envelope.get("sequence"))

    out = _sse_frame(core_event, inner)

    if core_event.startswith(_CORE_LIFECYCLE_PREFIX):
        # Synthesise the fan-out re-read signal. The body is unused by the
        # replicator (documents_changed only triggers the re-fetch) but we
        # carry trace_id for correlation/debugging.
        dc: Dict[str, Any] = {}
        if isinstance(inner.get("trace_id"), str) and inner.get("trace_id"):
            dc["trace_id"] = inner["trace_id"]
        out += _sse_frame("documents_changed", dc)

    return out


@router.get(
    "/api/events",
    summary="Reader live event stream (SSE) — Core lifecycle + documents_changed",
    responses={
        200: {"description": "text/event-stream"},
        401: {"description": "Authentication failed"},
    },
)
async def events_stream(request: Request) -> Response:
    """Live SSE stream for the caller's tenant.

    Proxies Core's /api/sse/stream (Bearer JWT forwarded so Core enforces the
    company isolation), re-frames each Core envelope into the replicator's
    event names, and synthesises ``documents_changed`` on every entitled
    mutation. Standard text/event-stream framing.
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")
    jwt_token = _user_jwt(request, ctx)

    if not jwt_token:
        # The SSE scope is enforced by Core off the user JWT — a non-user caller
        # has no per-actor stream to subscribe to.
        raise ValidationFailedError(
            message="/api/events requires a user Bearer JWT.",
        )

    last_event_id = request.headers.get("last-event-id")
    core = request.app.state.core

    logger.info(
        "[%s] GET /api/events — company=%s actor=%s (SSE proxy of Core)",
        trace_id,
        ctx.tenant_id,
        ctx.identifier or "(none)",
    )

    async def event_gen():
        # Open framing comment so proxies flush headers immediately.
        yield ": connected\n\n"

        cur_event = "message"
        data_lines: list[str] = []

        try:
            async for line in core.open_events_stream(
                jwt_token=jwt_token,
                pattern=None,
                data_uuid=None,
                last_event_id=last_event_id,
            ):
                # WHATWG SSE line parsing on Core's stream.
                if line == "":
                    # Blank line -> dispatch the buffered Core frame.
                    if data_lines:
                        raw = "\n".join(data_lines)
                        try:
                            envelope = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            envelope = None
                        if isinstance(envelope, dict):
                            reframed = _reframe_core_envelope(cur_event, envelope)
                            if reframed:
                                yield reframed
                    cur_event = "message"
                    data_lines = []
                    continue

                if line.startswith(":"):
                    # Core keep-alive comment -> forward a keep-alive so our
                    # client's connection stays warm.
                    yield ": keepalive\n\n"
                    continue

                if line.startswith("event:"):
                    cur_event = line[len("event:"):].strip()
                    continue

                if line.startswith("data:"):
                    chunk = line[len("data:"):]
                    if chunk.startswith(" "):
                        chunk = chunk[1:]
                    data_lines.append(chunk)
                    continue

                # id: / retry: and anything else -> ignore (Core's id is not
                # relied upon; correlation is data.trace_id).
                continue
        except Exception as exc:  # noqa: BLE001 — stream end is non-fatal
            # A dropped/failed upstream stream ends our generator cleanly; the
            # replicator reconnects with bounded backoff.
            logger.info(
                "[%s] /api/events upstream ended: %s",
                trace_id,
                exc,
            )
            return

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── POST /api/blobs/fetch ────────────────────────────────────────────────────


async def _read_blob_fetch_body(request: Request) -> dict:
    """Read + parse the blob-fetch JSON body from the cached raw body."""
    raw = getattr(request.state, "raw_body", None)
    if raw is None:
        raw = await request.body()
    if not raw:
        raise ValidationFailedError(
            message="blob fetch requires a JSON body with 'blob_ref'.",
        )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationFailedError(
            message=f"Invalid blob-fetch JSON body: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailedError(
            message="blob-fetch body must be a JSON object.",
        )
    return parsed


@router.post(
    "/api/blobs/fetch",
    summary="Fetch original-PDF / blob bytes by blob_ref (POST-body)",
    responses={
        200: {"description": "Raw blob bytes"},
        400: {"description": "Invalid / missing JSON body"},
        401: {"description": "Authentication failed"},
        404: {"description": "Blob not found (ARTIFACT_NOT_FOUND)"},
    },
)
async def blobs_fetch(request: Request) -> Response:
    """Proxy raw blob bytes from HB blob storage by ``blob_ref``.

    The Stage-4 ``original_pdf_ref`` byte fetch the replicator does. ``blob_ref``
    is a bearer capability — POST-body only, never a URL. Auth via the shared
    dispatcher; the user JWT is forwarded for attribution while Relay's own s2s
    HMAC authenticates the Relay->HB hop (HeartBeatClient.fetch_blob).
    """
    ctx: CallerContext = await authenticate_request(request)
    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")
    body = await _read_blob_fetch_body(request)

    blob_ref = str(body.get("blob_ref") or body.get("original_pdf_ref") or "").strip()
    if not blob_ref:
        raise ArtifactNotFoundError(artifact_ref="")

    jwt_token = _user_jwt(request, ctx)

    logger.info(
        "[%s] POST /api/blobs/fetch — ref=%s actor=%s tenant=%s",
        trace_id,
        blob_ref[:16],
        ctx.actor_type,
        ctx.tenant_id,
    )

    heartbeat = request.app.state.heartbeat
    blob = await heartbeat.fetch_blob(artifact_ref=blob_ref, jwt_token=jwt_token)
    if not blob or not blob.get("data"):
        raise ArtifactNotFoundError(artifact_ref=blob_ref)

    data: bytes = blob["data"]
    content_type = blob.get("content_type") or "application/pdf"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "X-Relay-Blob": "true",
            "X-Relay-Blob-Ref": blob_ref,
        },
    )
