"""
Unit tests for ``services/relay/src/clients/introspect.py``.

Cover the HMAC-cutover migration (post 2026-05-08 per
``HMAC_S2S_MIGRATION_SPEC.md`` + ``RELAY_NEXT_STEPS_NOTE_2026_05_09``
§1.5): the four canonical HMAC headers replace
``Authorization: Bearer api_key:api_secret``. Body bytes are signed
exactly as sent on the wire.
"""

from __future__ import annotations

import re

import pytest
import respx
from httpx import Response

from src.clients.introspect import IntrospectClient
from src.errors import (
    AuthenticationFailedError,
    HeartBeatUnavailableError,
    JWTRejectedError,
)


HEARTBEAT_URL = "http://localhost:9000"
SIGNING_KEY = "0123456789abcdef" * 4  # 64-hex test key
API_KEY = "rl_test_relay001"


@pytest.fixture
def client():
    return IntrospectClient(
        heartbeat_url=HEARTBEAT_URL,
        service_api_key=API_KEY,
        service_api_secret="legacy-bcrypt-secret-unused",
        service_signing_key=SIGNING_KEY,
        timeout_s=5.0,
    )


def _assert_hmac_headers(req) -> None:
    h = req.headers
    assert h["x-api-key"] == API_KEY
    assert h["x-timestamp"].isdigit()
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        h["x-nonce"],
    )
    assert re.fullmatch(r"[0-9a-f]{64}", h["x-signature"])
    # Bearer s2s must NOT be sent (HB returns 401 BEARER_S2S_REMOVED)
    assert (
        "authorization" not in h
        or not h["authorization"].lower().startswith("bearer ")
    ), "Authorization: Bearer ... must not appear on HMAC-signed introspect"


# ── Happy path ────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_introspect_active_user_returns_result(client):
    body = {
        "active": True,
        "user_id": "user-001",
        "tenant_id": "tenant-abbey",
        "role": "Operator",
        "permissions": ["blob.upload", "events.batch.subscribe"],
        "actor_type": "human",
        "device_id": "dev-1",
        "expires_at": "2030-01-01T00:00:00Z",
        "session_expires_at": "2030-01-02T00:00:00Z",
        "step_up_satisfied": True,
    }
    route = respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
        return_value=Response(200, json=body)
    )

    result = await client.introspect(
        "eyJ...jwt...", trace_id="t-1", required_permission="blob.upload"
    )

    assert result.active is True
    assert result.user_id == "user-001"
    assert result.tenant_id == "tenant-abbey"
    assert result.permissions == ["blob.upload", "events.batch.subscribe"]
    assert result.role == "Operator"

    # Wire format: HMAC headers + signed JSON body via content=...
    req = route.calls.last.request
    _assert_hmac_headers(req)
    assert req.headers["content-type"] == "application/json"
    assert req.headers["x-trace-id"] == "t-1"


@respx.mock
@pytest.mark.asyncio
async def test_introspect_includes_required_permission_in_body(client):
    """Per RELAY_JWT_INTROSPECT_HB_RESPONSE §2.1 + §4.3: pass
    ``required_permission='blob.upload'`` for ingest calls."""
    captured_body = {}

    def _mock_handler(request):
        captured_body["raw"] = request.read().decode("utf-8")
        return Response(
            200,
            json={
                "active": True,
                "user_id": "u",
                "tenant_id": "t",
                "role": "Operator",
                "permissions": ["blob.upload"],
                "actor_type": "human",
                "device_id": "d",
                "expires_at": None,
                "session_expires_at": None,
                "step_up_satisfied": True,
            },
        )

    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(side_effect=_mock_handler)

    await client.introspect(
        "abc.def.ghi", required_permission="blob.upload"
    )

    import json as _json

    parsed = _json.loads(captured_body["raw"])
    assert parsed["token"] == "abc.def.ghi"
    assert parsed["required_permission"] == "blob.upload"


# ── Body-bytes discipline ─────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_signed_bytes_match_wire_bytes(client):
    """The bytes signed must equal the bytes sent (spec §8.1 step 3).

    Captures the raw request body, recomputes the expected signature
    from those exact bytes, and compares.
    """
    import hashlib
    import hmac as _hmac

    captured = {}

    def _mock_handler(request):
        captured["bytes"] = request.read()
        captured["headers"] = dict(request.headers)
        return Response(
            200,
            json={
                "active": True,
                "user_id": "u",
                "tenant_id": "t",
                "role": "Operator",
                "permissions": [],
                "actor_type": "human",
                "device_id": "d",
                "expires_at": None,
                "session_expires_at": None,
                "step_up_satisfied": True,
            },
        )

    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(side_effect=_mock_handler)

    await client.introspect("the-jwt-token")

    body_bytes = captured["bytes"]
    headers = captured["headers"]
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    signing_input = (
        f"POST\n/api/auth/introspect\n"
        f"{headers['x-timestamp']}\n{headers['x-nonce']}\n{body_sha256}"
    ).encode("utf-8")
    expected_sig = _hmac.new(
        SIGNING_KEY.encode("utf-8"), signing_input, hashlib.sha256
    ).hexdigest()
    assert headers["x-signature"] == expected_sig


# ── Misconfiguration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_api_key_raises_authentication_failed():
    client = IntrospectClient(
        heartbeat_url=HEARTBEAT_URL,
        service_api_key="",
        service_signing_key=SIGNING_KEY,
    )
    with pytest.raises(AuthenticationFailedError, match="api_key"):
        await client.introspect("any.jwt.value")


@pytest.mark.asyncio
async def test_missing_signing_key_raises_authentication_failed():
    client = IntrospectClient(
        heartbeat_url=HEARTBEAT_URL,
        service_api_key=API_KEY,
        service_signing_key="",  # operator forgot to wire RELAY_S2S_SIGNING_KEY
    )
    with pytest.raises(AuthenticationFailedError, match="RELAY_S2S_SIGNING_KEY"):
        await client.introspect("any.jwt.value")


# ── Error mapping ────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_token_expired_maps_to_jwt_rejected_401(client):
    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
        return_value=Response(
            200,
            json={
                "active": False,
                "error_code": "TOKEN_EXPIRED",
                "message": "JWT exp claim in the past",
                "user_id": None,
                "tenant_id": None,
                "role": None,
                "permissions": [],
                "actor_type": None,
                "device_id": None,
                "expires_at": None,
                "session_expires_at": None,
                "step_up_satisfied": None,
            },
        )
    )

    with pytest.raises(JWTRejectedError) as excinfo:
        await client.introspect("expired.jwt")

    assert excinfo.value.status_code == 401


@respx.mock
@pytest.mark.asyncio
async def test_hb_unreachable_raises_heartbeat_unavailable(client):
    import httpx

    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
        side_effect=httpx.ConnectError("refused")
    )

    with pytest.raises(HeartBeatUnavailableError):
        await client.introspect("some.jwt")


@respx.mock
@pytest.mark.asyncio
async def test_hb_5xx_raises_heartbeat_unavailable(client):
    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
        return_value=Response(503, text="HeartBeat is restarting")
    )

    with pytest.raises(HeartBeatUnavailableError):
        await client.introspect("some.jwt")


@respx.mock
@pytest.mark.asyncio
async def test_hb_401_means_relay_creds_invalid(client):
    """A 401 from the introspect endpoint itself means HB rejected
    Relay's own s2s credentials (e.g. signing key mismatch, replay
    nonce, timestamp skew). Surfaces as ``HeartBeatUnavailableError``,
    not ``JWTRejectedError`` — the user JWT is innocent here."""
    respx.post(f"{HEARTBEAT_URL}/api/auth/introspect").mock(
        return_value=Response(
            401,
            json={
                "detail": {
                    "error_code": "BEARER_S2S_REMOVED",
                    "message": "Migrate to HMAC.",
                }
            },
        )
    )

    with pytest.raises(HeartBeatUnavailableError, match="s2s credentials"):
        await client.introspect("user.jwt")
