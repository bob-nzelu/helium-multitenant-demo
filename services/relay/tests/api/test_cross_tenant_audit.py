"""
Integration regression for CSSV1 S7 (R10) — "caller from tenant A
cannot see tenant B data."

Per HB CLAUDE.md "Tenant Isolation — Default Deny", every Relay
endpoint that takes a tenant-identified resource MUST refuse with
403 + counter + audit when caller_tenant != requested_tenant. R5's
``/api/duplicate/lookup`` is the only existing call site in this
chip; future endpoints (R3 withdraw, R4 status, R8 lock) add their
own regression here.

R5's tenant scoping is structural (Redis key + HB call carry the
caller's tenant), so a literal "different tenant in body" payload
isn't possible today — instead we exercise the guard helper directly
through the dispatcher on a synthetic mismatch context. This pins
the "default-deny" contract independent of which endpoint owns the
body shape.

When R3/R4/R8 land, each one adds a class here that posts a body
with a foreign tenant id and asserts the same 403 + counter + audit
trio.
"""

from __future__ import annotations

import pytest

from src.api.caller_context import CallerContext
from src.core.tenant_guard import tenant_guard
from src.errors import CrossTenantDeniedError
from src.observability import counters


# ── Fixtures ──────────────────────────────────────────────────────────────


class _RecordingHeartBeat:
    """Records audit_log calls without HTTP."""

    def __init__(self):
        self.calls = []

    async def audit_log(
        self,
        service,
        event_type,
        user_id=None,
        details=None,
    ):
        self.calls.append({
            "service": service,
            "event_type": event_type,
            "user_id": user_id,
            "details": details or {},
        })


@pytest.fixture(autouse=True)
def _isolated_counters():
    counters.reset()
    yield
    counters.reset()


def _ctx(tenant_id: str, identifier: str = "user-001") -> CallerContext:
    return CallerContext(
        actor_type="user",
        tenant_id=tenant_id,
        identifier=identifier,
        permissions=[],
        source_id=None,
        trace_id="t",
        downstream_auth_header="",
        raw_api_key="",
    )


# ── Default-deny regression (per future endpoint) ────────────────────────


class TestCrossTenantDenialFiresOnEveryEndpoint:
    """Caller from tenant A → tenant B resource on each Relay endpoint
    that takes a tenant-identified body field. This test class is a
    honeypot: every new tenant-aware endpoint adds a method here."""

    @pytest.mark.asyncio
    async def test_duplicate_lookup_uses_no_op_pin_no_403_for_self(self):
        """R5 is structurally scoped (Redis + HB tenant lookup) so the
        guard call is a no-op pin — caller's same-tenant action does
        NOT trip the guard. This pin protects against an over-eager
        future change accidentally wiring a non-None requested_tenant
        on R5 and breaking happy-path Reader calls."""
        ctx = _ctx(tenant_id="tenant-a")
        hb = _RecordingHeartBeat()

        # Mirrors duplicate.py's call:
        await tenant_guard(
            ctx,
            requested_tenant=None,
            endpoint="/api/duplicate/lookup",
            heartbeat_client=hb,
        )

        # No counter, no audit.
        assert hb.calls == []
        snapshot = list(counters.get_all())
        assert all(name != "relay_cross_tenant_denied_total" for name, _, _ in snapshot)

    @pytest.mark.asyncio
    async def test_synthetic_endpoint_a_to_b_denied_403_counter_audit(self):
        """A future tenant-aware endpoint (synthesised here) — when
        caller A asks for tenant B, tenant_guard MUST 403 + count +
        audit. Pin against an arbitrary endpoint label so the contract
        stays stable as new endpoints adopt it."""
        ctx = _ctx(tenant_id="tenant-a", identifier="user-aaa")
        hb = _RecordingHeartBeat()

        with pytest.raises(CrossTenantDeniedError) as excinfo:
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/_synthetic/withdraw",
                heartbeat_client=hb,
            )

        # 403 + canonical error code
        assert excinfo.value.status_code == 403
        assert excinfo.value.error_code == "CROSS_TENANT_DENIED"

        # Counter
        rows = [
            (n, l, v) for n, l, v in counters.get_all()
            if n == "relay_cross_tenant_denied_total"
        ]
        assert len(rows) == 1
        assert rows[0][1] == {"endpoint": "/api/_synthetic/withdraw"}
        assert rows[0][2] == 1

        # Audit fire-and-forget
        assert len(hb.calls) == 1
        call = hb.calls[0]
        assert call["service"] == "relay"
        assert call["event_type"] == "security.cross_tenant_denied"
        assert call["user_id"] == "user-aaa"
        assert call["details"]["caller_tenant"] == "tenant-a"
        assert call["details"]["requested_tenant"] == "tenant-b"

    @pytest.mark.asyncio
    async def test_response_body_does_not_leak_tenant_ids(self):
        """The 403 response body must NOT echo caller_tenant or
        requested_tenant — that would leak tenant existence to a
        cross-tenant probe. Tenant ids stay on the exception object
        for audit/counter use only."""
        ctx = _ctx(tenant_id="tenant-a")
        hb = _RecordingHeartBeat()

        with pytest.raises(CrossTenantDeniedError) as excinfo:
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/_synthetic/x",
                heartbeat_client=hb,
            )

        body_repr = repr(excinfo.value.to_dict())
        assert "tenant-a" not in body_repr
        assert "tenant-b" not in body_repr

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_mask_403(self):
        """Belt-and-braces: even if HB audit raises, the 403 still
        propagates. Cross-tenant denial NEVER becomes a silent skip."""

        class _BoomHB:
            async def audit_log(self, *args, **kwargs):
                raise RuntimeError("HB down")

        ctx = _ctx(tenant_id="tenant-a")

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/_synthetic/y",
                heartbeat_client=_BoomHB(),
            )

        # Counter still fired despite audit failure.
        rows = [
            (n, l, v) for n, l, v in counters.get_all()
            if n == "relay_cross_tenant_denied_total"
        ]
        assert len(rows) == 1
        assert rows[0][2] == 1


# ── Counter HELP registration ────────────────────────────────────────────


class TestCrossTenantCounterRegistered:
    """The /metrics route renders HELP/TYPE for every counter known to
    COUNTER_HELP. Pin that the cross-tenant counter is registered so
    ops sees it at zero samples even before the first denial."""

    def test_counter_help_registered(self):
        from src.observability.counters import COUNTER_HELP

        assert "relay_cross_tenant_denied_total" in COUNTER_HELP
        help_text, prom_type = COUNTER_HELP["relay_cross_tenant_denied_total"]
        assert help_text  # non-empty
        assert prom_type == "counter"

    def test_duplicate_lookup_counter_help_registered(self):
        """R5 counter sibling — same registration discipline."""
        from src.observability.counters import COUNTER_HELP

        assert "relay_duplicate_lookup_total" in COUNTER_HELP
        help_text, prom_type = COUNTER_HELP["relay_duplicate_lookup_total"]
        assert help_text
        assert prom_type == "counter"
