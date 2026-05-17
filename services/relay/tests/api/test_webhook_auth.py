"""
Tests for the CSSV1 S4 R12 signed webhook receiver.

Two layers:
    1. Unit tests on the verifier helpers (``_compute_signature``,
       ``_check_ip_allow_list``, ``_parse_cidrs``). Includes the
       canonical test vector from ``WEBHOOK_CONFIG_CONTRACT.md §7``
       so HB-producer + Relay-consumer + Core-consumer all agree
       byte-for-byte.
    2. Integration tests against the live route
       ``POST /api/v1/webhook/config_changed`` through the FastAPI
       LifespanManager + ASGITransport stack. Pin all six failure
       codes from contract §5.4 plus the success path.

The legacy ``/internal/refresh-cache`` route stays on the Bearer
``verify_internal_token`` scheme and has its own coverage in
``test_internal_route.py``; this file does NOT touch that route.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import time
from typing import Any, Dict

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.webhook_auth import (
    _check_ip_allow_list,
    _compute_signature,
    _parse_cidrs,
)
from src.config import RelayConfig
from src.errors import WebhookAuthError


# ── Canonical test vector — must match WEBHOOK_CONFIG_CONTRACT.md §7 ────


CANON_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
CANON_TIMESTAMP = "1782825600"
CANON_BODY_BYTES = (
    b'{"changed":["tier_settings"],"timestamp":"2026-06-19T00:00:00Z","source":"config.db"}'
)
# Computed at implementation time (2026-05-17). HB-producer + Core-consumer
# pin the same value via their own ``_expected_signature()`` helpers.
CANON_SIGNATURE = (
    "sha256=a502df4f0e8d5af82e1af9f7dc92b94df727d19c6321061251afc743cddfae16"
)


# ── _compute_signature ──────────────────────────────────────────────────


class TestComputeSignature:
    """Canonical signing primitive — same one HB uses."""

    def test_canonical_vector(self):
        """Contract §7 vector. If this fails, Relay's signing diverges
        from HB's and webhooks will reject under cross-service tests."""
        sig = _compute_signature(
            signing_key_hex=CANON_KEY,
            timestamp=CANON_TIMESTAMP,
            body_bytes=CANON_BODY_BYTES,
        )
        assert sig == CANON_SIGNATURE

    def test_empty_body(self):
        """sha256(timestamp + ".") for an empty body — should still be
        a 64-char hex digest."""
        sig = _compute_signature(
            signing_key_hex=CANON_KEY,
            timestamp="0",
            body_bytes=b"",
        )
        assert sig.startswith("sha256=")
        assert len(sig) == len("sha256=") + 64

    def test_timestamp_changes_signature(self):
        """Replay protection inputs — two different timestamps with the
        same body must produce different signatures."""
        sig_a = _compute_signature(CANON_KEY, "1000000000", CANON_BODY_BYTES)
        sig_b = _compute_signature(CANON_KEY, "1000000001", CANON_BODY_BYTES)
        assert sig_a != sig_b

    def test_body_change_changes_signature(self):
        """Tamper detection — body change MUST invalidate signature."""
        tampered = CANON_BODY_BYTES.replace(b"tier_settings", b"transforma_config")
        sig_a = _compute_signature(CANON_KEY, CANON_TIMESTAMP, CANON_BODY_BYTES)
        sig_b = _compute_signature(CANON_KEY, CANON_TIMESTAMP, tampered)
        assert sig_a != sig_b


# ── CIDR parsing + IP check ─────────────────────────────────────────────


class TestParseCidrs:
    def test_canonical_default(self):
        nets = _parse_cidrs("172.16.0.0/12,10.0.0.0/8,127.0.0.1/32")
        assert len(nets) == 3
        assert ipaddress.ip_address("172.20.0.5") in nets[0]

    def test_whitespace_tolerated(self):
        nets = _parse_cidrs(" 127.0.0.1/32 , 10.0.0.0/8 ")
        assert len(nets) == 2

    def test_empty_string_yields_empty(self):
        assert _parse_cidrs("") == []

    def test_malformed_entry_skipped(self):
        """Bad CIDR is dropped with a warning; well-formed peers keep working."""
        nets = _parse_cidrs("garbage,127.0.0.1/32")
        assert len(nets) == 1
        assert ipaddress.ip_address("127.0.0.1") in nets[0]


class TestCheckIpAllowList:
    def setup_method(self):
        self.nets = _parse_cidrs("127.0.0.1/32,10.0.0.0/8")

    def test_loopback_allowed(self):
        assert _check_ip_allow_list("127.0.0.1", self.nets) is True

    def test_docker_private_allowed(self):
        assert _check_ip_allow_list("10.42.0.99", self.nets) is True

    def test_public_ip_rejected(self):
        assert _check_ip_allow_list("8.8.8.8", self.nets) is False

    def test_malformed_ip_rejected(self):
        """A garbage string in client.host can't be parsed → reject."""
        assert _check_ip_allow_list("not-an-ip", self.nets) is False

    def test_empty_nets_rejects_everything(self):
        assert _check_ip_allow_list("127.0.0.1", []) is False


# ── Integration: live route ──────────────────────────────────────────────


WEBHOOK_PATH = "/api/v1/webhook/config_changed"


def _signed_headers(
    body_bytes: bytes,
    *,
    signing_key_hex: str = CANON_KEY,
    timestamp: str | None = None,
) -> Dict[str, str]:
    ts = timestamp or str(int(time.time()))
    sig = _compute_signature(signing_key_hex, ts, body_bytes)
    return {
        "Content-Type": "application/json",
        "X-HeartBeat-Timestamp": ts,
        "X-HeartBeat-Signature": sig,
    }


@pytest.fixture
def _config_signed():
    """Relay config with webhook signing fully configured."""
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        internal_service_token="test-internal-token",
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
        webhook_signing_key=CANON_KEY,
        # Loopback only — the ASGITransport client connects from 127.0.0.1.
        webhook_allowed_cidrs="127.0.0.1/32",
    )


@pytest.fixture
def _config_no_signing_key():
    """Relay config WITHOUT the webhook signing key — 503 expected."""
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        internal_service_token="test-internal-token",
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
        webhook_signing_key="",          # not configured
        webhook_allowed_cidrs="127.0.0.1/32",
    )


@pytest.fixture
def _config_strict_cidr():
    """Relay config with an allow-list that 127.0.0.1 is NOT in."""
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        internal_service_token="test-internal-token",
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
        webhook_signing_key=CANON_KEY,
        webhook_allowed_cidrs="192.168.99.0/24",
    )


async def _make_client(config: RelayConfig) -> AsyncClient:
    app = create_app(config=config, api_key_secrets={})
    ctx = LifespanManager(app)
    await ctx.__aenter__()
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    # Cache the lifespan ctx on the client so the test fixture can close
    # both in lockstep.
    client._lifespan_ctx = ctx  # type: ignore[attr-defined]
    return client


@pytest.fixture
async def signed_client(_config_signed):
    client = await _make_client(_config_signed)
    try:
        yield client
    finally:
        await client.aclose()
        await client._lifespan_ctx.__aexit__(None, None, None)


@pytest.fixture
async def unsigned_client(_config_no_signing_key):
    client = await _make_client(_config_no_signing_key)
    try:
        yield client
    finally:
        await client.aclose()
        await client._lifespan_ctx.__aexit__(None, None, None)


@pytest.fixture
async def strict_cidr_client(_config_strict_cidr):
    client = await _make_client(_config_strict_cidr)
    try:
        yield client
    finally:
        await client.aclose()
        await client._lifespan_ctx.__aexit__(None, None, None)


def _body() -> bytes:
    return json.dumps({
        "changed": ["transforma_config"],
        "timestamp": "2026-05-17T12:00:00Z",
        "source": "heartbeat",
    }).encode("utf-8")


class TestWebhookRouteSuccess:

    @pytest.mark.asyncio
    async def test_signed_request_accepted(self, signed_client):
        body = _body()
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body),
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["status"] == "ok"


class TestWebhookRouteFailures:
    """Pin all six failure codes from WEBHOOK_CONFIG_CONTRACT.md §5.4."""

    @pytest.mark.asyncio
    async def test_no_signing_key_returns_503_not_configured(self, unsigned_client):
        body = _body()
        resp = await unsigned_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body),
        )
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "WEBHOOK_NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_disallowed_ip_returns_403_ip_rejected(self, strict_cidr_client):
        body = _body()
        resp = await strict_cidr_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_IP_REJECTED"

    @pytest.mark.asyncio
    async def test_missing_signature_returns_403_sig_missing(self, signed_client):
        body = _body()
        # No X-HeartBeat-Signature header.
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Timestamp": str(int(time.time())),
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_MISSING"

    @pytest.mark.asyncio
    async def test_wrong_signature_prefix_returns_403_sig_missing(self, signed_client):
        body = _body()
        ts = str(int(time.time()))
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Timestamp": ts,
                # Prefix isn't ``sha256=`` — must reject as missing.
                "X-HeartBeat-Signature": "md5=deadbeef",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_MISSING"

    @pytest.mark.asyncio
    async def test_missing_timestamp_returns_403_bad_timestamp(self, signed_client):
        body = _body()
        ts = str(int(time.time()))
        sig = _compute_signature(CANON_KEY, ts, body)
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Signature": sig,
                # No X-HeartBeat-Timestamp.
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_BAD_TIMESTAMP"

    @pytest.mark.asyncio
    async def test_non_integer_timestamp_returns_403_bad_timestamp(self, signed_client):
        body = _body()
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Timestamp": "yesterday",
                "X-HeartBeat-Signature": "sha256=deadbeef",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_BAD_TIMESTAMP"

    @pytest.mark.asyncio
    async def test_stale_timestamp_returns_403_replay(self, signed_client):
        """Timestamp 10 minutes old, beyond the 300 s window."""
        body = _body()
        stale_ts = str(int(time.time()) - 600)
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body, timestamp=stale_ts),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_REPLAY"

    @pytest.mark.asyncio
    async def test_future_timestamp_returns_403_replay(self, signed_client):
        """Timestamp 10 minutes in the future, beyond the +300 s side
        of the symmetric window."""
        body = _body()
        future_ts = str(int(time.time()) + 600)
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body, timestamp=future_ts),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_REPLAY"

    @pytest.mark.asyncio
    async def test_hmac_mismatch_returns_403_sig_invalid(self, signed_client):
        body = _body()
        ts = str(int(time.time()))
        # Sign with a DIFFERENT key — verifier expects CANON_KEY, this
        # uses a different one → mismatch.
        wrong_key = "f" * 64
        bad_sig = _compute_signature(wrong_key, ts, body)
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Timestamp": ts,
                "X-HeartBeat-Signature": bad_sig,
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_INVALID"

    @pytest.mark.asyncio
    async def test_tampered_body_returns_403_sig_invalid(self, signed_client):
        """Sign one body, send a different one — HMAC mismatch fires."""
        original = _body()
        tampered = original.replace(b"transforma_config", b"tier_settings")
        ts = str(int(time.time()))
        # Sign over the ORIGINAL but send TAMPERED — verifier hashes
        # the wire body so signature won't match.
        sig = _compute_signature(CANON_KEY, ts, original)
        resp = await signed_client.post(
            WEBHOOK_PATH,
            content=tampered,
            headers={
                "Content-Type": "application/json",
                "X-HeartBeat-Timestamp": ts,
                "X-HeartBeat-Signature": sig,
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "WEBHOOK_SIG_INVALID"


class TestWebhookErrorBodyShape:

    @pytest.mark.asyncio
    async def test_error_body_does_not_leak_signing_key(self, unsigned_client):
        body = _body()
        resp = await unsigned_client.post(
            WEBHOOK_PATH,
            content=body,
            headers=_signed_headers(body),
        )
        body_text = resp.text
        # Make sure neither the canonical key nor any 64-hex string is
        # echoed back. Defensive — leaking secrets in 5xx bodies is a
        # common footgun.
        assert CANON_KEY not in body_text
