"""
Tests for the §B-Drift / §B-VersionAxes version-drift gateway guard.

Covered:
  * Exact 409 body per axis on a supplied-axis mismatch.
  * Pass-through when axes match.
  * Pass-through when axis headers are absent.
  * Pass-through when the authoritative value is unknown (NEEDS-HB skip).
  * Header-name normalisation (canonical map + SBS alias table).
  * The guard runs BEFORE the handler — a drifted request produces NO handler
    side effect and is NOT forwarded.
  * Composite ``user_permissions:<uid>`` axis.
  * Wiring: ``/api/ingest`` carries the guard; the handler is registered.

The guard is exercised both as a pure function (``evaluate_version_drift``) and
through a real FastAPI app with the guard mounted on a sentinel route, so we can
prove ordering/no-side-effect without fighting multipart-HMAC signing.
"""

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.api.version_drift import (
    DEFAULT_HEADER_AXIS_MAP,
    USER_ID_HEADER,
    VersionDriftError,
    evaluate_version_drift,
    make_version_drift_guard,
    normalise_axis_name,
    version_drift_error_handler,
    version_drift_guard,
)


# ── Pure normaliser ────────────────────────────────────────────────────────


class TestNormaliseAxisName:
    def test_canonical_axes_pass_through(self):
        for axis in (
            "policy_revision",
            "license_state_id",
            "auth_policy_revision",
            "usage_state_id",
        ):
            assert normalise_axis_name(axis) == axis

    def test_composite_user_permissions_passes_through(self):
        assert normalise_axis_name("user_permissions:u-42") == "user_permissions:u-42"

    def test_canonical_header_map(self):
        assert normalise_axis_name("X-Policy-Revision") == "policy_revision"
        assert normalise_axis_name("X-License-State-Id") == "license_state_id"
        assert normalise_axis_name("X-Auth-Policy-Revision") == "auth_policy_revision"
        assert normalise_axis_name("X-Usage-State-Id") == "usage_state_id"
        assert normalise_axis_name("X-User-Permissions") == "user_permissions"

    def test_header_map_is_case_insensitive(self):
        assert normalise_axis_name("x-policy-revision") == "policy_revision"
        assert normalise_axis_name("X-POLICY-REVISION") == "policy_revision"

    def test_sbs_alias_fallback(self):
        # bare aliases (no x- prefix) collapse via the SBS alias table
        assert normalise_axis_name("policy") == "policy_revision"
        assert normalise_axis_name("license_state") == "license_state_id"
        assert normalise_axis_name("auth_policy") == "auth_policy_revision"
        assert normalise_axis_name("usage") == "usage_state_id"
        assert normalise_axis_name("usage_state") == "usage_state_id"

    def test_unknown_header_returns_empty(self):
        assert normalise_axis_name("X-Nonsense") == ""
        assert normalise_axis_name("") == ""

    def test_repinned_header_map_overrides(self):
        custom = {"x-pol": "policy_revision"}
        assert normalise_axis_name("X-Pol", header_axis_map=custom) == "policy_revision"


# ── Pure drift evaluation ──────────────────────────────────────────────────


def _accessor_for(values: dict):
    def _acc(axis: str):
        return values.get(axis)

    return _acc


class TestEvaluateVersionDrift:
    def test_match_passes(self):
        result = evaluate_version_drift(
            {"x-policy-revision": "v5"},
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
            {"x-policy-revision": "v4"},
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

    def test_unknown_authoritative_value_is_skipped(self):
        # license_state_id supplied but the accessor returns None → NEEDS-HB skip
        result = evaluate_version_drift(
            {"x-license-state-id": "lic-9"},
            axis_value_accessor=_accessor_for({}),  # everything unknown
        )
        assert result is None

    @pytest.mark.parametrize(
        "header,axis,expected,got",
        [
            ("x-policy-revision", "policy_revision", "p2", "p1"),
            ("x-license-state-id", "license_state_id", "lic-2", "lic-1"),
            ("x-auth-policy-revision", "auth_policy_revision", "auth-2", "auth-1"),
            ("x-usage-state-id", "usage_state_id", "use-2", "use-1"),
        ],
    )
    def test_each_axis_drifts_with_exact_body(self, header, axis, expected, got):
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
            {"x-user-permissions": "perm-1"},
            axis_value_accessor=_accessor_for({"user_permissions:u-7": "perm-2"}),
            user_id="u-7",
        )
        assert result is not None
        assert result.axis == "user_permissions:u-7"
        assert result.expected == "perm-2"
        assert result.got == "perm-1"

    def test_composite_user_permissions_without_user_id_is_skipped(self):
        result = evaluate_version_drift(
            {"x-user-permissions": "perm-1"},
            axis_value_accessor=_accessor_for({"user_permissions:u-7": "perm-2"}),
            user_id="",
        )
        assert result is None

    def test_first_mismatch_wins(self):
        # both drift; policy is iterated first in DEFAULT_HEADER_AXIS_MAP order
        result = evaluate_version_drift(
            {"x-policy-revision": "p1", "x-license-state-id": "lic-1"},
            axis_value_accessor=_accessor_for(
                {"policy_revision": "p2", "license_state_id": "lic-2"}
            ),
        )
        assert result is not None
        assert result.axis == "policy_revision"


# ── Guard dependency through a real app (ordering / no side effect) ────────


class _Spy:
    """Records whether the protected handler body ran (a 'side effect')."""

    def __init__(self):
        self.handler_ran = False


def _make_guarded_app(authoritative: dict, spy: _Spy) -> FastAPI:
    """
    Build a minimal app with the REAL guard mounted on a sentinel POST route,
    backed by an in-memory authoritative-value accessor.
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

    return app


@pytest.fixture
async def guarded_client_factory():
    clients = []

    async def _factory(authoritative: dict, spy: _Spy) -> AsyncClient:
        app = _make_guarded_app(authoritative, spy)
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
        resp = await client.post("/sentinel", headers={"X-Policy-Revision": "v5"})
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
        resp = await client.post("/sentinel", headers={"X-Policy-Revision": "v4"})
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
            "/sentinel", headers={"X-License-State-Id": "lic-anything"}
        )
        assert resp.status_code == 200
        assert spy.handler_ran is True

    @pytest.mark.asyncio
    async def test_composite_user_permissions_409(self, guarded_client_factory):
        spy = _Spy()
        client = await guarded_client_factory(
            {"user_permissions:u-7": "perm-2"}, spy
        )
        resp = await client.post(
            "/sentinel",
            headers={"X-User-Permissions": "perm-1", USER_ID_HEADER: "u-7"},
        )
        assert resp.status_code == 409
        assert resp.json() == {
            "code": "version_drift",
            "axis": "user_permissions:u-7",
            "expected": "perm-2",
            "got": "perm-1",
        }
        assert spy.handler_ran is False


# ── policy_revision read from ConfigCache + /api/ingest wiring ──────────────


class TestConfigCacheAccessorAndWiring:
    def test_policy_revision_read_top_level(self):
        from src.api.version_drift import _config_cache_axis_value
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"policy_revision": "rev-top"}
        assert _config_cache_axis_value(cache, "policy_revision") == "rev-top"

    def test_policy_revision_read_nested_under_tenant(self):
        from src.api.version_drift import _config_cache_axis_value
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"tenant": {"policy_revision": "rev-nested"}}
        assert _config_cache_axis_value(cache, "policy_revision") == "rev-nested"

    def test_untracked_axes_return_none(self):
        from src.api.version_drift import _config_cache_axis_value
        from src.config_cache import ConfigCache

        cache = ConfigCache(heartbeat_client=None)
        cache._config = {"policy_revision": "rev-top"}
        for axis in (
            "license_state_id",
            "auth_policy_revision",
            "usage_state_id",
            "user_permissions:u-1",
        ):
            assert _config_cache_axis_value(cache, axis) is None

    def test_none_cache_returns_none(self):
        from src.api.version_drift import _config_cache_axis_value

        assert _config_cache_axis_value(None, "policy_revision") is None

    def test_default_header_map_has_four_first_class_axes_plus_composite(self):
        # Guards against accidental axis-set drift in the configurable map.
        assert DEFAULT_HEADER_AXIS_MAP["x-policy-revision"] == "policy_revision"
        assert DEFAULT_HEADER_AXIS_MAP["x-license-state-id"] == "license_state_id"
        assert (
            DEFAULT_HEADER_AXIS_MAP["x-auth-policy-revision"] == "auth_policy_revision"
        )
        assert DEFAULT_HEADER_AXIS_MAP["x-usage-state-id"] == "usage_state_id"
        assert DEFAULT_HEADER_AXIS_MAP["x-user-permissions"] == "user_permissions"

    def test_ingest_route_carries_version_drift_guard(self):
        # The default exported guard is the one mounted on /api/ingest.
        from src.api.routes.ingest import router

        ingest_routes = [r for r in router.routes if getattr(r, "path", "") == "/api/ingest"]
        assert ingest_routes, "/api/ingest route not found"
        route = ingest_routes[0]
        dep_calls = [d.dependency for d in route.dependencies]
        assert version_drift_guard in dep_calls, (
            "version_drift_guard not registered as an /api/ingest route dependency"
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
