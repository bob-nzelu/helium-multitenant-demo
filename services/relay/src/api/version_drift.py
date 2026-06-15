"""
Version-drift gateway guard (§B-Drift / §B-VersionAxes).

Implements the Relay obligation from the Scout repo CLAUDE.md
"Backend Debt Notes":

    §B-Drift: Relay MUST act as the front-door gateway for every sensitive
    mutating API call and check the incoming request's version axes against its
    authoritative current values BEFORE forwarding to Core/Edge/FIRS. On a stale
    axis it MUST return HTTP 409 with body
    ``{"code": "version_drift", "axis": <axis>, "expected": <expected>, "got": <got>}``
    and NOT forward the request (no backend side effects).

    §B-VersionAxes: the contract covers FIVE state axes, not just
    ``policy_revision`` — see the canonical header map below.

This module is the executable mirror of the SBS spec at
``scout_backend_simulator_relay.py`` (``_drift_response_if_needed``,
``_drift_response``, ``_normalise_axis_name``). It is a reusable FastAPI
dependency mounted on the sensitive mutating routes ``/api/ingest`` and
``/api/finalize``.

CANON (ratified Q17 — five axes, ``X-Helium-*`` wire headers)
-------------------------------------------------------------
The wire contract is the five ``X-Helium-*`` request headers below. The older
SBS ``policy_revision`` / colon-named / bare-name spellings are a MOCK-only
artefact (SBS impersonates Relay today); they are NOT the wire contract and are
deliberately NOT accepted here. The header→axis map is configurable (a
module-level dict, overridable via :func:`make_version_drift_guard`) so a
≤cosmetic rename from HB-S3/S4 lands in one place.

| Wire header                           | Axis                  |
|---------------------------------------|-----------------------|
| ``X-Helium-Policy-Revision``          | ``policy_revision``   |
| ``X-Helium-License-State``            | ``license_state_id``  |
| ``X-Helium-Usage-State``              | ``usage_state_id``    |
| ``X-Helium-Auth-Policy-Revision``     | ``auth_policy_revision`` |
| ``X-Helium-User-Permissions-Revision``| ``user_permissions`` (composite) |

The composite ``user_permissions`` axis expands server-side to
``user_permissions:<user_id>`` where ``<user_id>`` is the caller's JWT-derived
identity (``CallerContext.identifier`` for the user actor) — NOT a sibling
request header. A non-user caller (HMAC/ERP, service creds) has no user
identity, so the composite axis is skipped for them.

Authoritative values + the NEEDS-HB skip
-----------------------------------------
Authoritative axis values come from a *pluggable accessor*
(:func:`_config_cache_axis_value`) reading ``app.state.config_cache``. Today the
cache only tracks ``policy_revision``; the accessor probes each axis key
generically (top-level then under ``tenant``) so axes the HB config fabric
starts feeding (``license_state_id`` / ``usage_state_id`` /
``auth_policy_revision``) light up automatically with no code change. An axis
whose authoritative value is unknown returns ``None`` and is SKIPPED — it cannot
drift until HB feeds it.

NEEDS-HB: HB-S3/S4 must (a) feed ``license_state_id`` / ``usage_state_id`` /
``auth_policy_revision`` (and the per-user permissions revision) into the
tenant config Relay caches, and (b) confirm the final ``X-Helium-*`` header
spellings. Expect at most a cosmetic rename of the header map.

Pass-through rules (mirror SBS):
  * Absent axis headers always pass through.
  * A supplied axis whose authoritative value is unknown passes through (skip).
  * Drift (409) is raised only for a SUPPLIED axis that mismatches a KNOWN
    authoritative value — and is raised BEFORE the handler body runs, so the
    request is never forwarded (no backend side effects).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Mapping, Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from .caller_context import CallerContext
from .deps import authenticate_request

logger = logging.getLogger(__name__)


# ── Canonical header → axis map (CANON: Q17 five ``X-Helium-*`` axes) ──────
#
# Keys are lowercase (HTTP header lookups are case-insensitive; Starlette's
# ``request.headers`` is a case-insensitive multidict, but we lowercase on both
# sides to be explicit). Configurable so a ≤cosmetic HB-S3/S4 rename lands in
# one place (NEEDS-HB).
DEFAULT_HEADER_AXIS_MAP: Dict[str, str] = {
    "x-helium-policy-revision": "policy_revision",
    "x-helium-license-state": "license_state_id",
    "x-helium-usage-state": "usage_state_id",
    "x-helium-auth-policy-revision": "auth_policy_revision",
    # Composite per-user permissions axis. The supplied value is the
    # permissions revision; the axis name in the 409 body becomes
    # ``user_permissions:<user_id>`` where <user_id> is derived SERVER-SIDE
    # from the caller's JWT identity (CallerContext.identifier), matching SBS
    # ``relay_axis_headers_for_current_state`` → ``user_permissions:<user_id>``.
    "x-helium-user-permissions-revision": "user_permissions",
}

# The composite axis name as it appears in the header map (pre-expansion).
_COMPOSITE_USER_PERMISSIONS_AXIS = "user_permissions"


class VersionDriftError(Exception):
    """
    Raised by the guard when a supplied version axis is stale.

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


def _config_cache_axis_value(config_cache: Any, axis: str) -> Optional[str]:
    """
    Pluggable authoritative-value accessor backed by ``ConfigCache``.

    Returns the authoritative current value for ``axis``, or ``None`` when the
    axis is not yet tracked (→ caller SKIPS it; NEEDS-HB).

    The lookup is generic so axes the HB config fabric starts feeding light up
    with no code change:
      1. top-level cached config key (``config_cache.get(axis)``);
      2. the same key nested under the ``tenant`` section.

    Today only ``policy_revision`` is present in the cached config shape, so the
    other first-class axes (``license_state_id`` / ``usage_state_id`` /
    ``auth_policy_revision``) and the composite ``user_permissions:<uid>`` axis
    resolve to ``None`` until HB feeds them (NEEDS-HB).
    """
    if config_cache is None:
        return None

    # The composite per-user axis (``user_permissions:<uid>``) is not in the
    # cached tenant config shape yet — it needs a per-user revision fabric from
    # HB. Skip it explicitly so we don't accidentally read a top-level
    # ``user_permissions`` blob and mis-compare.
    if axis.startswith(f"{_COMPOSITE_USER_PERMISSIONS_AXIS}:"):
        return None

    # Top-level first (wherever HB chooses to surface the axis), then nested
    # under ``tenant``. ``ConfigCache.get`` reads the raw cached dict.
    value = config_cache.get(axis, None) if hasattr(config_cache, "get") else None
    if value is None and hasattr(config_cache, "get_tenant"):
        tenant = config_cache.get_tenant() or {}
        value = tenant.get(axis)
    return None if value is None else str(value)


def evaluate_version_drift(
    supplied_headers: Mapping[str, str],
    *,
    axis_value_accessor: Callable[[str], Optional[str]],
    header_axis_map: Optional[Mapping[str, str]] = None,
    user_id: str = "",
) -> Optional[VersionDriftError]:
    """
    Pure drift evaluation (no Request/app coupling — directly unit-testable).

    Walks the configured header map in order; for every header PRESENT in
    ``supplied_headers`` it resolves the axis and, when the axis has a KNOWN
    authoritative value, compares supplied-vs-expected. Returns a
    :class:`VersionDriftError` for the first mismatch, else ``None``.

    Mirror of SBS ``_drift_response_if_needed`` (sans the forced-drift demo
    primer, which is SBS-only test scaffolding).

    * ``axis_value_accessor(axis)`` returns the authoritative value or ``None``
      (skip — unknown/untracked axis; NEEDS-HB).
    * The composite ``user_permissions`` header is expanded to
      ``user_permissions:<user_id>`` using ``user_id`` (the caller's JWT-derived
      identity). Without a user id the composite axis is skipped — a non-user
      caller has no per-user permissions axis.

    Iteration is over ``header_axis_map`` (deterministic insertion order) rather
    than ``supplied_headers`` so "first mismatch wins" is stable regardless of
    request header ordering.
    """
    mapping = header_axis_map if header_axis_map is not None else DEFAULT_HEADER_AXIS_MAP
    # Normalise supplied header keys to lowercase once for case-insensitive lookup.
    supplied_lower = {str(k).lower(): str(v) for k, v in supplied_headers.items()}

    for header_name, axis in mapping.items():
        if header_name.lower() not in supplied_lower:
            continue
        got = supplied_lower[header_name.lower()]

        if axis == _COMPOSITE_USER_PERMISSIONS_AXIS:
            if not user_id:
                # No JWT-derived user identity → can't form the composite axis.
                continue
            axis = f"{_COMPOSITE_USER_PERMISSIONS_AXIS}:{user_id}"

        expected = axis_value_accessor(axis)
        if expected is None:
            # Unknown / untracked authoritative value → cannot drift (NEEDS-HB).
            continue

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

    The returned dependency depends on :func:`authenticate_request` so it
    receives the resolved :class:`CallerContext`. FastAPI dedupes the shared
    ``authenticate_request`` dependency, so on ``/api/ingest`` (where the
    handler also depends on it) auth resolves exactly once and the SAME context
    is shared. The user-permissions axis derives ``<user_id>`` from
    ``ctx.identifier`` (JWT-derived) for the user actor only.

    ``axis_value_accessor`` signature is ``(config_cache, axis) -> str | None``;
    defaults to :func:`_config_cache_axis_value`. This is the seam HB-S3/S4
    plugs into once it feeds the extra axes — no call-site change required.
    """
    resolved_map = header_axis_map if header_axis_map is not None else DEFAULT_HEADER_AXIS_MAP
    resolved_accessor = axis_value_accessor or _config_cache_axis_value

    async def _guard(
        request: Request,
        ctx: CallerContext = Depends(authenticate_request),
    ) -> None:
        supplied = _supplied_axis_headers(request, resolved_map)
        if not supplied:
            # No version axes on the request → nothing to check, pass through.
            return

        config_cache = getattr(request.app.state, "config_cache", None)

        # Composite ``user_permissions`` axis: the user id is the caller's
        # JWT-derived identity. Only the user actor has one; HMAC/ERP and
        # service callers have no per-user permissions axis (→ skipped).
        user_id = ctx.identifier if (ctx is not None and ctx.is_user) else ""

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


# Default guard instance for ordinary call sites. Mount as a route dependency:
#   @router.post("/api/ingest", dependencies=[Depends(version_drift_guard)])
# or add it to the route's ``dependencies=[...]`` list in the include.
version_drift_guard = make_version_drift_guard()
