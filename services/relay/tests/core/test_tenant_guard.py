"""
Unit tests for ``src/core/tenant_guard.py`` (CSSV1 S7 R10).

Pin the default-deny rule from HB CLAUDE.md "Tenant Isolation":
matching tenants pass through, mismatches raise 403 + counter +
fire-and-forget HB audit. The rule is binding — these regressions
must stay green.

Tests use the in-memory counter store + a fake HB client that
records ``audit_log`` calls; no HTTP, no FastAPI runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from src.api.caller_context import CallerContext
from src.core.tenant_guard import tenant_guard
from src.errors import CrossTenantDeniedError
from src.observability import counters


def _ctx(
    *,
    tenant_id: str = "tenant-a",
    actor_type: str = "user",
    identifier: str = "user-001",
) -> CallerContext:
    """Build a minimal CallerContext for guard tests."""
    return CallerContext(
        actor_type=actor_type,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        identifier=identifier,
        permissions=[],
        source_id=None,
        trace_id="test-trace",
        downstream_auth_header="",
        raw_api_key="",
    )


class _FakeHeartBeat:
    """Records audit_log invocations without HTTP. Mirrors the slice
    of HeartBeatClient that tenant_guard touches."""

    def __init__(self, raise_on_audit: bool = False):
        self.audit_calls: List[Dict[str, Any]] = []
        self._raise_on_audit = raise_on_audit

    async def audit_log(
        self,
        service: str,
        event_type: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._raise_on_audit:
            raise RuntimeError("HB audit unreachable")
        self.audit_calls.append(
            {
                "service": service,
                "event_type": event_type,
                "user_id": user_id,
                "details": details or {},
            }
        )


@pytest.fixture(autouse=True)
def _isolated_counters():
    """Reset the in-memory counter store between cases so we can assert
    deltas without picking up state from neighbouring tests."""
    counters.reset()
    yield
    counters.reset()


# ── Match path (no-op) ────────────────────────────────────────────────────


class TestTenantGuardMatch:
    """Caller's tenant matches requested tenant → returns None silently."""

    @pytest.mark.asyncio
    async def test_match_returns_none(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat()

        result = await tenant_guard(
            ctx,
            requested_tenant="tenant-a",
            endpoint="/api/whatever",
            heartbeat_client=hb,
        )

        assert result is None
        assert hb.audit_calls == []

    @pytest.mark.asyncio
    async def test_match_does_not_increment_counter(self):
        ctx = _ctx(tenant_id="tenant-a")
        await tenant_guard(
            ctx,
            requested_tenant="tenant-a",
            endpoint="/api/whatever",
            heartbeat_client=_FakeHeartBeat(),
        )

        # No relay_cross_tenant_denied_total entries.
        snapshot = list(counters.get_all())
        assert all(name != "relay_cross_tenant_denied_total" for name, _, _ in snapshot)


# ── No-op pin (requested_tenant=None) ─────────────────────────────────────


class TestTenantGuardNoOpPin:
    """Endpoints with structural tenant scoping pass requested_tenant=None
    to keep the call shape uniform. Guard returns without raising."""

    @pytest.mark.asyncio
    async def test_none_requested_returns_none(self):
        ctx = _ctx(tenant_id="tenant-a")
        result = await tenant_guard(
            ctx,
            requested_tenant=None,
            endpoint="/api/duplicate/lookup",
            heartbeat_client=_FakeHeartBeat(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_none_requested_does_not_audit(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat()

        await tenant_guard(
            ctx,
            requested_tenant=None,
            endpoint="/api/duplicate/lookup",
            heartbeat_client=hb,
        )

        assert hb.audit_calls == []


# ── Mismatch path (default deny) ──────────────────────────────────────────


class TestTenantGuardMismatch:
    """Caller A → tenant B → 403 + counter + audit fire-and-forget."""

    @pytest.mark.asyncio
    async def test_mismatch_raises_cross_tenant_denied(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat()

        with pytest.raises(CrossTenantDeniedError) as excinfo:
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=hb,
            )

        # 403 status; error_code; tenant ids on the exception (NOT in body).
        assert excinfo.value.status_code == 403
        assert excinfo.value.error_code == "CROSS_TENANT_DENIED"
        assert excinfo.value.endpoint == "/api/whatever"
        assert excinfo.value.caller_tenant == "tenant-a"
        assert excinfo.value.requested_tenant == "tenant-b"

    @pytest.mark.asyncio
    async def test_mismatch_response_body_does_not_leak_tenant_ids(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat()

        with pytest.raises(CrossTenantDeniedError) as excinfo:
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=hb,
            )

        body = excinfo.value.to_dict()
        # endpoint may appear (it's stable, not tenant-identifying); the
        # tenant ids must not be in the wire body.
        body_str = repr(body)
        assert "tenant-a" not in body_str
        assert "tenant-b" not in body_str

    @pytest.mark.asyncio
    async def test_mismatch_increments_counter_with_endpoint_label(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat()

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=hb,
            )

        snapshot = list(counters.get_all())
        # exactly one matching counter row
        rows = [
            (name, labels, val)
            for name, labels, val in snapshot
            if name == "relay_cross_tenant_denied_total"
        ]
        assert len(rows) == 1
        name, labels, val = rows[0]
        assert labels == {"endpoint": "/api/whatever"}
        assert val == 1

    @pytest.mark.asyncio
    async def test_mismatch_emits_hb_audit_with_canonical_event_type(self):
        ctx = _ctx(tenant_id="tenant-a", identifier="user-007")
        hb = _FakeHeartBeat()

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=hb,
            )

        assert len(hb.audit_calls) == 1
        call = hb.audit_calls[0]
        assert call["service"] == "relay"
        assert call["event_type"] == "security.cross_tenant_denied"
        assert call["user_id"] == "user-007"
        assert call["details"]["endpoint"] == "/api/whatever"
        assert call["details"]["caller_tenant"] == "tenant-a"
        assert call["details"]["requested_tenant"] == "tenant-b"
        assert call["details"]["actor_type"] == "user"

    @pytest.mark.asyncio
    async def test_mismatch_with_no_hb_client_still_raises_and_counts(self):
        """Degraded mode: heartbeat_client=None → counter + raise still
        fire (audit is skipped). No silent skip."""
        ctx = _ctx(tenant_id="tenant-a")

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=None,
            )

        snapshot = list(counters.get_all())
        rows = [
            (name, labels, val)
            for name, labels, val in snapshot
            if name == "relay_cross_tenant_denied_total"
        ]
        assert len(rows) == 1
        assert rows[0][2] == 1

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_mask_403(self):
        """If HB audit raises, the 403 must still propagate (audit is
        fire-and-forget; never let an audit hiccup hide the rule)."""
        ctx = _ctx(tenant_id="tenant-a")
        hb = _FakeHeartBeat(raise_on_audit=True)

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/whatever",
                heartbeat_client=hb,
            )

    @pytest.mark.asyncio
    async def test_each_endpoint_label_gets_its_own_counter_row(self):
        """Two distinct endpoints fire two distinct counter rows."""
        ctx = _ctx(tenant_id="tenant-a")

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/foo",
                heartbeat_client=None,
            )
        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/bar",
                heartbeat_client=None,
            )
        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/foo",  # second hit, same endpoint
                heartbeat_client=None,
            )

        snapshot = {
            (name, tuple(sorted(labels.items()))): val
            for name, labels, val in counters.get_all()
        }
        assert (
            "relay_cross_tenant_denied_total",
            (("endpoint", "/api/foo"),),
        ) in snapshot
        assert snapshot[
            ("relay_cross_tenant_denied_total", (("endpoint", "/api/foo"),))
        ] == 2
        assert snapshot[
            ("relay_cross_tenant_denied_total", (("endpoint", "/api/bar"),))
        ] == 1


# ── Mock-backed equivalents (sanity for future refactors) ────────────────


class TestTenantGuardWithAsyncMock:
    """A lighter regression: AsyncMock as the HB stand-in. Keeps the
    test suite compatible with future refactors that might swap the
    fake for unittest.mock.AsyncMock-based fixtures."""

    @pytest.mark.asyncio
    async def test_mismatch_calls_audit_log_once(self):
        ctx = _ctx(tenant_id="tenant-a")
        hb = AsyncMock()

        with pytest.raises(CrossTenantDeniedError):
            await tenant_guard(
                ctx,
                requested_tenant="tenant-b",
                endpoint="/api/zzz",
                heartbeat_client=hb,
            )

        hb.audit_log.assert_awaited_once()
        kwargs = hb.audit_log.call_args.kwargs
        assert kwargs["event_type"] == "security.cross_tenant_denied"
