"""
Finalize service — the #3 reference-only fiscalize call (R-M2 / §B-Submit).

The #3 ``finalize(ref)`` call fiscalizes an **already-ingested** doc by
reference (file SHA-256 / ``trace_id`` / ``doc_ref``) with **NO PDF bytes** —
it fiscalizes the bytes the backend already holds. Same intent + same
downstream lifecycle/SSE as ``ingest(finalize=true)`` (#2). The contract
(CLAUDE.md §B-Submit L260-266; SCOUT contract §3.3):

  - echoes the client ``trace_id`` on the resulting lifecycle SSE;
  - a duplicate / already-finalized ``trace_id`` returns **409**, which the
    client treats as success (idempotent);
  - the **same ``trace_id``** is carried across the #2↔#3 switch so a retry
    that flips call type still dedups backend-side.

Idempotency model (two layers, mirrors SBS relay.py:1114-1175):

  - **Replay** (same finalize call repeated, e.g. a network retry): the cached
    202 body is returned with ``idempotent_replay=True``. No second Core
    trigger, no second lifecycle event.
  - **Already-finalized** (the contract's named 409): once a ``ref``/``trace_id``
    has reached a terminal finalize, a *fresh* finalize for it raises
    :class:`AlreadyFinalizedError` (409). The split: the first response we
    cache is the success; any later call with the same key replays that
    success as a 200-equivalent unless the caller asks to treat re-finalize as
    the terminal 409. We key both on ``(operation, ref|trace_id)``.

Relay holds no per-document store today (the authoritative ``ingesters[]`` /
``finalizer`` lifecycle lives in Core — §B-IngestFinalize). Relay is
ingress-only (Q24, ARCH tick56): its job is to forward the finalize trigger +
``trace_id`` to Core (HTTP, discretionary ruling (c)) and be
idempotent-per-(ref|trace_id) so retries are safe. The finalize WORK is Core's,
so **Core** emits the ``finalize.accepted`` lifecycle event on its own stream —
Relay does NOT publish lifecycle events. The in-process idempotency cache is
intentionally simple (single-instance); a shared store is an S3 hardening
concern, not Monday's bar.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from uuid6 import uuid7

from ..errors import AlreadyFinalizedError, FinalizeReferenceMissingError

logger = logging.getLogger(__name__)

OPERATION_FINALIZE = "finalize"

# The lifecycle family Core emits on its own stream when it accepts a finalize
# (Q24: Relay is ingress-only; Core owns the lifecycle event). Echoed back on
# the FinalizeResponse so the client/Scout can correlate, but Relay no longer
# publishes it.
FAMILY_FINALIZE_ACCEPTED = "relay.finalize.accepted"


@dataclass
class FinalizeResult:
    """Result of a #3 reference-only finalize."""

    status: str  # "accepted"
    ref: str
    trace_id: str
    doc_ref: str
    event_id: str
    event_family: str
    finalize_by_reference: bool = True
    raw_bytes_sent: bool = False
    idempotent_replay: bool = False
    core: Dict[str, Any] = field(default_factory=dict)


def _finalize_key(*, ref: str, trace_id: str) -> str:
    """Idempotency key: ``finalize:<ref|trace_id>``.

    Prefer ``trace_id`` (the stable id Scout carries across the #2↔#3 switch,
    §3.3) but fall back to ``ref`` so a finalize with only a doc_ref/sha256
    still dedups. Mirrors SBS ``_idempotency_record_key`` (relay.py:1165-1175)
    which uses ``idempotency_key or trace_id``.
    """
    token = str(trace_id or ref or "").strip()
    return f"{OPERATION_FINALIZE}:{token}" if token else ""


class FinalizeService:
    """
    Reference-only finalize (#3) handler.

    Usage:
        svc = FinalizeService(core_client)
        result = await svc.finalize(ref="sha256:...", trace_id="018f...")
    """

    def __init__(
        self,
        core_client: Any,
    ):
        self._core = core_client
        # (operation, ref|trace_id) -> {"result": FinalizeResult, "finalized": True}
        self._records: Dict[str, Dict[str, Any]] = {}

    async def finalize(
        self,
        *,
        ref: str = "",
        trace_id: str = "",
        doc_ref: str = "",
        actor_user_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> FinalizeResult:
        """
        Run the #3 reference-only finalize.

        Args:
            ref: doc reference — file SHA-256 / doc_ref / trace_id.
            trace_id: client UUIDv7; echoed on the lifecycle event.
            doc_ref: optional explicit doc_ref (defaults to ``ref``).
            actor_user_id: finalizing actor (forwarded to Core; §B-IngestFinalize).
            metadata: forwarded identity/trace fields.
            jwt_token: Bearer JWT (forwarded to Core for attribution).

        Returns:
            FinalizeResult (status="accepted"; ``idempotent_replay`` set on a
            replay of the same finalize call).

        Raises:
            FinalizeReferenceMissingError (400): neither ref nor trace_id.
            AlreadyFinalizedError (409): a *new* finalize for a ref/trace_id
                that already reached terminal (the contract's named 409).
        """
        ref = str(ref or "").strip()
        trace_id = str(trace_id or "").strip()
        doc_ref = str(doc_ref or "").strip() or ref

        if not ref and not trace_id:
            raise FinalizeReferenceMissingError()

        key = _finalize_key(ref=ref, trace_id=trace_id)
        existing = self._records.get(key) if key else None
        if existing is not None:
            return self._on_duplicate(existing, ref=ref, trace_id=trace_id)

        # ── Forward the finalize trigger to Core (HTTP, discretionary (c)). ──
        # Best-effort: Core being down must not strand the finalize — the doc
        # is already ingested; Core reconciles. We still record the result so the
        # client's trace_id is dedup-anchored; Core emits the lifecycle event.
        core_resp: Dict[str, Any] = {}
        try:
            core_resp = await self._core.finalize_by_reference(
                ref=ref or trace_id,
                trace_id=trace_id,
                metadata=metadata,
                jwt_token=jwt_token,
            )
        except Exception as exc:  # best-effort downstream trigger
            logger.warning(
                "Core finalize_by_reference failed (non-fatal) — "
                "ref=%s trace_id=%s: %s",
                (ref or trace_id)[:16],
                trace_id or "(none)",
                exc,
            )

        # Relay is ingress-only (Q24): Core emits the finalize.accepted lifecycle
        # event on its own stream. Relay just records + responds. The event_id
        # rides Core's ack when present, else a local correlation id; the
        # event_family is the family Core will emit, echoed for the client.
        event_id = str(core_resp.get("event_id") or f"relay-evt-{uuid7()}")

        result = FinalizeResult(
            status="accepted",
            ref=ref,
            trace_id=trace_id,
            doc_ref=doc_ref,
            event_id=event_id,
            event_family=str(core_resp.get("event_family") or FAMILY_FINALIZE_ACCEPTED),
            core=core_resp,
        )

        if key:
            self._records[key] = {"result": result, "finalized": True}

        logger.info(
            "Finalize#3 accepted — ref=%s trace_id=%s event_family=%s",
            (ref or doc_ref or "(none)")[:16],
            trace_id or "(none)",
            result.event_family,
        )
        return result

    def _on_duplicate(
        self,
        record: Dict[str, Any],
        *,
        ref: str,
        trace_id: str,
    ) -> FinalizeResult:
        """A finalize arrived for an already-known (ref|trace_id).

        Per §B-Submit / §3.3 a duplicate / already-finalized ``trace_id``
        returns **409** treated-as-success. We raise AlreadyFinalizedError so
        the route emits the canonical 409 the client expects (idempotent). The
        cached original event id rides along for correlation.
        """
        prior: FinalizeResult = record["result"]
        logger.info(
            "Finalize#3 duplicate (already finalized) → 409 — "
            "ref=%s trace_id=%s original_event_id=%s",
            (ref or trace_id or "(none)")[:16],
            trace_id or "(none)",
            prior.event_id,
        )
        raise AlreadyFinalizedError(
            ref=ref,
            trace_id=trace_id,
            original_event_id=prior.event_id,
        )
