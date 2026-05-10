"""
Tests for the inbound auth dispatcher in ``src/api/deps.py``.

The dispatcher is the inbound-side counterpart of CSSV1 R1: every request
to Relay presents credentials in one of three forms (HMAC, Bearer
api_key:api_secret, Bearer JWT) and ``authenticate_request`` resolves
them into a single :class:`CallerContext`. CSSV1 R9.4 — pin the
branching with synthetic Request objects so a regression in the
header-shape detection (e.g., misclassifying a JWT as service creds)
shows up loudly.

Tests construct ``Request`` directly via Starlette's test scope shape
rather than the full FastAPI test client; the dispatcher is a plain
async function with no FastAPI runtime dependencies, and a synthetic
scope is faster + isolates the branching from middleware ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from src.api.caller_context import CallerContext
from src.api.deps import authenticate_request
from src.config import RelayConfig
from src.core.auth import compute_signature
from src.errors import (
    AuthenticationFailedError,
    AuthUpstreamUnavailableError,
    HeartBeatUnavailableError,
    InvalidAPIKeyError,
    JWTRejectedError,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_app_state(
    *,
    config: Optional[RelayConfig] = None,
    api_key_secrets: Optional[Dict[str, str]] = None,
    introspect_client: Any = None,
    tenant_registry: Optional[Dict[str, Any]] = None,
) -> SimpleNamespace:
    """Minimal app.state stand-in covering every dispatcher dependency."""
    return SimpleNamespace(
        config=config or RelayConfig(
            heartbeat_api_key="hb-key",
            heartbeat_api_secret="hb-sec",
        ),
        api_key_secrets=api_key_secrets or {},
        introspect_client=introspect_client,
        tenant_registry=tenant_registry or {},
    )


def _make_request(
    *,
    headers: Dict[str, str],
    raw_body: bytes = b"",
    app_state: Optional[SimpleNamespace] = None,
) -> Request:
    """Build a Starlette ``Request`` with the given headers + cached body.

    Mimics what ``BodyCacheMiddleware`` + ``TraceIDMiddleware`` write into
    ``scope["state"]`` so the dispatcher can read the cached body via
    ``request.state.raw_body``.
    """
    header_pairs: List[Tuple[bytes, bytes]] = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in headers.items()
    ]
    state: Dict[str, Any] = {"raw_body": raw_body, "trace_id": "test-trace"}
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/ingest",
        "headers": header_pairs,
        "query_string": b"",
        "state": state,
    }

    # Attach an app stub so request.app.state.* works.
    if app_state is None:
        app_state = _make_app_state()
    scope["app"] = SimpleNamespace(state=app_state)

    async def _empty_receive() -> Dict[str, Any]:  # pragma: no cover
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive=_empty_receive)


# ── HMAC path (§3.3) ─────────────────────────────────────────────────────


class TestDispatcherHmacPath:
    """HMAC headers present → ``_verify_hmac`` → erp CallerContext."""

    @pytest.mark.asyncio
    async def test_valid_hmac_routes_to_erp(self):
        api_key = "test-key-001"
        secret = "secret-for-test-key-001"
        body = b"some body"
        # Use current time formatted for the existing window logic.
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = compute_signature(api_key, ts, body, secret)

        app_state = _make_app_state(api_key_secrets={api_key: secret})
        req = _make_request(
            headers={
                "X-API-Key": api_key,
                "X-Timestamp": ts,
                "X-Signature": sig,
            },
            raw_body=body,
            app_state=app_state,
        )

        ctx = await authenticate_request(req)

        assert isinstance(ctx, CallerContext)
        assert ctx.actor_type == "erp"
        assert ctx.identifier == api_key
        assert ctx.raw_api_key == api_key
        # Default ERP permissions baked into _verify_hmac
        assert "blob.write" in ctx.permissions

    @pytest.mark.asyncio
    async def test_hmac_path_takes_precedence_over_bearer(self):
        """If BOTH HMAC headers and Authorization: Bearer are present, HMAC wins.

        Per the dispatcher's order-of-checks (HMAC first), the Authorization
        header should be ignored — pinning this so a future refactor doesn't
        accidentally invert the precedence.
        """
        api_key = "test-key-001"
        secret = "secret-for-test-key-001"
        body = b""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = compute_signature(api_key, ts, body, secret)

        # Bogus introspect client that would explode if the dispatcher
        # mistakenly took the Bearer branch.
        introspect = AsyncMock()
        introspect.introspect.side_effect = AssertionError(
            "dispatcher fell through to Bearer despite valid HMAC headers"
        )

        app_state = _make_app_state(
            api_key_secrets={api_key: secret},
            introspect_client=introspect,
        )
        req = _make_request(
            headers={
                "X-API-Key": api_key,
                "X-Timestamp": ts,
                "X-Signature": sig,
                "Authorization": "Bearer some.jwt.value",
            },
            raw_body=body,
            app_state=app_state,
        )

        ctx = await authenticate_request(req)
        assert ctx.actor_type == "erp"
        introspect.introspect.assert_not_called()


# ── Service-creds path (§3.2) ────────────────────────────────────────────


class TestDispatcherServiceCredsPath:
    """``Bearer api_key:api_secret`` (single colon, no JWT dots) → service."""

    @pytest.mark.asyncio
    async def test_valid_service_creds_route(self):
        api_key = "core-svc"
        secret = "core-svc-secret"
        app_state = _make_app_state(api_key_secrets={api_key: secret})
        req = _make_request(
            headers={"Authorization": f"Bearer {api_key}:{secret}"},
            app_state=app_state,
        )

        ctx = await authenticate_request(req)

        assert ctx.actor_type == "service"
        assert ctx.identifier == api_key
        # Service creds are platform-admin by convention.
        assert "*" in ctx.permissions

    @pytest.mark.asyncio
    async def test_unknown_service_creds_rejected(self):
        app_state = _make_app_state(api_key_secrets={})
        req = _make_request(
            headers={"Authorization": "Bearer ghost-key:ghost-secret"},
            app_state=app_state,
        )

        with pytest.raises(InvalidAPIKeyError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_wrong_secret_rejected(self):
        app_state = _make_app_state(api_key_secrets={"k": "real-secret"})
        req = _make_request(
            headers={"Authorization": "Bearer k:wrong-secret"},
            app_state=app_state,
        )

        with pytest.raises(InvalidAPIKeyError):
            await authenticate_request(req)


# ── User JWT path (§3.1) ─────────────────────────────────────────────────


class TestDispatcherUserJwtPath:
    """``Bearer <jwt>`` (compact JWS — two dots, no colon) → introspect."""

    @pytest.mark.asyncio
    async def test_valid_jwt_routes_via_introspect(self):
        # Realistic compact JWS shape: header.payload.signature
        jwt = "eyJ0eXAi.eyJzdWIi.SIGNATURE"

        introspect_result = SimpleNamespace(
            active=True,
            actor_type="user",
            user_id="user-123",
            role="creator",
            permissions=["blob.write"],
            tenant_id="tenant-abbey",
            device_id="device-1",
            last_auth_at=None,
            expires_at=None,
            session_expires_at=None,
            step_up_satisfied=True,
            error_code=None,
            message=None,
        )
        introspect = AsyncMock()
        introspect.introspect.return_value = introspect_result

        app_state = _make_app_state(introspect_client=introspect)
        req = _make_request(
            headers={
                "Authorization": f"Bearer {jwt}",
                "X-Source-ID": "float-app-1",
            },
            app_state=app_state,
        )

        ctx = await authenticate_request(req)

        assert ctx.actor_type == "user"
        assert ctx.identifier == "user-123"
        assert ctx.tenant_id == "tenant-abbey"
        assert ctx.source_id == "float-app-1"
        assert ctx.downstream_auth_header == f"Bearer {jwt}"

        introspect.introspect.assert_called_once()
        kwargs = introspect.introspect.call_args.kwargs
        assert kwargs["jwt_token"] == jwt

    @pytest.mark.asyncio
    async def test_introspect_unreachable_fails_closed_502(self):
        """HB introspect down → ``AuthUpstreamUnavailableError`` (502).

        Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC §2.4, the dispatcher must
        NEVER permit a request when the auth upstream is unreachable —
        cached claims are not allowed.
        """
        introspect = AsyncMock()
        introspect.introspect.side_effect = HeartBeatUnavailableError(
            "HeartBeat down for the count"
        )

        app_state = _make_app_state(introspect_client=introspect)
        req = _make_request(
            headers={"Authorization": "Bearer eyJ.eyJ.SIG"},
            app_state=app_state,
        )

        with pytest.raises(AuthUpstreamUnavailableError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_jwt_rejected_propagates(self):
        """Introspect-mapped JWTRejectedError bubbles through unchanged."""
        introspect = AsyncMock()
        introspect.introspect.side_effect = JWTRejectedError(
            error_code="TOKEN_EXPIRED",
            message="Token expired at 2026-04-01T00:00:00Z",
            status_code=401,
        )

        app_state = _make_app_state(introspect_client=introspect)
        req = _make_request(
            headers={"Authorization": "Bearer eyJ.eyJ.SIG"},
            app_state=app_state,
        )

        with pytest.raises(JWTRejectedError) as exc_info:
            await authenticate_request(req)
        assert exc_info.value.error_code == "TOKEN_EXPIRED"

    @pytest.mark.asyncio
    async def test_introspect_client_missing_502(self):
        """If app.state.introspect_client is None → fail closed."""
        app_state = _make_app_state(introspect_client=None)
        req = _make_request(
            headers={"Authorization": "Bearer eyJ.eyJ.SIG"},
            app_state=app_state,
        )

        with pytest.raises(AuthUpstreamUnavailableError):
            await authenticate_request(req)


# ── Header-shape detection edges ─────────────────────────────────────────


class TestDispatcherHeaderShapeBranching:
    """Pin the JWT-vs-service-creds heuristic so future tweaks regress loudly."""

    @pytest.mark.asyncio
    async def test_no_credentials_rejected(self):
        req = _make_request(headers={})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_empty_bearer_rejected(self):
        req = _make_request(headers={"Authorization": "Bearer "})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_bearer_with_only_whitespace_rejected(self):
        req = _make_request(headers={"Authorization": "Bearer    "})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_partial_hmac_falls_through(self):
        """If only some HMAC headers are set, dispatcher must NOT take the HMAC branch.

        With X-API-Key but no X-Timestamp/X-Signature, the HMAC validator
        would crash. The dispatcher must either route elsewhere or reject
        cleanly. With no Authorization header either, it should reject.
        """
        req = _make_request(headers={"X-API-Key": "k"})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_jwt_with_colon_in_payload_routes_correctly(self):
        """A real JWS contains two dots; even if base64url payload includes :, the heuristic must detect dots-first.

        The dispatcher checks ``"." in token`` to disambiguate from
        ``api_key:api_secret``. Pin this so a future refactor doesn't
        accidentally misclassify a real JWT as service creds.
        """
        jwt = "eyJraWQiOiJ4OnkifQ.eyJzdWIiOiJ1OnkifQ.SIG_PART"
        # The dispatcher should see the two dots and route to JWT.
        introspect = AsyncMock()
        introspect.introspect.return_value = SimpleNamespace(
            active=True,
            actor_type="user",
            user_id="u",
            role=None,
            permissions=[],
            tenant_id="t",
            device_id=None,
            last_auth_at=None,
            expires_at=None,
            session_expires_at=None,
            step_up_satisfied=None,
            error_code=None,
            message=None,
        )
        app_state = _make_app_state(introspect_client=introspect)
        req = _make_request(
            headers={"Authorization": f"Bearer {jwt}"},
            app_state=app_state,
        )

        ctx = await authenticate_request(req)
        assert ctx.actor_type == "user"
        introspect.introspect.assert_called_once()

    @pytest.mark.asyncio
    async def test_bearer_three_part_no_dots_rejected(self):
        """Token like ``a:b:c`` (more than one colon, no dots) is neither JWT nor service creds.

        Currently the dispatcher routes it to JWT (since ``"." in token``
        is False, but also the colon-count check fails for service creds).
        Pin observed behaviour so a refactor catches the change.
        """
        introspect = AsyncMock()
        introspect.introspect.side_effect = JWTRejectedError(
            error_code="TOKEN_INVALID",
            message="Not a JWT",
        )
        app_state = _make_app_state(introspect_client=introspect)
        req = _make_request(
            headers={"Authorization": "Bearer a:b:c"},
            app_state=app_state,
        )

        with pytest.raises(JWTRejectedError):
            await authenticate_request(req)
