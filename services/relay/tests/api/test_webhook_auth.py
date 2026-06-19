"""
Tests for the L5 Ed25519 webhook receiver (``src.api.webhook_auth``).

HeartBeat signs webhooks with **Ed25519** (reusing its OAuth JWKS infra +
a published webhook public key); Relay VERIFIES against that key fetched by
``kid`` via :class:`JWKSCache`. This file is a FRESH rewrite — the old
symmetric-HMAC (``sha256=`` shared-secret) tests are intentionally gone, per
the ARCH "Bob ratification pass" 2026-06-19 (ledger L5) that reversed the
symmetric ruling.

These tests exercise verification WITHOUT any real HeartBeat or JWKS HTTP
server: an Ed25519 keypair is generated in-process via ``cryptography`` and
served through a stub :class:`JWKSCache` (modelled on
``tests/core/test_oauth_validator.py``).

Coverage:
- valid signature accepted
- tampered body rejected
- wrong-kid rejected
- wrong-key (kid present but different keypair) rejected
- missing signature header rejected
- missing kid header rejected
- missing/non-integer timestamp rejected
- expired timestamp (outside replay window) rejected
- route POST /api/webhook: 200 on valid, 401 on bad signature
- structural assertion: NO symmetric-HMAC code path exists in the module
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import time
from typing import Any, Dict, Optional

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from starlette.datastructures import Headers

import src.api.webhook_auth as webhook_auth
from src.api.webhook_auth import (
    HEADER_KEY_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    HEADER_WEBHOOK_ID,
    WebhookVerifier,
)
from src.core.jwks_cache import JWKSCache
from src.errors import WebhookSignatureError


# ── Ed25519 test-key factory ──────────────────────────────────────────────


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signing_input(webhook_id: str, timestamp: str, body: bytes) -> bytes:
    """Mirror of ``webhook_auth._build_signing_input`` — the bytes HB signs.

    PROVISIONAL (NEEDS-FROM-HB #1): ``"{webhook_id}:{timestamp}:{sha256_hex(body)}"``.
    Kept independent here so a drift in the production helper is caught.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{webhook_id}:{timestamp}:{body_hash}".encode("ascii")


def _sign(
    private_key: Ed25519PrivateKey,
    *,
    webhook_id: str,
    timestamp: str,
    body: bytes,
) -> str:
    """Produce the base64url Ed25519 signature HB would send."""
    sig = private_key.sign(_signing_input(webhook_id, timestamp, body))
    return _b64url(sig)


# ── JWKSCache stub (in-memory, no HTTP) ───────────────────────────────────


class _StubJWKSCache(JWKSCache):
    """Serves Ed25519 public keys from an in-memory ``kid -> key`` dict.

    Bypasses ``JWKSCache.__init__`` and overrides ``get_key`` entirely, exactly
    like the OAuth validator tests' stub — so no httpx client is constructed.
    """

    def __init__(self, keys: Dict[str, Ed25519PublicKey]):
        self._key_map = keys

    async def get_key(self, kid: str) -> Optional[Ed25519PublicKey]:
        return self._key_map.get(kid)


# ── Minimal fake Request ──────────────────────────────────────────────────


class _FakeState:
    pass


class _FakeRequest:
    """Just enough of ``starlette.requests.Request`` for the verifier.

    Exposes a case-insensitive ``.headers`` (real Starlette ``Headers``),
    a ``.state`` carrying ``raw_body``, and an async ``.body()`` fallback.
    """

    def __init__(self, headers: Dict[str, str], body: bytes):
        # Starlette Headers is case-insensitive, matching a real request.
        self.headers = Headers(headers)
        self.state = _FakeState()
        self.state.raw_body = body
        self._body = body

    async def body(self) -> bytes:
        return self._body


# ── Fixtures ──────────────────────────────────────────────────────────────

KID = "hb-webhook-key-1"


@pytest.fixture
def keypair():
    return _generate_keypair()


@pytest.fixture
def private_key(keypair):
    return keypair[0]


@pytest.fixture
def public_key(keypair):
    return keypair[1]


@pytest.fixture
def jwks_cache(public_key):
    """Stub cache with one valid Ed25519 webhook key under KID."""
    return _StubJWKSCache({KID: public_key})


@pytest.fixture
def verifier(jwks_cache):
    return WebhookVerifier(jwks_cache, replay_window_s=300)


def _make_request(
    private_key: Ed25519PrivateKey,
    *,
    body: bytes = b'{"event": "ping"}',
    webhook_id: str = "wh-001",
    kid: str = KID,
    timestamp: Optional[str] = None,
    signature: Optional[str] = None,
    omit: Optional[set] = None,
) -> _FakeRequest:
    """Build a fully-signed fake webhook request, with knobs for negatives."""
    omit = omit or set()
    if timestamp is None:
        timestamp = str(int(time.time()))
    if signature is None:
        signature = _sign(
            private_key, webhook_id=webhook_id, timestamp=timestamp, body=body
        )
    headers: Dict[str, str] = {}
    if HEADER_KEY_ID not in omit:
        headers[HEADER_KEY_ID] = kid
    if HEADER_TIMESTAMP not in omit:
        headers[HEADER_TIMESTAMP] = timestamp
    if HEADER_SIGNATURE not in omit:
        headers[HEADER_SIGNATURE] = signature
    if HEADER_WEBHOOK_ID not in omit:
        headers[HEADER_WEBHOOK_ID] = webhook_id
    return _FakeRequest(headers, body)


# ── Valid path ─────────────────────────────────────────────────────────────


class TestValidSignature:
    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, verifier, private_key):
        request = _make_request(private_key)
        # Returns None and does not raise.
        assert await verifier.verify(request) is None

    @pytest.mark.asyncio
    async def test_valid_signature_no_webhook_id(self, verifier, private_key):
        """webhook_id is optional in the provisional contract — empty is fine
        as long as both sides agree (signing input uses '')."""
        request = _make_request(private_key, webhook_id="")
        assert await verifier.verify(request) is None

    @pytest.mark.asyncio
    async def test_kid_header_is_case_insensitive(self, verifier, private_key):
        """HTTP headers are case-insensitive — an upper-cased kid still works."""
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        sig = _sign(private_key, webhook_id="wh-001", timestamp=ts, body=body)
        request = _FakeRequest(
            {
                "X-HeartBeat-Key-Id": KID,
                "X-HeartBeat-Timestamp": ts,
                "X-HeartBeat-Signature": sig,
                "X-HeartBeat-Webhook-Id": "wh-001",
            },
            body,
        )
        assert await verifier.verify(request) is None


# ── Rejections ──────────────────────────────────────────────────────────────


class TestRejections:
    @pytest.mark.asyncio
    async def test_tampered_body_rejected(self, verifier, private_key):
        request = _make_request(private_key, body=b'{"event": "ping"}')
        # Swap the body AFTER signing — signature now covers the wrong digest.
        request.state.raw_body = b'{"event": "TAMPERED"}'
        request._body = b'{"event": "TAMPERED"}'
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_wrong_kid_rejected(self, verifier, private_key):
        """kid not present in the JWKS → reject (no key to verify against)."""
        request = _make_request(private_key, kid="unknown-kid")
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, verifier):
        """kid resolves, but the delivery was signed by a DIFFERENT keypair."""
        attacker_key, _ = _generate_keypair()
        request = _make_request(attacker_key)  # signs with the wrong key, KID header
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, verifier, private_key):
        request = _make_request(private_key, omit={HEADER_SIGNATURE})
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_missing_kid_rejected(self, verifier, private_key):
        request = _make_request(private_key, omit={HEADER_KEY_ID})
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_missing_timestamp_rejected(self, verifier, private_key):
        request = _make_request(private_key, omit={HEADER_TIMESTAMP})
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_non_integer_timestamp_rejected(self, verifier, private_key):
        request = _make_request(private_key, timestamp="not-a-number")
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_expired_timestamp_rejected(self, verifier, private_key):
        """Timestamp older than the 300s replay window → reject (even though
        the signature over that old timestamp is itself valid)."""
        old_ts = str(int(time.time()) - 3600)  # 1 hour ago
        request = _make_request(private_key, timestamp=old_ts)
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_future_timestamp_rejected(self, verifier, private_key):
        """Far-future timestamp is also outside the window (abs skew)."""
        future_ts = str(int(time.time()) + 3600)
        request = _make_request(private_key, timestamp=future_ts)
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_malformed_signature_rejected(self, verifier, private_key):
        """A signature that base64url-decodes but is the wrong length / bytes."""
        request = _make_request(private_key, signature=_b64url(b"too-short"))
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)


# ── Dependency wiring ──────────────────────────────────────────────────────


class TestDependency:
    @pytest.mark.asyncio
    async def test_unwired_verifier_fails_closed(self):
        """get_webhook_verifier raises 401 (not 500) if state is unwired."""
        from src.api.webhook_auth import get_webhook_verifier

        class _App:
            class state:  # noqa: N801 - mimic app.state attribute access
                pass

        class _Req:
            app = _App()

        with pytest.raises(WebhookSignatureError):
            await get_webhook_verifier(_Req())


# ── Route: POST /api/webhook via the full app ──────────────────────────────


@pytest.fixture
def test_config():
    from src.config import RelayConfig

    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        # HMAC s2s migration: non-empty signing key required for the
        # HeartBeatClient calls the lifespan makes at startup.
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
    )


@pytest.fixture
async def route_client(test_config, jwks_cache):
    """App client with the stub JWKSCache-backed verifier injected.

    The real lifespan builds a verifier pointed at the (unreachable) JWKS URL;
    we override ``app.state.webhook_verifier`` with one backed by the in-memory
    stub so the route can verify a real in-test signature.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from src.api.app import create_app

    app = create_app(config=test_config, api_key_secrets={})
    async with LifespanManager(app):
        app.state.webhook_verifier = WebhookVerifier(jwks_cache, replay_window_s=300)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestWebhookRoute:
    @pytest.mark.asyncio
    async def test_route_accepts_valid_signature(self, route_client, private_key):
        body = b'{"event": "config_changed"}'
        ts = str(int(time.time()))
        sig = _sign(private_key, webhook_id="wh-77", timestamp=ts, body=body)
        resp = await route_client.post(
            "/api/webhook",
            content=body,
            headers={
                HEADER_KEY_ID: KID,
                HEADER_TIMESTAMP: ts,
                HEADER_SIGNATURE: sig,
                HEADER_WEBHOOK_ID: "wh-77",
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_route_rejects_bad_signature(self, route_client, private_key):
        body = b'{"event": "config_changed"}'
        ts = str(int(time.time()))
        sig = _sign(private_key, webhook_id="wh-77", timestamp=ts, body=body)
        resp = await route_client.post(
            "/api/webhook",
            content=b'{"event": "TAMPERED"}',  # body differs from what was signed
            headers={
                HEADER_KEY_ID: KID,
                HEADER_TIMESTAMP: ts,
                HEADER_SIGNATURE: sig,
                HEADER_WEBHOOK_ID: "wh-77",
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_route_rejects_missing_headers(self, route_client):
        resp = await route_client.post(
            "/api/webhook",
            content=b'{"event": "x"}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 401


# ── Structural guard: NO symmetric-HMAC path exists ────────────────────────


def _executable_code_only(module) -> str:
    """Return module source with all comments AND string-literals removed.

    Docstrings/comments in this module legitimately *describe* the old
    symmetric scheme they replace (NEEDS-FROM-HB cross-references it), so a
    naive substring scan over raw source would false-positive. We tokenize and
    drop COMMENT + STRING tokens, leaving only real executable code — which is
    where a symmetric-HMAC *code path* would actually live.
    """
    import io
    import tokenize

    src = inspect.getsource(module)
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        # NL/NEWLINE/INDENT carry no identifiers — keep names + operators.
        out.append(tok.string)
    return " ".join(out).lower()


class TestNoSymmetricHmacPath:
    """Lock in the L5 ruling: the receiver is asymmetric-only.

    These scan EXECUTABLE CODE (comments + docstrings stripped) so they assert
    the absence of a symmetric *code path*, not the absence of the words in
    explanatory prose (which intentionally references the scheme it replaced).
    """

    def test_module_has_no_symmetric_hmac_code(self):
        code = _executable_code_only(webhook_auth)
        # No HMAC primitive, no shared-secret comparison, no shared-secret config.
        assert "hmac" not in code            # no `import hmac`, no `hmac.new(...)`
        assert "compare_digest" not in code  # no constant-time secret compare
        assert "webhook_signing_key" not in code  # no shared-secret config field
        # The asymmetric primitive IS present in real code.
        source = inspect.getsource(webhook_auth)
        assert ".verify(" in source          # Ed25519 public-key verify
        assert "InvalidSignature" in source

    def test_route_module_has_no_symmetric_hmac_code(self):
        import src.api.routes.webhook as webhook_route

        code = _executable_code_only(webhook_route)
        assert "hmac" not in code
        assert "compare_digest" not in code
        assert "webhook_signing_key" not in code

    def test_no_symmetric_webhook_auth_error_symbol(self):
        """The old symmetric error type must not exist in errors."""
        import src.errors as errors

        assert not hasattr(errors, "WebhookAuthError")
        assert hasattr(errors, "WebhookSignatureError")
