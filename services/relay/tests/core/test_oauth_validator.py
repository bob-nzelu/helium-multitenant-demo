"""
Tests for OAuthTokenValidator (Q37 Gap #2).

These tests exercise the local JWKS-based JWT validation without any real
HeartBeat or Redis connections.  Ed25519 keys are generated in-process via
``cryptography``.

Coverage:
- Valid token with correct aud → claims returned
- Expired token → JWTValidationError
- Wrong aud → JWTValidationError
- Unknown kid → JWTValidationError (after JWKS refresh)
- jti in Redis blocklist → JWTValidationError
- Redis down → fail-open (request proceeds)
- Malformed JWT → JWTValidationError
- Missing exp claim → JWTValidationError
- Missing kid in header → JWTValidationError
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.jwks_cache import JWKSCache
from src.core.oauth_validator import JWTValidationError, OAuthTokenValidator


# ── Ed25519 test-key factory ──────────────────────────────────────────────


def _generate_keypair():
    """Generate an Ed25519 private/public key pair."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def _public_key_to_jwk(public_key, kid: str = "test-key-1") -> Dict[str, Any]:
    """Export an Ed25519PublicKey to a JWK-compatible dict."""
    raw_bytes = public_key.public_bytes_raw()
    x_b64 = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "x": x_b64,
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(
    private_key: Ed25519PrivateKey,
    claims: Dict[str, Any],
    kid: str = "test-key-1",
    alg: str = "EdDSA",
) -> str:
    """Build a real EdDSA-signed JWT."""
    header = {"alg": alg, "kid": kid, "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(claims).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    sig_b64 = _b64url(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _valid_claims(
    tenant_id: str = "tenant-abbey",
    jti: str = "jti-001",
    exp_offset: int = 300,
) -> Dict[str, Any]:
    """Build a valid claims dict (expires in exp_offset seconds)."""
    return {
        "sub": f"client:{tenant_id}",
        "aud": OAuthTokenValidator.REQUIRED_AUD,
        "iss": "heartbeat",
        "tenant_id": tenant_id,
        "jti": jti,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }


# ── JWKSCache stub ────────────────────────────────────────────────────────


class _StubJWKSCache(JWKSCache):
    """JWKSCache subclass that serves keys from an in-memory dict."""

    def __init__(self, keys: Dict[str, Any]):
        # Bypass __init__; we override get_key entirely.
        self._key_map = keys  # kid → Ed25519PublicKey

    async def get_key(self, kid: str):
        return self._key_map.get(kid)


# ── Fixtures ─────────────────────────────────────────────────────────────


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
    """Stub cache with one valid Ed25519 key under 'test-key-1'."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return _StubJWKSCache({"test-key-1": public_key})


@pytest.fixture
def validator(jwks_cache):
    return OAuthTokenValidator(jwks_cache)


# ── Tests ─────────────────────────────────────────────────────────────────


class TestValidToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_claims(self, validator, private_key):
        claims = _valid_claims()
        token = _make_jwt(private_key, claims)

        result = await validator.validate(token, redis_client=None)

        assert result["tenant_id"] == "tenant-abbey"
        assert result["aud"] == OAuthTokenValidator.REQUIRED_AUD
        assert result["jti"] == "jti-001"

    @pytest.mark.asyncio
    async def test_valid_token_aud_as_list(self, validator, private_key):
        """RFC 7519 §4.1.3 — aud can be a list."""
        claims = _valid_claims()
        claims["aud"] = [OAuthTokenValidator.REQUIRED_AUD, "another.service"]
        token = _make_jwt(private_key, claims)

        result = await validator.validate(token, redis_client=None)
        assert result["tenant_id"] == "tenant-abbey"

    @pytest.mark.asyncio
    async def test_valid_token_within_clock_skew(self, validator, private_key):
        """Token expired 30s ago but within 60s clock skew → allowed."""
        claims = _valid_claims(exp_offset=-30)
        token = _make_jwt(private_key, claims)

        result = await validator.validate(token, redis_client=None)
        assert result["tenant_id"] == "tenant-abbey"


class TestExpiredToken:
    @pytest.mark.asyncio
    async def test_expired_outside_skew_raises(self, validator, private_key):
        """Expired 120s ago (beyond 60s skew) → JWTValidationError."""
        claims = _valid_claims(exp_offset=-120)
        token = _make_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="expired"):
            await validator.validate(token, redis_client=None)

    @pytest.mark.asyncio
    async def test_missing_exp_raises(self, validator, private_key):
        claims = _valid_claims()
        del claims["exp"]
        token = _make_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="exp"):
            await validator.validate(token, redis_client=None)


class TestWrongAud:
    @pytest.mark.asyncio
    async def test_wrong_aud_raises(self, validator, private_key):
        claims = _valid_claims()
        claims["aud"] = "helium.frontend"
        token = _make_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="aud"):
            await validator.validate(token, redis_client=None)

    @pytest.mark.asyncio
    async def test_aud_list_without_required_raises(self, validator, private_key):
        claims = _valid_claims()
        claims["aud"] = ["helium.frontend", "other.service"]
        token = _make_jwt(private_key, claims)

        with pytest.raises(JWTValidationError, match="aud"):
            await validator.validate(token, redis_client=None)


class TestUnknownKid:
    @pytest.mark.asyncio
    async def test_unknown_kid_raises(self, jwks_cache, private_key):
        """Token with a kid not in JWKS → JWTValidationError after refresh attempt."""
        claims = _valid_claims()
        # Sign with a key whose kid isn't in the cache.
        token = _make_jwt(private_key, claims, kid="unknown-key-999")

        validator = OAuthTokenValidator(jwks_cache)
        with pytest.raises(JWTValidationError, match="kid"):
            await validator.validate(token, redis_client=None)


class TestSignatureVerification:
    @pytest.mark.asyncio
    async def test_tampered_payload_raises(self, validator, private_key):
        """Modify the payload after signing → signature check fails."""
        claims = _valid_claims()
        token = _make_jwt(private_key, claims)
        parts = token.split(".")
        # Replace payload with a different one (re-encode different claims).
        evil_claims = dict(claims)
        evil_claims["tenant_id"] = "attacker-tenant"
        evil_payload = _b64url(json.dumps(evil_claims).encode("utf-8"))
        tampered = f"{parts[0]}.{evil_payload}.{parts[2]}"

        with pytest.raises(JWTValidationError):
            await validator.validate(tampered, redis_client=None)

    @pytest.mark.asyncio
    async def test_malformed_token_raises(self, validator):
        with pytest.raises(JWTValidationError):
            await validator.validate("not.a.valid.jwt.at.all", redis_client=None)

    @pytest.mark.asyncio
    async def test_two_part_token_raises(self, validator):
        with pytest.raises(JWTValidationError):
            await validator.validate("header.payload", redis_client=None)


class TestJtiBlocklist:
    @pytest.mark.asyncio
    async def test_blocklisted_jti_raises(self, validator, private_key):
        """jti present in Redis blocklist → JWTValidationError."""
        claims = _valid_claims(jti="revoked-jti")
        token = _make_jwt(private_key, claims)

        # Build a mock RedisClient that reports the jti as present.
        redis = MagicMock()
        redis.is_available = True
        redis._redis = AsyncMock()
        redis._redis.get = AsyncMock(return_value="1")  # truthy → blocked

        with pytest.raises(JWTValidationError, match="blocklist"):
            await validator.validate(token, redis_client=redis)

    @pytest.mark.asyncio
    async def test_clean_jti_allowed(self, validator, private_key):
        """jti NOT in Redis → normal path, claims returned."""
        claims = _valid_claims(jti="clean-jti")
        token = _make_jwt(private_key, claims)

        redis = MagicMock()
        redis.is_available = True
        redis._redis = AsyncMock()
        redis._redis.get = AsyncMock(return_value=None)  # not present

        result = await validator.validate(token, redis_client=redis)
        assert result["jti"] == "clean-jti"

    @pytest.mark.asyncio
    async def test_redis_down_fail_open(self, validator, private_key):
        """Redis unavailable → fail-open; token accepted despite blocklist check failure."""
        claims = _valid_claims(jti="some-jti")
        token = _make_jwt(private_key, claims)

        redis = MagicMock()
        redis.is_available = True
        redis._redis = AsyncMock()
        redis._redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        # Should NOT raise — fail-open.
        result = await validator.validate(token, redis_client=redis)
        assert result["tenant_id"] == "tenant-abbey"

    @pytest.mark.asyncio
    async def test_redis_none_skips_blocklist(self, validator, private_key):
        """redis_client=None → blocklist check skipped entirely."""
        claims = _valid_claims(jti="some-jti")
        token = _make_jwt(private_key, claims)
        result = await validator.validate(token, redis_client=None)
        assert result["jti"] == "some-jti"

    @pytest.mark.asyncio
    async def test_redis_not_available_skips_blocklist(self, validator, private_key):
        """RedisClient.is_available=False → blocklist check skipped."""
        claims = _valid_claims()
        token = _make_jwt(private_key, claims)

        redis = MagicMock()
        redis.is_available = False
        redis._redis = None

        result = await validator.validate(token, redis_client=redis)
        assert result["tenant_id"] == "tenant-abbey"


class TestMissingKidInHeader:
    @pytest.mark.asyncio
    async def test_missing_kid_header_raises(self, validator, private_key):
        """JWT header without 'kid' → JWTValidationError."""
        claims = _valid_claims()
        # Build JWT manually without kid in header.
        header = {"alg": "EdDSA", "typ": "JWT"}  # no kid
        header_b64 = _b64url(json.dumps(header).encode("utf-8"))
        payload_b64 = _b64url(json.dumps(claims).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        sig_b64 = _b64url(private_key.sign(signing_input))
        token = f"{header_b64}.{payload_b64}.{sig_b64}"

        with pytest.raises(JWTValidationError, match="kid"):
            await validator.validate(token, redis_client=None)
