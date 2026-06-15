"""
Tests for the §B-Drift / §B-VersionAxes version-drift gateway guard.

Covered:
  * Exact 409 body per axis on a supplied-axis mismatch.
  * Pass-through when axes match.
  * Pass-through when axis headers are absent.
  * Pass-through when the authoritative value is unknown (NEEDS-HB skip).
  * The five canonical ``X-Helium-*`` headers map to the right axes.
  * The guard runs BEFORE the handler — a drifted request produces NO handler
    side effect and is NOT forwarded.
  * Composite ``user_permissions:<uid>`` axis with the uid derived from the
    JWT-resolved CallerContext (not a sibling header); skipped for non-user
    callers.
  * ``policy_revision`` read from ConfigCache (top-level + nested under tenant).
  * Wiring: ``/api/ingest`` and ``/api/finalize`` carry the guard; the app
    registers the VersionDriftError handler.

The guard is exercised both as a pure function (``evaluate_version_drift``) and
through a real FastAPI app with the guard mounted on a sentinel route. The app
tests override ``authenticate_request`` with a fixed CallerContext so we can
prove ordering/no-side-effect (and the JWT-derived user id) without signing
multipart-HMAC requests.
"""

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.caller_context import CallerContext
from src.api.deps import authenticate_request
from src.api.version_drift import (
    DEFAULT_HEADER_AXIS_MAP,
    VersionDriftError,
    _config_cache_axis_value,
    evaluate_version_drift,
    make_version_drift_guard,
    version_drift_error_handler,
    version_drift_guard,
)


# ── Pure drift evaluation ──────────────────────────────────────────────────


def _accessor_for(values: dict):
    def _acc(axis: str):
        return values.get(axis)

    return _acc


class TestEvaluateVersionDrift:
    def test_match_passes(self):
        result = evaluate_version_drift(
            {"X-Helium-Policy-Revision": "v5"},
            axis_value_accessor=_accessor_for({"policy_revision": "v5"}),
        )
        assert result is None

    def test_absent_headers_pass(self):
        result = evaluate_version_drift(
            {},
            axis_value_accessor=_accessor_for({"policy_revision": "v5"}),
        )
        assert result is None

    def test_mismatch_raises_with_exact_fields(self):
        result = evaluate_version_drift(
            {"X-Helium-Policy-Revision": "v4"},
            axis_value_accessor=_accessor_for({"policy_revision": "v5"}),
        )
        assert isinstance(result, VersionDriftError)
        assert result.axis == "policy_revision"
        assert result.expected == "v5"
        assert result.got == "v4"
        assert result.to_body() == {
            "code": "version_drift",
            "axis": "policy_revision",
            "expected": "v5",
            "got": "v4",
        }

    def test_header_lookup_is_case_insensitive(self):
        # lowercased wire header still resolves to the policy axis
        result = evaluate_version_drift(
            {"x-helium-policy-revision": "v4"},
            axis_value_accessor=_accessor_for({"policy_revision": "v5"}),
        )
        assert result is not None
        assert result.axis == "policy_revision"

    def test_unknown_authoritative_value_is_skipped(self):
        # license supplied but the accessor returns None → NEEDS-HB skip
        result = evaluate_version_drift(
            {"X-Helium-License-State": "lic-9"},
            axis_value_accessor=_accessor_for({}),  # everything unknown
        )
        assert result is None

    @pytest.mark.parametrize(
        "header,axis,expected,got",
        [
            ("X-Helium-Policy-Revision", "policy_revision", "p2", "p1"),
            ("X-Helium-License-State", "license_state_id", "lic-2", "lic-1"),
            ("X-Helium-Usage-State", "usage_state_id", "use-2", "use-1"),
            (
                "X-Helium-Auth-Policy-Revision",
                "auth_policy_revision",
                "auth-2",
                "auth-1",
            ),
        ],
    )
    def test_each_first_class_axis_drifts_with_exact_body(
        self, header, axis, expected, got
    ):
        result = evaluate_version_drift(
            {header: got},
            axis_value_accessor=_accessor_for({axis: expected}),
        )
        assert result is not None
        assert result.to_body() == {
            "code": "version_drift",
            "axis": axis,
            "expected": expected,
            "got": got,
        }

    def test_composite_user_permissions_drift(self):
        result = evaluate_version_drift(
            {"X-Helium-User-Permissions-Revision": "perm-1"},
            axis_value_accessor=_accessor_for({"user_permissions:u-7": "perm-2"}),
            user_id="u-7",
        )
        assert result is not None
        assert result.axis == "user_permissions:u-7"
        assert result.expected == "perm-2"
        assert result.got == "perm-1"

    def test_composite_user_permissions_without_user_id_is_skipped(self):
        # No JWT-derived user id (e.g. HMAC/ERP caller) → composite axis skipped.
        result = evaluate_version_drift(
            {"X-Helium-User-Permissions-Revision": "perm-1"},
            axis_value_accessor=_accessor_for({"user_permissions:u-7": "perm-2"}),
            user_id="",
        )
        assert result is None

    def test_first_mismatch_wins_in_map_order(self):
        # Both drift; policy is iterated first in DEFAULT_HEADER_AXIS_MAP order,
        # regardless of supplied-header insertion order.
        result = evaluate_version_drift(
            {
                "X-Helium-License-State": "lic-1",
                "X-Helium-Policy-Revision": "p1",
            },
            axis_value_accessor=_accessor_for(
                {"policy_revision": "p2", "license_state_id": "lic-2"}
            ),
        )
        assert result is not None
        assert result.axis == "policy_revision"


# ── Canonical header map shape ─────────────────────────────────────────────


class TestCanonicalHeaderMap:
    def test_default_map_has_exactly_five_axes(self):
        assert DEFAULT_HEADER_AXIS_MAP == {
            "x-helium-policy-revision": "policy_revision",
            "x-helium-license-state": "license_state_id",
            "x-helium-usage-state": "usage_state_id",
            "x-helium-auth-policy-revision": "auth_policy_revision",
            "x-helium-user-permissions-revision": "user_permissions",
        }

    def test_no_legacy_or_colon_named_headers(self):
        # SBS bare-name / colon-named scheme must NOT leak into the wire map.
        for key in DEFAULT_HEADER_AXIS_MAP:
            assert key.startswith("x-helium-")
            assert ":" not in key


# ── Guard dependency through a real app (ordering / no side effect) ────────


class _Spy:
    """Records whether the protected handler body ran (a 'side effect')."""

    def __init__(self):
        self.handler_ran = False


def _make_guarded_app(
    authoritative: dict,
    spy: _Spy,
    *,
    ctx: CallerContext,
) -> FastAPI:
    """
    Build a minimal app with the REAL guard mounted on a sentinel POST route,
    backed by an in-memory authoritative-value accessor. ``authenticate_request``
    is overridden to return ``ctx`` so the guard sees a fixed JWT-derived
    identity without HMAC/JWT plumbing.
    """
    guard = make_version_drift_guard(
        axis_value_accessor=lambda _cache, axis: authoritative.get(axis)
    )
    app = FastAPI()
    app.add_exception_handler(VersionDriftError, version_drift_error_handler)

    @app.post("/sentinel", dependencies=[Depends(guard)])
    async def sentinel(request: Request):  # noqa: ANN201
        spy.handler_ran = True
        return {"ok": True}

    app.dependency_overrides[authenticate_request] = lambda: ctx
    return app


_USER_CTX = CallerContext(
    actor_type="user",
    tenant_id="t-1",
    identifier="u-7",
    permissions=["*"],
)
_ERP_CTX = CallerContext(
    actor_type="erp",
    tenant_id="t-1",
    identifier="api-key-xyz",
    permissions=["blob.write"],
)


@pytest.fixture
async def guarded_client_factory():
    clients = []

    async def _factory(
        authoritative: dict,
        spy: _Spy,
        *,
        ctx: CallerContext = _USER_CTX,
    ) -> AsyncClient:
        app = _make_guarded_app(authoritative, spy, ctx=ctx)
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        clients.append(client)
        return client

    yield _factory
    for c in clients:
        await c.aclose()


class TestGuardThroughApp:
    @pytest.mark.asyncio
    async def test_match_forwards_to_handler(self, guarded_client_factory):
        spy = _Spy()
        client = await guarded_client_factory({"policy_revision": "v5"}, spy)
        resp = await client.post(
            "/sentinel", headers={"X-Helium-Policy-Revision": "v5"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert spy.handler_ran is True

    @pytest.mark.asyncio
    async def test_absent_axes_forward_to_handler(self, guarded_client_factory):
        spy = _Spy()
        client = await guarded_client_factory({"policy_revision": "v5"}, spy)
        resp = await client.post("/sentinel")
        assert resp.status_code == 200
        assert spy.handler_ran is True

    @pytest.mark.asyncio
    async def test_drift_returns_exact_409_and_does_not_run_handler(
        self, guarded_client_factory
    ):
        spy = _Spy()
        client = await guarded_client_factory({"policy_revision": "v5"}, spy)
        resp = await client.post(
            "/sentinel", headers={"X-Helium-Policy-Revision": "v4"}
        )
        assert resp.status_code == 409
        assert resp.json() == {
            "code": "version_drift",
            "axis": "policy_revision",
            "expected": "v5",
            "got": "v4",
        }
        # The decisive assertion: guard ran BEFORE the handler — no side effect,
        # request NOT forwarded.
        assert spy.handler_ran is False

    @pytest.mark.asyncio
    async def test_unknown_axis_value_forwards(self, guarded_client_factory):
        # authoritative has nothing → license axis supplied but value unknown →
        # skip (NEEDS-HB), request forwards.
        spy = _Spy()
        client = await guarded_client_factory({}, spy)
        resp = await client.post(
            "/sentinel", headers={"X-Helium-License-State": "lic-anything"}
        )
        assert resp.status_code == 200
        assert spy.handler_ran is True

    @pytest.mark.asyncio
    async def test_composite_user_permissions_409_uses_jwt_user_id(
        self, guarded_client_factory
    ):
        # ctx.identifier = "u-7" → axis becomes user_permissions:u-7
        spy = _Spy()
        client = await guarded_client_factory(
            {"user_permissions:u-7": "perm-2"}, spy, ctx=_USER_CTX
        )
        resp = await client.post(
            "/sentinel",
            headers={"X-Helium-User-Permissions-Revision": "perm-1"},
        )
        assert resp.status_code == 409
        assert resp.json() == {
            "code": "version_drift",
            "axis": "user_permissions:u-7",
            "expected": "perm-2",
            "got": "perm-1",
        }
        assert spy.handler_ran is False

    @pytest.mark.asyncio
    async def test_composite_user_permissions_skipped_for_non_user_caller(
        self, guarded_client_factory
    ):
        # ERP caller has no JWT user identity → composite axis is skipped even
        # though the header is supplied → request forwards.
        spy = _Spy()
        client = await guarded_client_factory(
            {"user_permissions:api-key-xyz": "perm-2"}, spy, ctx=_ERP_CTX
        )
        resp = await client.post(
            "/sentinel",
            headers={"X-Helium-User-Permissions-Revision": "perm-1"},
        )
        assert resp.status_code == 200
        assert spy.handler_ran is True


# ── policy_revision read from ConfigCache ──────────────────────────────────


class TestConfigCacheAccessor:
    def test_policy_revision_read_top_level(self):
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"policy_revision": "rev-top"}
        assert _config_cache_axis_value(cache, "policy_revision") == "rev-top"

    def test_policy_revision_read_nested_under_tenant(self):
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"tenant": {"policy_revision": "rev-nested"}}
        assert _config_cache_axis_value(cache, "policy_revision") == "rev-nested"

    def test_untracked_axes_return_none_today(self):
        # NEEDS-HB: these are not in the cached config shape yet.
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"policy_revision": "rev-top"}
        for axis in (
            "license_state_id",
            "usage_state_id",
            "auth_policy_revision",
            "user_permissions:u-1",
        ):
            assert _config_cache_axis_value(cache, axis) is None

    def test_axis_lights_up_when_hb_feeds_it(self):
        # Forward-compat: once HB surfaces e.g. usage_state_id in the cached
        # config, the generic accessor resolves it with no code change.
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"usage_state_id": "use-42"}
        assert _config_cache_axis_value(cache, "usage_state_id") == "use-42"

    def test_composite_user_permissions_never_read_from_top_level(self):
        # Even if a stray top-level ``user_permissions`` blob exists, the
        # composite axis must NOT mis-resolve against it (needs per-user fabric).
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"user_permissions": "should-be-ignored"}
        assert _config_cache_axis_value(cache, "user_permissions:u-1") is None

    def test_none_cache_returns_none(self):
        assert _config_cache_axis_value(None, "policy_revision") is None


# ── /api/ingest + /api/finalize wiring + app handler registration ──────────


class TestRouteWiringAndAppHandler:
    def test_ingest_route_carries_version_drift_guard(self):
        from src.api.routes.ingest import router

        ingest_routes = [
            r for r in router.routes if getattr(r, "path", "") == "/api/ingest"
        ]
        assert ingest_routes, "/api/ingest route not found"
        dep_calls = [d.dependency for d in ingest_routes[0].dependencies]
        assert version_drift_guard in dep_calls, (
            "version_drift_guard not registered as an /api/ingest route dependency"
        )

    def test_finalize_route_carries_version_drift_guard(self):
        from src.api.routes.finalize import router

        finalize_routes = [
            r for r in router.routes if getattr(r, "path", "") == "/api/finalize"
        ]
        assert finalize_routes, "/api/finalize route not found"
        dep_calls = [d.dependency for d in finalize_routes[0].dependencies]
        assert version_drift_guard in dep_calls, (
            "version_drift_guard not registered as an /api/finalize route dependency"
        )

    def test_app_registers_version_drift_handler(self):
        from src.api.app import create_app
        from src.config import RelayConfig

        cfg = RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            heartbeat_api_key="test-relay-key",
            heartbeat_s2s_signing_key="0123456789abcdef" * 4,
        )
        app = create_app(config=cfg, api_key_secrets={"k": "s"})
        assert VersionDriftError in app.exception_handlers


# ── End-to-end through the REAL /api/finalize route (HMAC-signed) ──────────
#
# Proves the guard fires through a real sensitive mutating route + the app's
# exception handler, that a stale axis blocks the request BEFORE the handler
# (no relay.finalize.accepted lifecycle event emitted = not forwarded), and that
# a matching axis passes through to a 202. The finalize route is chosen because
# its JSON body is HMAC-signable (unlike multipart ingest).


class TestFinalizeRouteDriftEndToEnd:
    TEST_API_KEY = "test-key-001"
    TEST_SECRET = "secret-001"

    def _config(self):
        from src.config import RelayConfig

        return RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            max_files=5,
            max_file_size_mb=10.0,
            max_total_size_mb=30.0,
            allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
            internal_service_token="test-internal-token",
            heartbeat_api_key="test-relay-key",
            heartbeat_s2s_signing_key="0123456789abcdef" * 4,
        )

    def _hmac_headers(self, body: bytes, extra: dict | None = None) -> dict:
        from datetime import datetime, timezone

        from src.core.auth import compute_signature

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = compute_signature(self.TEST_API_KEY, ts, body, self.TEST_SECRET)
        headers = {
            "X-API-Key": self.TEST_API_KEY,
            "X-Timestamp": ts,
            "X-Signature": sig,
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    @pytest.mark.asyncio
    async def test_stale_policy_axis_blocks_finalize_409_no_event(self):
        import json

        from asgi_lifespan import LifespanManager

        from src.api.app import create_app
        from src.services.finalize import FinalizeService
        from src.services.lifecycle import RecordingLifecyclePublisher

        app = create_app(
            config=self._config(), api_key_secrets={self.TEST_API_KEY: self.TEST_SECRET}
        )
        async with LifespanManager(app):
            # Seed Relay's authoritative policy_revision + a recording publisher
            # so we can prove the lifecycle event is NOT emitted on drift.
            app.state.config_cache._config["policy_revision"] = "server-rev-9"
            recorder = RecordingLifecyclePublisher()
            app.state.lifecycle_publisher = recorder
            app.state.finalize_service = FinalizeService(app.state.core, recorder)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                body = json.dumps(
                    {"ref": "sha256:drifted", "trace_id": "018f-drift-1"}
                ).encode("utf-8")
                headers = self._hmac_headers(
                    body, extra={"X-Helium-Policy-Revision": "client-rev-8"}
                )
                resp = await c.post("/api/finalize", content=body, headers=headers)

        assert resp.status_code == 409, resp.text
        assert resp.json() == {
            "code": "version_drift",
            "axis": "policy_revision",
            "expected": "server-rev-9",
            "got": "client-rev-8",
        }
        # Not forwarded: the finalize handler never ran, so no lifecycle event.
        assert recorder.events == []

    @pytest.mark.asyncio
    async def test_matching_policy_axis_passes_finalize_202(self):
        import json

        from asgi_lifespan import LifespanManager

        from src.api.app import create_app
        from src.services.finalize import FinalizeService
        from src.services.lifecycle import RecordingLifecyclePublisher

        app = create_app(
            config=self._config(), api_key_secrets={self.TEST_API_KEY: self.TEST_SECRET}
        )
        async with LifespanManager(app):
            app.state.config_cache._config["policy_revision"] = "server-rev-9"
            recorder = RecordingLifecyclePublisher()
            app.state.lifecycle_publisher = recorder
            app.state.finalize_service = FinalizeService(app.state.core, recorder)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                body = json.dumps(
                    {"ref": "sha256:fresh", "trace_id": "018f-fresh-1"}
                ).encode("utf-8")
                headers = self._hmac_headers(
                    body, extra={"X-Helium-Policy-Revision": "server-rev-9"}
                )
                resp = await c.post("/api/finalize", content=body, headers=headers)

        assert resp.status_code == 202, resp.text
        assert resp.json()["trace_id"] == "018f-fresh-1"
        # Forwarded: the finalize handler ran → one lifecycle event emitted.
        assert len(recorder.events) == 1
