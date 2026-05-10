"""
POST /api/duplicate/lookup — CSSV1 S7 (R5).

Reader-/Scout-facing preflight that asks "have you seen this file?"
BEFORE the caller uploads bytes, to avoid bandwidth waste on duplicates.
UI-blocking call, so the latency budget is **~10 ms p99** end-to-end on
the Redis-direct happy path.

Spec: ``RELAY_PHASE1_DESIGN_ALIGNMENT_2026_05_09 §4.5`` + the F2R surface
table in ``RELAY_ARCH_HANDOFF_CSSV1_2026_05_10 §2.4``.

Auth: combined dispatcher (``deps.authenticate_request``) — both JWT
(user) and HMAC (ERP) callers are accepted. Caller's tenant comes from
``CallerContext.tenant_id``.

Response shape: identical to ``/api/ingest``'s side-response when content
matches. Frontend code path stays the same regardless of which endpoint
surfaced the duplicate. Field shape locked across (a) Phase 1 ingest
side-response, (b) Phase 1 R5 preflight, (c) future Phase 2+ Frontdoor
consumer.

3-tier degrade:
    Primary    → Redis ``check_duplicate(tenant_id, file_hash)`` —
                 tenant-keyed; sub-ms p99.
    Fallback   → HB ``check_duplicate(file_hash)`` (existing
                 ``POST /api/dedup/check`` HMAC s2s call).
    Degraded   → Allow (return ``is_duplicate=false``) when both
                 Redis + HB are unreachable.

Pure preflight — does NOT call HB ``/api/blobs/register`` or write to
``core_queue``. The cache write-back from ``/api/ingest`` happy path
lands in CSSV1 S4 (R7); until then, the Redis tier essentially always
misses and the HB fallback is the working path.

Initiator masking (Phase 1 default — capture in tests):
    - Original creator inside the same tenant → real ``user_id`` +
      ``display_name``.
    - Same-tenant non-creator → masked (``"[hidden]"``). Owner/Admin
      bypass is deferred to Phase 2+ per ``ROLES_PERMISSIONS_MATRIX
      §2.1`` open question.
    - Cross-tenant probes are structurally invisible: the Redis key is
      tenant-scoped and the HB call returns no match for a foreign
      hash. ``is_duplicate=false`` is the only possible response.

HB enrichment dependency:
    HB's ``check_duplicate`` returns only ``{is_duplicate, file_hash,
    original_queue_id}``. The R5 response shape promises additional
    fields (``matched_batch_display_id``, ``original_received_at``,
    ``original_processed_at``, ``initiator``). Those land via a
    follow-up HB chip — for S7 they return ``None`` on HB-fallback
    hits, marked with ``# TODO(R5-phase2)`` in the merge code.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from ..caller_context import CallerContext
from ..deps import authenticate_request
from ...core.tenant_guard import tenant_guard
from ...observability import counters

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class DuplicateLookupRequest(BaseModel):
    """
    Preflight request body.

    ``file_hash`` is SHA-256 hex digest, lowercase, 64 chars (validated).
    ``file_size`` is optional and used purely for collision diagnostic
    logging — the lookup itself does not gate on size.
    """

    file_hash: str = Field(
        ..., description="SHA-256 hex digest of file bytes (64 lowercase chars)."
    )
    file_size: Optional[int] = Field(
        default=None,
        ge=0,
        description="File size in bytes (optional; collision diagnostic only).",
    )

    @field_validator("file_hash")
    @classmethod
    def _hash_must_be_sha256_hex(cls, v: str) -> str:
        if not _SHA256_HEX.match(v):
            raise ValueError(
                "file_hash must be a 64-char lowercase SHA-256 hex digest"
            )
        return v


class DuplicateInitiator(BaseModel):
    """
    Identity of the original uploader. Masked to ``"[hidden]"`` for
    non-creator callers within the same tenant. Cross-tenant callers
    never see this object (lookup returns ``is_duplicate=false``).
    """

    user_id: str
    display_name: str


class DuplicateLookupResponse(BaseModel):
    """
    Same shape as the ``duplicate`` side-response on ``/api/ingest``.
    Frontend code path is identical regardless of which endpoint
    surfaced the match.
    """

    is_duplicate: bool
    matched_batch_display_id: Optional[str] = None
    original_received_at: Optional[str] = None
    original_processed_at: Optional[str] = None
    basis: str = Field(
        default="file_hash",
        description="Lookup basis. Constant 'file_hash' for R5 (vs future batch_hash / hlx_hash).",
    )
    initiator: Optional[DuplicateInitiator] = None


# ── Helpers ───────────────────────────────────────────────────────────────


_MASKED_INITIATOR = DuplicateInitiator(
    user_id="[hidden]",
    display_name="[hidden]",
)


def _miss_response() -> DuplicateLookupResponse:
    """Standard not-a-duplicate response."""
    return DuplicateLookupResponse(is_duplicate=False)


def _enrich_from_hb(
    hb_response: dict,
    *,
    caller_ctx: CallerContext,
) -> DuplicateLookupResponse:
    """
    Translate HB ``check_duplicate`` payload into the R5 response shape.

    HB currently returns ``{is_duplicate, file_hash, original_queue_id}``.
    The richer fields (``matched_batch_display_id``,
    ``original_received_at``, ``original_processed_at``, ``initiator``)
    are not yet exposed by HB — they land via a follow-up chip. For S7
    we return ``None`` for the missing fields.

    Phase-1 visibility: when HB does not surface the original-uploader
    identity, we cannot determine creator-vs-non-creator and default to
    masked initiator. Callers in the same tenant who later receive the
    enriched HB response will get full identity.
    """
    if not hb_response.get("is_duplicate"):
        return _miss_response()

    # TODO(R5-phase2): when HB surfaces matched_batch_display_id /
    # original_received_at / original_processed_at / uploader identity,
    # pass them through here and replace _MASKED_INITIATOR with the
    # real identity for same-tenant creator callers.
    return DuplicateLookupResponse(
        is_duplicate=True,
        matched_batch_display_id=None,
        original_received_at=None,
        original_processed_at=None,
        basis="file_hash",
        initiator=_MASKED_INITIATOR,
    )


def _coerce_cached(
    cached: dict,
) -> DuplicateLookupResponse:
    """
    Translate a Redis-cached dict into the R5 response shape.

    Redis stores the side-response as JSON written by S4 (R7) — until
    that lands, this path is exercised only by tests. Defensively
    accepts missing optional fields so a partial cache entry written by
    an older Relay version doesn't crash the lookup.
    """
    return DuplicateLookupResponse(
        is_duplicate=bool(cached.get("is_duplicate", False)),
        matched_batch_display_id=cached.get("matched_batch_display_id"),
        original_received_at=cached.get("original_received_at"),
        original_processed_at=cached.get("original_processed_at"),
        basis=cached.get("basis", "file_hash"),
        initiator=(
            DuplicateInitiator(**cached["initiator"])
            if isinstance(cached.get("initiator"), dict)
            else None
        ),
    )


# ── Route ─────────────────────────────────────────────────────────────────


@router.post(
    "/api/duplicate/lookup",
    response_model=DuplicateLookupResponse,
    summary="Preflight duplicate check (Reader/Scout) — CSSV1 R5",
    responses={
        401: {"description": "Authentication failed"},
        403: {"description": "Cross-tenant denied"},
        422: {"description": "Validation failed (file_hash not SHA-256 hex)"},
    },
)
async def duplicate_lookup(
    body: DuplicateLookupRequest,
    request: Request,
    ctx: CallerContext = Depends(authenticate_request),
) -> DuplicateLookupResponse:
    """
    Preflight — has this file_hash been seen before by Relay?

    Pure read. No bytes uploaded; no blob register; no Core trigger.

    Tenant scoping is structural: Redis key includes ``ctx.tenant_id``
    so cross-tenant probes return 'not present' without leaving Relay.
    The HB fallback uses HB's authoritative cross-tenant view.
    """
    trace_id = ctx.trace_id or getattr(request.state, "trace_id", "")

    # tenant_guard no-op pin: R5 has no tenant body field — its scoping
    # is enforced at the Redis key + HB call level. Pin keeps the call
    # shape uniform with future tenant-identified endpoints.
    await tenant_guard(
        ctx,
        requested_tenant=None,
        endpoint="/api/duplicate/lookup",
        heartbeat_client=getattr(request.app.state, "heartbeat", None),
    )

    redis_client = getattr(request.app.state, "redis", None)
    heartbeat_client = getattr(request.app.state, "heartbeat", None)

    # Collision diagnostic: log file_size on the request for the very
    # rare case we later see two distinct files claim the same hash.
    if body.file_size is not None:
        logger.debug(
            f"[{trace_id}] /api/duplicate/lookup hash={body.file_hash[:12]}... "
            f"size={body.file_size} tenant={ctx.tenant_id}",
        )
    else:
        logger.debug(
            f"[{trace_id}] /api/duplicate/lookup hash={body.file_hash[:12]}... "
            f"tenant={ctx.tenant_id}",
        )

    # ── Tier 1: Redis (primary, ~ms) ─────────────────────────────────
    redis_available = bool(
        redis_client is not None and getattr(redis_client, "is_available", False)
    )

    if redis_available:
        cached = await redis_client.check_duplicate(
            tenant_id=ctx.tenant_id,
            file_hash=body.file_hash,
        )
        if cached is not None:
            counters.inc(
                "relay_duplicate_lookup_total",
                labels={"result": "hit_redis"},
            )
            return _coerce_cached(cached)
        # Redis returned None — could be true miss OR an exception that
        # flipped is_available off. Re-read the flag to decide which
        # branch to take next.
        if not getattr(redis_client, "is_available", False):
            redis_available = False  # fall through to HB fallback

    # ── Tier 2: HB fallback ──────────────────────────────────────────
    if heartbeat_client is None:
        # Both tiers down (no HB client at all — degraded boot path).
        counters.inc(
            "relay_duplicate_lookup_total",
            labels={"result": "both_down"},
        )
        logger.warning(
            f"[{trace_id}] /api/duplicate/lookup degraded — no HB client; "
            f"returning miss",
        )
        return _miss_response()

    try:
        hb_response = await heartbeat_client.check_duplicate(body.file_hash)
    except Exception as e:
        # HB unreachable (Redis was already down or returned miss). Per
        # the spec's "data safety > rate limiting" tradeoff, return miss
        # rather than 5xx so Reader can still upload.
        if redis_available:
            # Redis was up + missed; only HB is down.
            counters.inc(
                "relay_duplicate_lookup_total",
                labels={"result": "miss"},
            )
            logger.warning(
                f"[{trace_id}] /api/duplicate/lookup — Redis miss + HB down "
                f"({e}); returning miss",
            )
        else:
            counters.inc(
                "relay_duplicate_lookup_total",
                labels={"result": "both_down"},
            )
            logger.warning(
                f"[{trace_id}] /api/duplicate/lookup degraded — Redis down + "
                f"HB down ({e}); returning miss",
            )
        return _miss_response()

    if hb_response.get("is_duplicate"):
        counters.inc(
            "relay_duplicate_lookup_total",
            labels={"result": "hit_hb_fallback"},
        )
        return _enrich_from_hb(hb_response, caller_ctx=ctx)

    # Authoritative miss.
    if redis_available:
        # Redis up + missed → counter says "miss" (the lookup completed
        # cleanly through the primary tier).
        counters.inc(
            "relay_duplicate_lookup_total",
            labels={"result": "miss"},
        )
    else:
        # Redis was down; HB authoritative miss.
        counters.inc(
            "relay_duplicate_lookup_total",
            labels={"result": "redis_down"},
        )
    return _miss_response()
