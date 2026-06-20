"""
Tests for Q37 Gap #2 — OAuth aud routing in the auth dispatcher (deps.py).

Covers the new aud-routing branch in ``authenticate_request``:
- Valid aud=helium.relay-ingest token → CallerContext built (actor_type="erp")
- Expired token → 401 JWTValidationError
- Wrong aud (helium.frontend) → routed to introspect, NOT OAuthTokenValidator
- Unknown kid → 401 (JWKS refresh attempted)
- jti in Redis blocklist → 401
- Redis down → fail-open (request proceeds)
- JWKS URL empty / not configured → OAuth validator disabled, bearer falls
  through to introspect
"""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.requests import Request

from src.api.caller_context import CallerContext
from src.api.deps import authenticate_request
from src.config import RelayConfig
from src.core.oauth_validator import JWTValidationError, OAuthTokenValidator
from src.errors import AuthUpstreamUnavailableError


# ── JWT helpers ───────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(
    private_key: Ed25519PrivateKey,
    claims: Dict[str, Any],
    kid: str = "test-key-1",
) -> str:
    header = {"alg": "EdDSA", "kid": kid, "typ": "JWT"}
    h_b64 = _b64url(json.dumps(header).encode("utf-8"))
    p_b64 = _b64url(json.dumps(claims).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig_b64 = _b64url(private_key.sign(signing_input))
    return f"{h_b64}.{p_b64}.{sig_b64}"


def _valid_relay_claims(
    tenant_id: str = "tenant-abbey",
    jti: str = "jti-test-001",
    exp_offset: int = 300,
) -> Dict[str, Any]:
    return {
        "sub": f"client:{tenant_id}",
        "aud": OAuthTokenValidator.REQUIRED_AUD,
        "iss": "heartbeat",
        "tenant_id": tenant_id,
        "jti": jti,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }


# ── Request factory ───────────────────────────────────────────────────────


def _make_request(
    headers: Dict[str, str],
    *,
    jwks_url: str = "http://heartbeat:9000/.well-known/jwks.json",
    oauth_validator: Any = None,
    introspect_client: Any = None,
    redis_client: Any = None,
) -> Request:
    header_pairs = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in headers.items()
    ]
    state: Dict[str, Any] = {"raw_body": b"", "trace_id": "test-trace"}
    cfg = RelayConfig(
        heartbeat_api_key="hb-key",
        heartbeat_api_secret="hb-sec",
        jwks_url=jwks_url,
    )
    app_state = SimpleNamespace(
        config=cfg,
        api_key_secrets={},
        introspect_client=introspect_client,
        tenant_registry={},
        oauth_validator=oauth_validator,
        redis=redis_client,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/ingest",
        "headers": header_pairs,
        "query_string": b"",
        "state": state,
        "app": SimpleNamespace(state=app_state),
    }

    async def _recv():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive=_recv)


# ── OAuth path routing ────────────────────────────────────────────────────


class TestOAuthAudRouting:
    """aud=helium.relay-ingest → _verify_oauth_jwt → OAuthTokenValidator."""

    @pytest.mark.asyncio
    async def test_valid_oauth_token_builds_erp_context(self):
        private_key = Ed25519PrivateKey.generate()
        claims = _valid_relay_claims()
        token = _make_jwt(private_key, claims)

        # Stub OAuthTokenValidator — returns pre-validated claims.
        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(return_value=claims)

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
        )

        ctx = await authenticate_request(req)

        assert isinstance(ctx, CallerContext)
        assert ctx.actor_type == "erp"
        assert ctx.tenant_id == "tenant-abbey"
        assert ctx.identifier == f"client:tenant-abbey"
        assert "blob.write" in ctx.permissions
        mock_validator.validate.assert_called_once()

    @pytest.mark.asyncio
    async def test_expired_oauth_token_raises_401(self):
        private_key = Ed25519PrivateKey.generate()
        claims = _valid_relay_claims()
        token = _make_jwt(private_key, claims)

        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(
            side_effect=JWTValidationError("JWT expired: exp=...")
        )

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
        )

        with pytest.raises(JWTValidationError, match="expired"):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_unknown_kid_raises_401(self):
        """Unknown kid → OAuthTokenValidator raises JWTValidationError."""
        private_key = Ed25519PrivateKey.generate()
        claims = _valid_relay_claims()
        token = _make_jwt(private_key, claims, kid="ghost-key")

        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(
            side_effect=JWTValidationError("JWT kid='ghost-key' not found in JWKS")
        )

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
        )

        with pytest.raises(JWTValidationError, match="kid"):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_blocklisted_jti_raises_401(self):
        private_key = Ed25519PrivateKey.generate()
        claims = _valid_relay_claims(jti="revoked-jti")
        token = _make_jwt(private_key, claims)

        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(
            side_effect=JWTValidationError("JWT jti='revoked-jti' is in the revocation blocklist")
        )

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
        )

        with pytest.raises(JWTValidationError, match="blocklist"):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_redis_down_fail_open(self):
        """Redis down → OAuthTokenValidator proceeds (fail-open)."""
        private_key = Ed25519PrivateKey.generate()
        claims = _valid_relay_claims()
        token = _make_jwt(private_key, claims)

        # Validator succeeds (has already applied fail-open logic internally).
        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(return_value=claims)

        redis = MagicMock()
        redis.is_available = False

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
            redis_client=redis,
        )

        ctx = await authenticate_request(req)
        assert ctx.actor_type == "erp"
        assert ctx.tenant_id == "tenant-abbey"


class TestWrongAudFallsToIntrospect:
    """aud != helium.relay-ingest → existing HB introspect path (unchanged)."""

    def _introspect_mock_success(self) -> AsyncMock:
        introspect = AsyncMock()
        introspect.introspect.return_value = SimpleNamespace(
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
        return introspect

    def _make_frontend_jwt(self) -> str:
        """Build a JWT-shaped token with aud=helium.frontend in payload."""
        header = {"alg": "EdDSA", "kid": "hb-key", "typ": "JWT"}
        payload = {
            "sub": "user-123",
            "aud": "helium.frontend",
            "exp": int(time.time()) + 300,
            "jti": "frontend-jti",
        }
        h_b64 = _b64url(json.dumps(header).encode("utf-8"))
        p_b64 = _b64url(json.dumps(payload).encode("utf-8"))
        # Signature doesn't matter here — introspect client is mocked.
        return f"{h_b64}.{p_b64}.fakesignature"

    @pytest.mark.asyncio
    async def test_frontend_aud_routes_to_introspect_not_validator(self):
        """aud=helium.frontend goes to introspect; OAuthTokenValidator never called."""
        token = self._make_frontend_jwt()
        introspect = self._introspect_mock_success()

        # A validator that would fail if called.
        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(
            side_effect=AssertionError("OAuthTokenValidator should NOT be called for helium.frontend aud")
        )

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
            introspect_client=introspect,
        )

        ctx = await authenticate_request(req)
        # Routes to introspect → user actor_type
        assert ctx.actor_type == "user"
        assert ctx.identifier == "user-123"
        introspect.introspect.assert_called_once()
        mock_validator.validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_aud_in_payload_routes_to_introspect(self):
        """JWT with no aud claim at all falls through to introspect."""
        header = {"alg": "EdDSA", "kid": "hb-key", "typ": "JWT"}
        payload = {"sub": "user-x", "exp": int(time.time()) + 300}
        h_b64 = _b64url(json.dumps(header).encode("utf-8"))
        p_b64 = _b64url(json.dumps(payload).encode("utf-8"))
        token = f"{h_b64}.{p_b64}.fakesig"

        introspect = self._introspect_mock_success()
        mock_validator = AsyncMock(spec=OAuthTokenValidator)
        mock_validator.REQUIRED_AUD = OAuthTokenValidator.REQUIRED_AUD
        mock_validator.validate = AsyncMock(
            side_effect=AssertionError("Should not call OAuth validator")
        )

        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            oauth_validator=mock_validator,
            introspect_client=introspect,
        )

        ctx = await authenticate_request(req)
        assert ctx.actor_type == "user"
        mock_validator.validate.assert_not_called()


class TestJwksUrlNotConfigured:
    """RELAY_JWKS_URL empty → OAuth path disabled; all Bearer JWTs go to introspect."""

    def _introspect_mock_success(self) -> AsyncMock:
        introspect = AsyncMock()
        introspect.introspect.return_value = SimpleNamespace(
            active=True,
            actor_type="user",
            user_id="user-abc",
            role=None,
            permissions=[],
            tenant_id="tenant-t",
            device_id=None,
            last_auth_at=None,
            expires_at=None,
            session_expires_at=None,
            step_up_satisfied=None,
            error_code=None,
            message=None,
        )
        return introspect

    def _relay_ingest_jwt_shape(self) -> str:
        """A token whose aud=helium.relay-ingest but JWKS URL is not set."""
        header = {"alg": "EdDSA", "kid": "k1"}
        payload = {
            "aud": OAuthTokenValidator.REQUIRED_AUD,
            "sub": "client:t",
            "exp": int(time.time()) + 300,
        }
        h_b64 = _b64url(json.dumps(header).encode("utf-8"))
        p_b64 = _b64url(json.dumps(payload).encode("utf-8"))
        return f"{h_b64}.{p_b64}.fakesig"

    @pytest.mark.asyncio
    async def test_jwks_url_empty_skips_oauth_path(self):
        """Empty jwks_url → even aud=helium.relay-ingest falls to introspect."""
        token = self._relay_ingest_jwt_shape()
        introspect = self._introspect_mock_success()

        # Build request with empty jwks_url and no oauth_validator.
        header_pairs = [
            (b"authorization", f"Bearer {token}".encode("latin-1")),
        ]
        state: Dict[str, Any] = {"raw_body": b"", "trace_id": "test-trace"}
        cfg = RelayConfig(
            heartbeat_api_key="hb-key",
            heartbeat_api_secret="hb-sec",
            jwks_url="",  # disabled
        )
        app_state = SimpleNamespace(
            config=cfg,
            api_key_secrets={},
            introspect_client=introspect,
            tenant_registry={},
            oauth_validator=None,
            redis=None,
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/ingest",
            "headers": header_pairs,
            "query_string": b"",
            "state": state,
            "app": SimpleNamespace(state=app_state),
        }

        async def _recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        req = Request(scope, receive=_recv)
        ctx = await authenticate_request(req)

        # Went to introspect, NOT OAuthTokenValidator.
        assert ctx.actor_type == "user"
        introspect.introspect.assert_called_once()

    @pytest.mark.asyncio
    async def test_oauth_validator_none_on_state_raises_502(self):
        """jwks_url set but oauth_validator not on app.state → 502 (defensive guard)."""
        header = {"alg": "EdDSA", "kid": "k1"}
        payload = {
            "aud": OAuthTokenValidator.REQUIRED_AUD,
            "sub": "client:t",
            "exp": int(time.time()) + 300,
        }
        h_b64 = _b64url(json.dumps(header).encode("utf-8"))
        p_b64 = _b64url(json.dumps(payload).encode("utf-8"))
        token = f"{h_b64}.{p_b64}.fakesig"

        header_pairs = [
            (b"authorization", f"Bearer {token}".encode("latin-1")),
        ]
        state: Dict[str, Any] = {"raw_body": b"", "trace_id": "test-trace"}
        cfg = RelayConfig(jwks_url="http://hb:9000/.well-known/jwks.json")
        app_state = SimpleNamespace(
            config=cfg,
            api_key_secrets={},
            introspect_client=None,
            tenant_registry={},
            oauth_validator=None,  # NOT set — defensive guard test
            redis=None,
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/ingest",
            "headers": header_pairs,
            "query_string": b"",
            "state": state,
            "app": SimpleNamespace(state=app_state),
        }

        async def _recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        req = Request(scope, receive=_recv)

        with pytest.raises(AuthUpstreamUnavailableError):
            await authenticate_request(req)
