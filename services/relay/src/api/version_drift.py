"""
Version-drift gateway guard (§B-Drift / §B-VersionAxes).

Implements the Relay obligation from CLAUDE.md "Backend Debt Notes":

    §B-Drift: Relay MUST act as the front-door gateway for every sensitive
    mutating API call and check the incoming request's version axes against its
    authoritative current values BEFORE forwarding to Core/Edge/FIRS. On a stale
    axis it MUST return HTTP 409 with body
    ``{"code": "version_drift", "axis": <axis>, "expected": <expected>, "got": <got>}``
    and NOT forward the request (no backend side effects).

This module is the executable mirror of the SBS spec at
``scout_backend_simulator_relay.py`` (``_drift_response_if_needed``,
``_drift_response``, ``_normalise_axis_name``). It is a reusable FastAPI
dependency so any sensitive mutating route can adopt it. ``/api/ingest`` adopts
it today; R-M2's future ``POST /api/finalize`` should ``Depends(version_drift_guard)``
the same way.

Design notes (surfaced for ARCH):
  * The header→axis map is *configurable* (a module-level dict, overridable per
    request via the factory) so the canonical wire spelling can be re-pinned
    once ARCH rules Open Q (a) without touching call sites.
  * Authoritative axis values come from a *pluggable accessor*
    (``axis_value_accessor``) that reads ``app.state.config_cache``. Today only
    ``policy_revision`` is tracked; axes the cache does not yet expose
    (``license_state_id`` / ``auth_policy_revision`` / ``usage_state_id``)
    return ``None`` and are SKIPPED — they cannot drift until the HB-2 config
    fabric feeds them (NEEDS-HB). A supplied header for an unknown-authoritative
    axis passes through rather than hard-failing, exactly like SBS
    (``expected is None`` → ``continue``).
  * Absent axis headers always pass through. Drift is only raised for a
    SUPPLIED axis whose value mismatches a KNOWN authoritative value.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Canonical header → axis map (ARCH Open Q (a) pending) ──────────────────
#
# Configurable so the canonical wire spelling can be re-pinned in one place.
# Mirrors the SBS first-class axes plus the composite per-user axis. The 4th
# first-class axis follows SBS (``usage_state_id``); CLAUDE.md §B-VersionAxes
# names it ``user_permissions`` (composite-only in SBS) — flagged to ARCH.
DEFAULT_HEADER_AXIS_MAP: Dict[str, str] = {
    "x-policy-revision": "policy_revision",
    "x-license-state-id": "license_state_id",
    "x-auth-policy-revision": "auth_policy_revision",
    "x-usage-state-id": "usage_state_id",
    # Composite per-user permissions axis: ``user_permissions:<uid>``. The
    # value is the permissions revision; the ``<uid>`` is carried in a sibling
    # header so the axis name in the 409 body is composite, matching SBS
    # (``relay_axis_headers_for_current_state`` → ``user_permissions:<user_id>``).
    "x-user-permissions": "user_permissions",
}

# Header that carries the user id for the composite ``user_permissions`` axis.
USER_ID_HEADER = "x-user-permissions-user-id"

# SBS ``_normalise_axis_name`` alias table (after lowercase + strip ``x-`` +
# strip ``-revision`` + ``-``→``_``). Kept verbatim so an axis name arriving in
# any accepted spelling collapses to the same canonical axis the SBS uses.
_AXIS_ALIASES: Dict[str, str] = {
    "policy": "policy_revision",
    "policy_revision": "policy_revision",
    "license_state": "license_state_id",
    "license_state_id": "license_state_id",
    "auth_policy": "auth_policy_revision",
    "auth_policy_revision": "auth_policy_revision",
    "usage": "usage_state_id",
    "usage_state": "usage_state_id",
    "usage_state_id": "usage_state_id",
}

# First-class axis names that are already canonical (pass through untouched).
_CANONICAL_AXES = frozenset(
    {
        "policy_revision",
        "license_state_id",
        "auth_policy_revision",
        "usage_state_id",
    }
)


class VersionDriftError(Exception):
    """
    Raised by :func:`version_drift_guard` when a supplied version axis is stale.

    Deliberately NOT a subclass of :class:`src.errors.RelayError`: the
    §B-Drift contract pins the response body to the EXACT shape
    ``{"code": "version_drift", "axis", "expected", "got"}`` with no
    ``status``/``error_code``/``message`` wrapper. ``RelayError.to_dict()``
    produces a different shape, so this error carries its own body and is
    rendered verbatim by :func:`version_drift_error_handler`.
    """

    status_code = 409

    def __init__(self, *, axis: str, expected: str, got: str):
        self.axis = axis
        self.expected = expected
        self.got = got
        super().__init__(f"version_drift on axis={axis}: expected={expected} got={got}")

    def to_body(self) -> Dict[str, str]:
        """Exact 409 body per §B-Drift (mirror of SBS ``_drift_response``)."""
        return {
            "code": "version_drift",
            "axis": self.axis,
            "expected": self.expected,
            "got": self.got,
        }


async def version_drift_error_handler(
    request: Request, exc: VersionDriftError
) -> JSONResponse:
    """Render :class:`VersionDriftError` as the verbatim 409 §B-Drift body."""
    logger.info(
        "[%s] version_drift axis=%s expected=%s got=%s",
        getattr(request.state, "trace_id", "unknown"),
        exc.axis,
        exc.expected,
        exc.got,
    )
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


def normalise_axis_name(
    name: str,
    *,
    header_axis_map: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Collapse a request-header name to its canonical version axis.

    Mirror of SBS ``_normalise_axis_name`` plus the configurable header map:

      1. An already-canonical axis (``policy_revision`` …) or a composite
         ``user_permissions:<uid>`` passes through untouched.
      2. Otherwise lowercase, strip a leading ``x-``, strip a trailing
         ``-revision``, swap ``-``→``_``, then alias-map.

    The configurable ``header_axis_map`` is consulted FIRST (on the raw
    lowercased header) so canonical wire spellings can be re-pinned in one
    place; the SBS alias table is the permissive fallback.

    Returns ``""`` when the header does not map to a known axis.
    """
    raw = str(name or "").strip()
    if raw in _CANONICAL_AXES:
        return raw
    if raw.startswith("user_permissions:"):
        return raw

    lowered = raw.lower()
    mapping = header_axis_map if header_axis_map is not None else DEFAULT_HEADER_AXIS_MAP
    mapped = mapping.get(lowered)
    if mapped:
        return mapped

    header = lowered.removeprefix("x-").removesuffix("-revision")
    header = header.replace("-", "_")
    return _AXIS_ALIASES.get(header, "")


def _config_cache_axis_value(config_cache: Any, axis: str) -> Optional[str]:
    """
    Pluggable authoritative-value accessor backed by ``ConfigCache``.

    Returns the authoritative current value for ``axis``, or ``None`` when the
    axis is not yet tracked (→ caller SKIPS it; NEEDS-HB).

    Today only ``policy_revision`` is resolvable: it is read from the cached HB
    tenant config (top-level ``policy_revision``, falling back to
    ``tenant.policy_revision``). The other first-class axes
    (``license_state_id`` / ``auth_policy_revision`` / ``usage_state_id``) and
    the composite ``user_permissions:<uid>`` axis are not in the cached config
    shape yet, so they resolve to ``None`` until HB feeds them.
    """
    if config_cache is None:
        return None

    if axis == "policy_revision":
        # Top-level first, then nested under ``tenant`` — wherever HB chooses to
        # surface it. ``ConfigCache.get`` reads the raw cached dict.
        value = config_cache.get("policy_revision", None)
        if value is None:
            tenant = config_cache.get_tenant() if hasattr(config_cache, "get_tenant") else {}
            value = (tenant or {}).get("policy_revision")
        return None if value is None else str(value)

    # NEEDS-HB: license_state_id / auth_policy_revision / usage_state_id /
    # user_permissions:<uid> are not in the cached config yet. Skip them.
    return None


def evaluate_version_drift(
    supplied_headers: Mapping[str, str],
    *,
    axis_value_accessor: Callable[[str], Optional[str]],
    header_axis_map: Optional[Mapping[str, str]] = None,
    user_id: str = "",
) -> Optional[VersionDriftError]:
    """
    Pure drift evaluation (no Request/app coupling — directly unit-testable).

    Walks the supplied headers, normalises each to a canonical axis, and for
    every axis with a KNOWN authoritative value compares supplied-vs-expected.
    Returns a :class:`VersionDriftError` for the first mismatch, else ``None``.

    Mirror of SBS ``_drift_response_if_needed`` (sans the forced-drift demo
    primer, which is SBS-only test scaffolding).

    * ``axis_value_accessor(axis)`` returns the authoritative value or ``None``
      (skip — unknown/untracked axis).
    * The composite ``user_permissions`` header is expanded to
      ``user_permissions:<user_id>`` using ``user_id`` (from
      :data:`USER_ID_HEADER`); without a user id the composite axis is skipped.
    """
    for raw_name, raw_value in supplied_headers.items():
        axis = normalise_axis_name(raw_name, header_axis_map=header_axis_map)
        if not axis:
            continue
        if axis == "user_permissions":
            if not user_id:
                # Can't form the composite axis without a user id — skip.
                continue
            axis = f"user_permissions:{user_id}"

        expected = axis_value_accessor(axis)
        if expected is None:
            # Unknown / untracked authoritative value → cannot drift (NEEDS-HB).
            continue

        got = str(raw_value)
        if got != str(expected):
            return VersionDriftError(axis=axis, expected=str(expected), got=got)
    return None


def _supplied_axis_headers(
    request: Request,
    header_axis_map: Mapping[str, str],
) -> Dict[str, str]:
    """Collect the raw axis headers present on the request (lowercase keys)."""
    out: Dict[str, str] = {}
    for header_name in header_axis_map:
        value = request.headers.get(header_name)
        if value is not None:
            out[header_name] = value
    return out


def make_version_drift_guard(
    *,
    header_axis_map: Optional[Mapping[str, str]] = None,
    axis_value_accessor: Optional[
        Callable[[Any, str], Optional[str]]
    ] = None,
):
    """
    Build a ``version_drift_guard`` dependency, optionally with a re-pinned
    header map or a custom authoritative-value accessor.

    ``axis_value_accessor`` signature is ``(config_cache, axis) -> str | None``;
    defaults to :func:`_config_cache_axis_value`. This is the seam HB-2 plugs
    into once it feeds the extra axes — no call-site change required.
    """
    resolved_map = header_axis_map if header_axis_map is not None else DEFAULT_HEADER_AXIS_MAP
    resolved_accessor = axis_value_accessor or _config_cache_axis_value

    async def _guard(request: Request) -> None:
        config_cache = getattr(request.app.state, "config_cache", None)
        supplied = _supplied_axis_headers(request, resolved_map)
        if not supplied:
            # No version axes on the request → nothing to check, pass through.
            return

        user_id = request.headers.get(USER_ID_HEADER, "") or ""

        def _accessor(axis: str) -> Optional[str]:
            return resolved_accessor(config_cache, axis)

        drift = evaluate_version_drift(
            supplied,
            axis_value_accessor=_accessor,
            header_axis_map=resolved_map,
            user_id=user_id,
        )
        if drift is not None:
            # Raise BEFORE the handler runs — request is NOT forwarded, so no
            # backend side effects (§B-Drift "no stale-policy commit").
            raise drift

    return _guard


# Default guard instance for ordinary call sites:
#   ``ctx = Depends(authenticate_request)``  +  ``_ = Depends(version_drift_guard)``
version_drift_guard = make_version_drift_guard()
