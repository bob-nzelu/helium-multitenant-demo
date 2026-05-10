"""
In-memory Prometheus-style counters for Relay.

Phase 1 stub equivalent — process-local counters that the ``/metrics``
route renders alongside the existing service-info gauges. Lightweight
on purpose: the ``prometheus_client`` library is a fine drop-in later
but we don't need it for the alarming-counter shape CSSV1 §R9 demands.

Public API
----------

``inc(name, labels=None)``
    Increment the counter ``name`` by 1. ``labels`` is an optional
    ``{label_key: label_value}`` dict; the ``(name, sorted-labels)``
    tuple is the unique counter identity. Idempotent across processes
    (each Relay container has its own dict; ops aggregates at the
    Prometheus scrape layer).

``get_all() -> Iterable[Tuple[str, Dict[str, str], int]]``
    Yield every ``(name, labels, value)`` tuple currently tracked.
    Used by the ``/metrics`` route to render exposition lines.

``reset()``
    Wipe all counters. Test-only helper. Not exposed via the route.

Counter naming follows the Prometheus convention
``relay_{namespace}_total`` for monotonically-increasing counters
(see https://prometheus.io/docs/practices/naming/). Pre-declare the
HELP + TYPE strings in :data:`COUNTER_HELP` so the exposition has
those lines even when the counter has zero samples.

Currently tracked counters (CSSV1 R9 + future R-chips):

- ``relay_bearer_removed_received_total{endpoint}`` — incremented
  whenever a Relay outbound call to HB receives ``401`` with
  ``error_code="BEARER_S2S_REMOVED"``. Any non-zero rate after Phase 0
  catchup means a code path is still sending the dead Bearer s2s
  form; ops alarms.
- ``relay_introspect_cache_total{result}`` — incremented on every
  call to :meth:`IntrospectClient.introspect`. ``result`` is one of
  ``hit`` (served from cache), ``miss`` (cache empty/expired, called
  HB), ``bypass`` (caller passed ``X-Bypass-Auth-Cache: true``), or
  ``no_jti`` (token had no ``jti`` claim, can't cache — fell through
  to HB). Used to size HB's introspect QPS reduction (CSSV1 S1
  chip 2/2).
- ``relay_amqp_publish_total{routing_key,result}`` — placeholder for
  CSSV1 R2 (state-only AMQP producer).
- ``relay_lock_acquire_duration_seconds`` — placeholder for CSSV1 R8.
- ``relay_status_orchestration_duration_seconds`` — placeholder for
  CSSV1 R4.

The placeholders are listed in :data:`COUNTER_HELP` so the exposition
is forward-compatible; their ``inc()`` callsites land in the chips
that wire them.
"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Iterable, Mapping, Optional, Tuple


# Map of (name, sorted-label-tuple) → integer count.
_COUNTERS: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], int] = {}
_LOCK = Lock()


# Pre-declared HELP + TYPE metadata so the exposition has the lines
# even when the counter hasn't fired yet. The /metrics route reads
# this dict to emit ``# HELP`` / ``# TYPE`` blocks for every known
# counter, then iterates :func:`get_all` for the sample lines.
COUNTER_HELP: Dict[str, Tuple[str, str]] = {
    # name: (help_text, prometheus_type)
    "relay_bearer_removed_received_total": (
        "Count of HB responses with 401 BEARER_S2S_REMOVED received by Relay. "
        "Non-zero rate = a Relay code path is still sending Bearer s2s.",
        "counter",
    ),
    "relay_introspect_cache_total": (
        "Count of IntrospectClient.introspect() calls labelled by cache "
        "outcome: hit (served from cache), miss (called HB then cached), "
        "bypass (X-Bypass-Auth-Cache:true skipped cache), or no_jti "
        "(token lacked jti claim, fell through to HB).",
        "counter",
    ),
    "relay_amqp_publish_total": (
        "Count of Relay AMQP publishes to core_queue, labeled by routing_key + result.",
        "counter",
    ),
    "relay_lock_acquire_duration_seconds": (
        "Histogram of Relay → HB batch_lock acquire durations (placeholder).",
        "counter",  # placeholder counter; flips to histogram in CSSV1 R8
    ),
    "relay_status_orchestration_duration_seconds": (
        "Histogram of Relay /api/status orchestration durations (placeholder).",
        "counter",  # placeholder counter; flips to histogram in CSSV1 R4
    ),
}


def _normalise_labels(labels: Optional[Mapping[str, str]]) -> Tuple[Tuple[str, str], ...]:
    """Sort-and-tuple the label dict so equivalent dicts hash to the same key."""
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, labels: Optional[Mapping[str, str]] = None) -> None:
    """Increment counter ``name`` (with optional ``labels``) by 1."""
    key = (name, _normalise_labels(labels))
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + 1


def get_all() -> Iterable[Tuple[str, Dict[str, str], int]]:
    """Yield every tracked counter's ``(name, labels_dict, value)`` snapshot."""
    with _LOCK:
        snapshot = list(_COUNTERS.items())
    for (name, labels_tuple), value in snapshot:
        yield name, dict(labels_tuple), value


def reset() -> None:
    """Wipe all counters. Test-only helper."""
    with _LOCK:
        _COUNTERS.clear()


__all__ = ["inc", "get_all", "reset", "COUNTER_HELP"]
