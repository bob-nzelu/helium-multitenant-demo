"""
Tests for the L5 Ed25519 webhook receiver (``src.api.webhook_auth``).

HeartBeat signs webhooks with **Ed25519** (reusing the SAME Ed25519 key it
publishes on its OAuth JWKS); Relay VERIFIES against the published key(s)
fetched via :class:`JWKSCache`. The locked contract (CONTRACT_LEDGER L5,
2026-06-20) carries NO kid header — verification is tried against every
published key. This file is a FRESH rewrite to the LOCKED contract — the old
symmetric-HMAC (``sha256=`` shared-secret) tests are intentionally gone, per
the ARCH "Bob ratification pass" 2026-06-19 (ledger L5) that reversed the
symmetric ruling.

These tests exercise verification WITHOUT any real HeartBeat or JWKS HTTP
server: an Ed25519 keypair is generated in-process via ``cryptography`` and
served through a stub :class:`JWKSCache` (modelled on
``tests/core/test_oauth_validator.py``).

LOCKED contract under test:
- signing input = ``f"{unix_ts}.".encode("utf-8") + raw_body_bytes``
- header ``X-HeartBeat-Signature: ed25519=<standard base64(sig)>``
- header ``X-HeartBeat-Timestamp: <unix epoch seconds>``
- replay window 300s absolute skew
- NO kid header, NO webhook-id header

Coverage:
- valid signature accepted
- tampered body rejected
- wrong-key (signed by a different keypair) rejected
- missing signature header rejected
- signature header without ``ed25519=`` prefix rejected
- missing/non-integer timestamp rejected
- expired timestamp (outside replay window) rejected
- key rotation: verifies against whichever of multiple published keys signed
- route POST /api/webhook: 200 on valid, 401 on bad signature
- structural assertion: NO symmetric-HMAC code path exists in the module
"""

from __future__ import annotations

import base64
import inspect
import time
from typing import Dict, List, Optional

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from starlette.datastructures import Headers

import src.api.webhook_auth as webhook_auth
from src.api.webhook_auth import (
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SIGNATURE_SCHEME_PREFIX,
    WebhookVerifier,
)
from src.core.jwks_cache import JWKSCache
from src.errors import WebhookSignatureError


# ── Ed25519 test-key factory ──────────────────────────────────────────────


def _generate_keypair():
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _signing_input(timestamp: str, body: bytes) -> bytes:
    """Mirror of ``webhook_auth._build_signing_input`` — the bytes HB signs.

    LOCKED (CONTRACT_LEDGER L5): ``f"{timestamp}.".encode("utf-8") + body``.
    Kept independent here so a drift in the production helper is caught.
    """
    return f"{timestamp}.".encode("utf-8") + body


def _sign(private_key: Ed25519PrivateKey, *, timestamp: str, body: bytes) -> str:
    """Produce the ``ed25519=<standard base64>`` header value HB would send."""
    sig = private_key.sign(_signing_input(timestamp, body))
    return SIGNATURE_SCHEME_PREFIX + base64.b64encode(sig).decode("ascii")


# ── JWKSCache stub (in-memory, no HTTP) ───────────────────────────────────


class _StubJWKSCache(JWKSCache):
    """Serves Ed25519 public keys from an in-memory list.

    Bypasses ``JWKSCache.__init__`` and overrides ``get_all_keys`` (the L5
    no-kid resolution entrypoint) entirely, like the OAuth validator tests'
    stub — so no httpx client is constructed.
    """

    def __init__(self, keys: List[Ed25519PublicKey]):
        self._key_list = list(keys)

    async def get_all_keys(self) -> List[Ed25519PublicKey]:
        return list(self._key_list)


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
    """Stub cache with one valid Ed25519 webhook key published."""
    return _StubJWKSCache([public_key])


@pytest.fixture
def verifier(jwks_cache):
    return WebhookVerifier(jwks_cache, replay_window_s=300)


def _make_request(
    private_key: Ed25519PrivateKey,
    *,
    body: bytes = b'{"event": "ping"}',
    timestamp: Optional[str] = None,
    signature: Optional[str] = None,
    omit: Optional[set] = None,
) -> _FakeRequest:
    """Build a fully-signed fake webhook request, with knobs for negatives."""
    omit = omit or set()
    if timestamp is None:
        timestamp = str(int(time.time()))
    if signature is None:
        signature = _sign(private_key, timestamp=timestamp, body=body)
    headers: Dict[str, str] = {}
    if HEADER_TIMESTAMP not in omit:
        headers[HEADER_TIMESTAMP] = timestamp
    if HEADER_SIGNATURE not in omit:
        headers[HEADER_SIGNATURE] = signature
    return _FakeRequest(headers, body)


# ── Valid path ─────────────────────────────────────────────────────────────


class TestValidSignature:
    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, verifier, private_key):
        request = _make_request(private_key)
        # Returns None and does not raise.
        assert await verifier.verify(request) is None

    @pytest.mark.asyncio
    async def test_headers_are_case_insensitive(self, verifier, private_key):
        """HTTP headers are case-insensitive — canonical-cased names still work."""
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        sig = _sign(private_key, timestamp=ts, body=body)
        request = _FakeRequest(
            {
                "X-HeartBeat-Timestamp": ts,
                "X-HeartBeat-Signature": sig,
            },
            body,
        )
        assert await verifier.verify(request) is None

    @pytest.mark.asyncio
    async def test_empty_body_signature_accepted(self, verifier, private_key):
        """An empty raw body is a valid signing input (``f"{ts}." + b"")``."""
        request = _make_request(private_key, body=b"")
        assert await verifier.verify(request) is None


# ── Key rotation: multiple published keys ──────────────────────────────────


class TestKeyRotation:
    @pytest.mark.asyncio
    async def test_verifies_against_second_published_key(self):
        """HB publishes two keys during rotation; the delivery is signed by the
        SECOND one. The verifier must try every published key and accept."""
        old_priv, old_pub = _generate_keypair()
        new_priv, new_pub = _generate_keypair()
        cache = _StubJWKSCache([old_pub, new_pub])
        verifier = WebhookVerifier(cache, replay_window_s=300)

        # Sign with the NEW key (second in the published set).
        request = _make_request(new_priv)
        assert await verifier.verify(request) is None

    @pytest.mark.asyncio
    async def test_no_published_keys_rejected(self, private_key):
        """Empty JWKS (no Ed25519 keys at all) → reject, fail closed."""
        verifier = WebhookVerifier(_StubJWKSCache([]), replay_window_s=300)
        request = _make_request(private_key)
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)


# ── Rejections ──────────────────────────────────────────────────────────────


class TestRejections:
    @pytest.mark.asyncio
    async def test_tampered_body_rejected(self, verifier, private_key):
        request = _make_request(private_key, body=b'{"event": "ping"}')
        # Swap the body AFTER signing — signature now covers different bytes.
        request.state.raw_body = b'{"event": "TAMPERED"}'
        request._body = b'{"event": "TAMPERED"}'
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, verifier):
        """The delivery was signed by a key NOT in the published JWKS."""
        attacker_key, _ = _generate_keypair()
        request = _make_request(attacker_key)  # signs with a key not published
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, verifier, private_key):
        request = _make_request(private_key, omit={HEADER_SIGNATURE})
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_signature_without_ed25519_prefix_rejected(
        self, verifier, private_key
    ):
        """A signature header missing the ``ed25519=`` scheme prefix → reject.

        The value below is a perfectly valid base64 signature but lacks the
        mandated prefix, so it must be refused before any verify attempt.
        """
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        raw_sig = private_key.sign(_signing_input(ts, body))
        bare_b64 = base64.b64encode(raw_sig).decode("ascii")  # no ed25519= prefix
        request = _FakeRequest(
            {HEADER_TIMESTAMP: ts, HEADER_SIGNATURE: bare_b64}, body
        )
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_wrong_scheme_prefix_rejected(self, verifier, private_key):
        """An ``hmac-sha256=`` (or any non-``ed25519=``) prefix → reject."""
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        raw_sig = private_key.sign(_signing_input(ts, body))
        wrong = "hmac-sha256=" + base64.b64encode(raw_sig).decode("ascii")
        request = _FakeRequest(
            {HEADER_TIMESTAMP: ts, HEADER_SIGNATURE: wrong}, body
        )
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
        """A signature with the right prefix but the wrong length / bytes."""
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        bad = SIGNATURE_SCHEME_PREFIX + base64.b64encode(b"too-short").decode("ascii")
        request = _FakeRequest(
            {HEADER_TIMESTAMP: ts, HEADER_SIGNATURE: bad}, body
        )
        with pytest.raises(WebhookSignatureError):
            await verifier.verify(request)

    @pytest.mark.asyncio
    async def test_non_base64_signature_rejected(self, verifier, private_key):
        """``ed25519=`` prefix present but the remainder is not valid base64."""
        ts = str(int(time.time()))
        body = b'{"event": "ping"}'
        bad = SIGNATURE_SCHEME_PREFIX + "!!!not base64!!!"
        request = _FakeRequest(
            {HEADER_TIMESTAMP: ts, HEADER_SIGNATURE: bad}, body
        )
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
        sig = _sign(private_key, timestamp=ts, body=body)
        resp = await route_client.post(
            "/api/webhook",
            content=body,
            headers={
                HEADER_TIMESTAMP: ts,
                HEADER_SIGNATURE: sig,
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_route_rejects_bad_signature(self, route_client, private_key):
        body = b'{"event": "config_changed"}'
        ts = str(int(time.time()))
        sig = _sign(private_key, timestamp=ts, body=body)
        resp = await route_client.post(
            "/api/webhook",
            content=b'{"event": "TAMPERED"}',  # body differs from what was signed
            headers={
                HEADER_TIMESTAMP: ts,
                HEADER_SIGNATURE: sig,
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
    symmetric scheme they replace, so a naive substring scan over raw source
    would false-positive. We tokenize and drop COMMENT + STRING tokens,
    leaving only real executable code — which is where a symmetric-HMAC *code
    path* would actually live.
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
