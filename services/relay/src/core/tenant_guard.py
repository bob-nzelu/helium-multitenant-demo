"""
Tenant-isolation guard — CSSV1 S7 (R10).

Enforces HeartBeat's "Tenant Isolation — Default Deny" rule on Relay's
edge. The rule lives at ``helium-services-phase3/CLAUDE.md`` (the
"Tenant Isolation — Default Deny" section, locked 2026-05-10 in
``Documentation/CSSV1_ALIGNMENT_2026_05_10.md`` §1.3) — read it before
modifying this module.

The binding rule (verbatim):

    Every read filters by caller's `tenant_id`. Every write uses
    tenant-scoped identifiers. Cross-tenant attempts are 403 + a
    `security_events` row + a Prometheus counter. Not a 404; not a
    silent skip — make abuse visible.

Caller's tenant comes from the dispatcher (``src/api/deps.py``) which
sets ``CallerContext.tenant_id`` from the JWT claim (user path), the
``api_key_secrets`` registry (HMAC ERP path), or the matching api_key
(service-creds path). On a cross-tenant attempt, ``tenant_guard`` does
three things in this order:

    1. Increment ``relay_cross_tenant_denied_total{endpoint}``.
    2. Fire HB audit event ``security.cross_tenant_denied`` —
       fire-and-forget, NEVER raises out of the audit call. HB's audit
       writer fans out to ``security_events`` per its CLAUDE.md
       "dual-fire" rule (Relay just emits the single event; HB owns
       the dual-write).
    3. Raise :class:`CrossTenantDeniedError` (HTTP 403). The response
       body intentionally does NOT echo tenant ids.

Usage on every Relay route that takes a tenant-identified resource::

    from ..core.tenant_guard import tenant_guard

    @router.post("/api/whatever")
    async def whatever(
        body: WhateverRequest,
        request: Request,
        ctx: CallerContext = Depends(authenticate_request),
    ):
        await tenant_guard(
            ctx,
            requested_tenant=body.tenant_id,
            endpoint="/api/whatever",
            heartbeat_client=request.app.state.heartbeat,
        )
        ...  # handler body

Every NEW Relay route that takes a ``batch_display_id``, ``tenant_id``,
or other tenant-identified body field MUST call ``tenant_guard``
before doing any work. ``requested_tenant=None`` is a no-op pin that
keeps the call shape uniform for endpoints whose tenant scoping is
structural (e.g. R5's Redis key + HB call already carry the caller's
tenant) — the guard returns without raising in that case.

Out of scope for this helper:
    - Service-creds ``on_behalf_of.tenant_id`` body-field enforcement.
      Flagged in CSSV1 arch notes; not in S7. The guard reads from
      ``CallerContext.tenant_id`` only.
    - Permission-slug gating. The guard is identity-only; slug checks
      live in the handler.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..api.caller_context import CallerContext
from ..errors import CrossTenantDeniedError
from ..observability import counters

logger = logging.getLogger(__name__)


# Audit event type — matches the dual-fire signal HB's audit writer
# fans out to security_events on (per HB CLAUDE.md "Cross-tenant admin
# tooling: explicit gates only" point 3).
_CROSS_TENANT_DENIED_EVENT = "security.cross_tenant_denied"


async def tenant_guard(
    ctx: CallerContext,
    requested_tenant: Optional[str],
    endpoint: str,
    heartbeat_client: Optional[Any] = None,
) -> None:
    """
    Verify caller's tenant matches the requested tenant. Raise on mismatch.

    Args:
        ctx: Resolved CallerContext from ``authenticate_request``. The
            caller's tenant comes from ``ctx.tenant_id``.
        requested_tenant: The tenant id the request body is asking
            Relay to operate on. ``None`` means the endpoint resolved
            no explicit tenant scope from the body — the call is a
            no-op pin (see module docstring).
        endpoint: Stable endpoint label for the counter + audit event
            (e.g. ``"/api/duplicate/lookup"``). Avoid tenant ids and
            other high-cardinality values.
        heartbeat_client: Optional ``HeartBeatClient`` for audit
            emission. When ``None`` (degraded mode / unit-test
            isolation), audit emission is skipped — the counter +
            raise still fire.

    Returns:
        ``None`` on match (or ``requested_tenant=None``).

    Raises:
        CrossTenantDeniedError: 403 on mismatch. Caller's response body
            does NOT echo tenant ids.
    """
    # No-op pin: endpoints with structural tenant scoping (R5's
    # tenant-keyed Redis lookup, /api/ingest's tenant-tagged blobs) pass
    # ``requested_tenant=None`` to keep the call shape uniform.
    if requested_tenant is None:
        return

    if ctx.tenant_id == requested_tenant:
        return

    # Mismatch — fire counter, audit, then raise. Order matters: counter
    # increments deterministically before audit (which can be async) so
    # alarms still fire even if HB is unreachable.
    counters.inc(
        "relay_cross_tenant_denied_total",
        labels={"endpoint": endpoint},
    )

    logger.warning(
        "Cross-tenant denial — endpoint=%s caller_tenant=%s "
        "requested_tenant=%s actor=%s identifier=%s",
        endpoint,
        ctx.tenant_id,
        requested_tenant,
        ctx.actor_type,
        ctx.identifier,
        extra={"trace_id": ctx.trace_id},
    )

    if heartbeat_client is not None:
        try:
            await heartbeat_client.audit_log(
                service="relay",
                event_type=_CROSS_TENANT_DENIED_EVENT,
                user_id=ctx.identifier,
                details={
                    "endpoint": endpoint,
                    "actor_type": ctx.actor_type,
                    "caller_tenant": ctx.tenant_id,
                    "requested_tenant": requested_tenant,
                },
            )
        except Exception as e:
            # audit_log is fire-and-forget per its own contract, but
            # belt-and-braces: never let an audit hiccup mask the 403.
            logger.warning(
                "tenant_guard audit emission failed (non-critical): %s",
                e,
                extra={"trace_id": ctx.trace_id},
            )

    raise CrossTenantDeniedError(
        endpoint=endpoint,
        caller_tenant=ctx.tenant_id,
        requested_tenant=requested_tenant,
    )


__all__ = ["tenant_guard"]
