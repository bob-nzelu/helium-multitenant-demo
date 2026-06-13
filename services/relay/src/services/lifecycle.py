"""
Lifecycle event publisher seam (R-M2 / §B-EventLog).

Relay emits a small slice of lifecycle events — for Monday:
``relay.finalize.accepted`` (§B-Submit / SBS ``simulate_finalize``,
scout_backend_simulator.py:1567-1581) and ``core.preview.available`` (Q4 — the
bulk ingest path no longer blocks on / returns Core's preview inline; it emits
this event when the preview becomes available so Scout reacts asynchronously).
The contract MUST is the **trace_id echo**: a non-empty ``trace_id`` supplied
by the client is carried onto the lifecycle event so the downstream SSE that
confirms the action echoes it back (events SBS ``_build_sse_frame`` lifts
``data.trace_id`` to top-level ``frame["trace_id"]``, events.py:530-532).
Scout's reducer keys the optimistic / batch row off that ``trace_id``.

Topology note (ARCH Open Q15 unresolved — see debt-map §B-EventLog):
- Relay does **NOT** host an SSE server. Scout connects to **Core's** SSE
  stream (memory "Scout as SSE Driver"); the confirming
  ``relay.finalize.accepted`` / ``core.artifact.hlx_available`` /
  ``core.submission.terminal`` frames flow over Core's stream.
- So Relay's Monday obligation is narrow: **forward the lifecycle trigger +
  the client ``trace_id`` to Core** so Core's stream echoes it.
- Transport for Monday = **HTTP via the existing CoreClient** (discretionary
  ARCH ruling (c), debt-map Open Q (c): least-new-plumbing). AMQP-first stays
  the S3 hardening contract and is deferred.

The ``LifecyclePublisher`` Protocol is the seam that keeps the sink swappable:
the default :class:`CoreLifecyclePublisher` publishes via ``CoreClient``; a test
or a future AMQP/SSE host can drop in another implementation without touching
the finalize handler. ``app.state.lifecycle_publisher`` holds the active one.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Family constants — single source of truth for the lifecycle slice Relay emits.
#
# Relay-originated finalize confirmation (§B-Submit). ``relay.*`` prefix.
FAMILY_FINALIZE_ACCEPTED = "relay.finalize.accepted"
#
# Bulk-preview readiness (Q4). ``core.*`` prefix because the preview itself is
# produced by Core and the event rides **Core's** stream (Q15 two-stream).
# Relay merely *publishes* the trigger frame via the CoreClient seam — same
# topology as ``relay.finalize.accepted``. The plain handle in the ARCH ruling
# is "preview_available"; the wire family follows the SBS ``core.<noun>.<state>``
# shape (cf. ``core.submission.<status>`` / ``hlx.available`` for "an output is
# now fetchable", scout_backend_simulator_events.py). Both ``core``/``relay``
# prefixes are recognised by the 8-slug event gate (KNOWN_EVENT_PREFIXES,
# owned by Core/HB).
FAMILY_PREVIEW_AVAILABLE = "core.preview.available"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LifecycleEvent:
    """A lifecycle event Relay hands to the publisher sink.

    Mirrors the SBS frame shape (``family`` + ``data`` + a top-level
    ``trace_id`` echo when present) so a Core/SSE sink can forward it
    verbatim. ``raw_bytes_in_event`` is always ``False`` — lifecycle events
    never carry artifact bytes (§B-IngestFinalize artifact-payload boundary).
    """

    family: str
    trace_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_frame(self) -> Dict[str, Any]:
        """Render the wire frame. ``trace_id`` is lifted to top-level when
        non-empty, exactly like the events SBS (events.py:530-532)."""
        payload = dict(self.data)
        payload.setdefault("trace_id", self.trace_id)
        payload.setdefault("raw_bytes_in_event", False)
        frame: Dict[str, Any] = {
            "family": self.family,
            "event": self.family,  # back-compat alias (older Scout consumers)
            "source": "relay",
            "timestamp": _now_iso(),
            "data": payload,
        }
        trace = str(self.trace_id or payload.get("trace_id") or "").strip()
        if trace:
            frame["trace_id"] = trace
        return frame


@runtime_checkable
class LifecyclePublisher(Protocol):
    """The swappable sink for Relay-originated lifecycle events.

    Implementations forward the event to wherever the confirming SSE is
    hosted (Core for Monday). MUST be best-effort from the caller's point of
    view: a publish failure must not fail the finalize HTTP response (the
    document is already accepted; the SSE echo is a downstream concern).
    """

    async def publish(self, event: LifecycleEvent) -> bool:
        """Publish one lifecycle event. Returns True on success, False on a
        swallowed failure. Never raises for transport problems."""
        ...


class CoreLifecyclePublisher:
    """Default publisher — forwards lifecycle events to Core over HTTP.

    Discretionary ARCH decision (c): Relay→Core transport for Monday is HTTP
    via the existing :class:`CoreClient`, NOT AMQP. The CoreClient method is a
    stub today (canned dict); when Core stands up the real
    ``POST /api/lifecycle/event`` (NEEDS-CORE), only the client body changes —
    this seam and the finalize handler stay put.
    """

    def __init__(self, core_client: Any):
        self._core = core_client

    async def publish(self, event: LifecycleEvent) -> bool:
        frame = event.to_frame()
        try:
            await self._core.publish_lifecycle_event(frame)
            logger.info(
                "Lifecycle event published to Core — family=%s trace_id=%s",
                event.family,
                event.trace_id or "(none)",
            )
            return True
        except Exception as exc:  # best-effort: never fail the request on this
            logger.warning(
                "Lifecycle publish to Core failed (non-fatal) — "
                "family=%s trace_id=%s: %s",
                event.family,
                event.trace_id or "(none)",
                exc,
            )
            return False


class RecordingLifecyclePublisher:
    """In-memory publisher for tests / local dev.

    Records every published event so tests can assert
    ``relay.finalize.accepted`` was emitted with the right ``trace_id`` echo
    without standing up a Core sink. Also usable as a dev no-network default.
    """

    def __init__(self) -> None:
        self.events: list[LifecycleEvent] = []
        self.frames: list[Dict[str, Any]] = []

    async def publish(self, event: LifecycleEvent) -> bool:
        self.events.append(event)
        self.frames.append(event.to_frame())
        return True

    def last(self) -> Optional[LifecycleEvent]:
        return self.events[-1] if self.events else None
