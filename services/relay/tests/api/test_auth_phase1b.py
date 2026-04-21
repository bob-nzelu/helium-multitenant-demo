"""
Tests for Phase 1b auth changes — nonce replay on HMAC — and for the
Phase 1a dispatcher (debt: not tested when it landed).

We stub Request/app directly rather than wiring a full FastAPI app to keep
the auth-logic tests fast and decoupled from lifespan plumbing.
"""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.api.deps import (
    authenticate_request,
    _verify_hmac,
    _verify_service_creds,
)
from src.errors import (
    AuthenticationFailedError,
    InvalidAPIKeyError,
    ReplayDetectedError,
)
from src.config import RelayConfig


def _make_request(
    headers=None,
    body=b"",
    api_key_secrets=None,
    redis_mock=None,
    introspect_mock=None,
    tenant_registry=None,
    nonce_ttl_s=600,
):
    """Build a minimal Request-like object for auth tests."""
    hdrs = headers or {}
    state = SimpleNamespace(
        raw_body=body,
        trace_id="trace-test",
    )
    cfg = RelayConfig(
        heartbeat_api_key="hb-key", heartbeat_api_secret="hb-secret",
        nonce_ttl_s=nonce_ttl_s,
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            config=cfg,
            api_key_secrets=api_key_secrets or {},
            tenant_registry=tenant_registry or {},
            redis=redis_mock,
            introspect_client=introspect_mock,
        ),
    )
    # httpx-style header access (case-insensitive via .get()).
    class _H:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}
        def get(self, k, default=None):
            return self._d.get(k.lower(), default)

    req = SimpleNamespace(
        headers=_H(hdrs),
        state=state,
        app=app,
    )

    async def _body():
        return body
    req.body = _body
    return req


# ── HMAC + Nonce (A.4) ──────────────────────────────────────────────────


def _valid_hmac_signature(api_key, secret, timestamp, body):
    """Matches src/core/auth.compute_signature format."""
    import hmac, hashlib
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    return hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256,
    ).hexdigest()


class TestHmacNonceReplay:
    @pytest.mark.asyncio
    async def test_first_nonce_accepted(self):
        body = b'{"invoice":1}'
        key, secret = "k-test", "s-test"
        ts = "2026-04-20T22:00:00Z"
        sig = _valid_hmac_signature(key, secret, ts, body)

        redis = MagicMock()
        redis.nonce_claim = AsyncMock(return_value=True)

        req = _make_request(
            headers={"X-Nonce": "nonce-1"},
            body=body,
            api_key_secrets={key: secret},
            redis_mock=redis,
        )
        # Patch timestamp validator by using current time
        from datetime import datetime, timezone
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig_now = _valid_hmac_signature(key, secret, now_ts, body)

        ctx = await _verify_hmac(req, key, now_ts, sig_now)
        assert ctx.actor_type == "erp"
        assert ctx.identifier == key
        redis.nonce_claim.assert_awaited_once_with("nonce-1", ttl_s=600)

    @pytest.mark.asyncio
    async def test_replayed_nonce_raises(self):
        body = b'{"invoice":1}'
        key, secret = "k-test", "s-test"

        redis = MagicMock()
        redis.nonce_claim = AsyncMock(return_value=False)  # replay

        from datetime import datetime, timezone
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = _valid_hmac_signature(key, secret, now_ts, body)

        req = _make_request(
            headers={"X-Nonce": "reused-nonce"},
            body=body,
            api_key_secrets={key: secret},
            redis_mock=redis,
        )
        with pytest.raises(ReplayDetectedError):
            await _verify_hmac(req, key, now_ts, sig)

    @pytest.mark.asyncio
    async def test_missing_nonce_accepted_for_backward_compat(self):
        body = b'{"invoice":1}'
        key, secret = "k-test", "s-test"

        redis = MagicMock()
        redis.nonce_claim = AsyncMock()  # must not be called

        from datetime import datetime, timezone
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = _valid_hmac_signature(key, secret, now_ts, body)

        req = _make_request(
            headers={},  # no X-Nonce
            body=body,
            api_key_secrets={key: secret},
            redis_mock=redis,
        )
        ctx = await _verify_hmac(req, key, now_ts, sig)
        assert ctx.actor_type == "erp"
        redis.nonce_claim.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonce_with_no_redis_passes(self):
        body = b'{"invoice":1}'
        key, secret = "k-test", "s-test"

        from datetime import datetime, timezone
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = _valid_hmac_signature(key, secret, now_ts, body)

        req = _make_request(
            headers={"X-Nonce": "n"},
            body=body,
            api_key_secrets={key: secret},
            redis_mock=None,  # Redis not available
        )
        ctx = await _verify_hmac(req, key, now_ts, sig)
        # No raise — fall through when redis absent
        assert ctx.actor_type == "erp"


# ── Phase 1a dispatcher debt ────────────────────────────────────────────


class TestDispatcher:
    @pytest.mark.asyncio
    async def test_no_credentials_raises(self):
        req = _make_request(headers={})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_bearer_empty_raises(self):
        req = _make_request(headers={"Authorization": "Bearer "})
        with pytest.raises(AuthenticationFailedError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_hmac_headers_dispatch(self):
        body = b"x"
        key, secret = "k", "s"
        from datetime import datetime, timezone
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = _valid_hmac_signature(key, secret, now_ts, body)

        req = _make_request(
            headers={
                "X-API-Key": key, "X-Timestamp": now_ts, "X-Signature": sig,
            },
            body=body,
            api_key_secrets={key: secret},
        )
        ctx = await authenticate_request(req)
        assert ctx.actor_type == "erp"

    @pytest.mark.asyncio
    async def test_service_creds_dispatch(self):
        req = _make_request(
            headers={"Authorization": "Bearer k:s"},
            api_key_secrets={"k": "s"},
        )
        ctx = await authenticate_request(req)
        assert ctx.actor_type == "service"
        assert ctx.identifier == "k"

    @pytest.mark.asyncio
    async def test_service_creds_unknown_key_raises(self):
        req = _make_request(
            headers={"Authorization": "Bearer k:s"},
            api_key_secrets={},  # not registered
        )
        with pytest.raises(InvalidAPIKeyError):
            await authenticate_request(req)

    @pytest.mark.asyncio
    async def test_jwt_path_dispatch(self):
        """JWT path routes to introspect client."""
        introspect = AsyncMock()
        introspect.introspect = AsyncMock(return_value=SimpleNamespace(
            user_id="u-1", tenant_id="t-1", permissions=["invoice.view"],
        ))
        # JWT with two dots (compact JWS shape)
        token = "header.eyJzdWIiOiJ1LTEifQ.signature"
        req = _make_request(
            headers={"Authorization": f"Bearer {token}"},
            introspect_mock=introspect,
        )
        ctx = await authenticate_request(req)
        assert ctx.actor_type == "user"
        assert ctx.tenant_id == "t-1"
        assert "invoice.view" in ctx.permissions
