"""
Tests for core/auth.py — HMAC-SHA256 authentication
"""

import pytest
from datetime import datetime, timezone, timedelta

from src.core.auth import (
    TIMESTAMP_WINDOW_S,
    authenticate,
    compute_signature,
    validate_api_key,
    validate_timestamp,
    verify_signature,
)
from src.errors import (
    AuthenticationFailedError,
    InvalidAPIKeyError,
    SignatureVerificationFailedError,
    TimestampExpiredError,
)


class TestValidateTimestamp:
    """Test timestamp validation."""

    def test_valid_current_timestamp(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = validate_timestamp(ts)
        assert isinstance(result, datetime)

    def test_valid_timestamp_with_offset(self):
        # 2 minutes ago — within window
        ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        result = validate_timestamp(ts)
        assert isinstance(result, datetime)

    def test_valid_timestamp_at_boundary(self):
        # Just under 5 minutes
        ts = (datetime.now(timezone.utc) - timedelta(seconds=299)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        result = validate_timestamp(ts)
        assert isinstance(result, datetime)

    def test_expired_timestamp(self):
        # 10 minutes ago — expired
        ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with pytest.raises(TimestampExpiredError) as exc_info:
            validate_timestamp(ts)
        assert "600" in str(exc_info.value.message)

    def test_future_timestamp_expired(self):
        # 10 minutes in the future — also outside window
        ts = (datetime.now(timezone.utc) + timedelta(seconds=600)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with pytest.raises(TimestampExpiredError):
            validate_timestamp(ts)

    def test_invalid_format(self):
        with pytest.raises(AuthenticationFailedError, match="Invalid timestamp"):
            validate_timestamp("not-a-date")

    def test_invalid_format_partial(self):
        with pytest.raises(AuthenticationFailedError):
            validate_timestamp("2026-13-01T00:00:00Z")  # Month 13

    def test_timestamp_window_constant(self):
        assert TIMESTAMP_WINDOW_S == 300


class TestValidateApiKey:
    """Test API key lookup."""

    def test_valid_key(self, api_key_secrets):
        secret = validate_api_key("test-key-001", api_key_secrets)
        assert secret == "secret-for-test-key-001"

    def test_invalid_key(self, api_key_secrets):
        with pytest.raises(InvalidAPIKeyError):
            validate_api_key("unknown-key", api_key_secrets)

    def test_empty_key(self, api_key_secrets):
        with pytest.raises(InvalidAPIKeyError):
            validate_api_key("", api_key_secrets)


class TestComputeSignature:
    """Test HMAC signature computation."""

    def test_deterministic(self):
        sig1 = compute_signature("key", "2026-01-01T00:00:00Z", b"body", "secret")
        sig2 = compute_signature("key", "2026-01-01T00:00:00Z", b"body", "secret")
        assert sig1 == sig2

    def test_returns_hex_string(self):
        sig = compute_signature("key", "2026-01-01T00:00:00Z", b"body", "secret")
        assert len(sig) == 64  # SHA256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_body_different_sig(self):
        sig1 = compute_signature("key", "ts", b"body1", "secret")
        sig2 = compute_signature("key", "ts", b"body2", "secret")
        assert sig1 != sig2

    def test_different_key_different_sig(self):
        sig1 = compute_signature("key1", "ts", b"body", "secret")
        sig2 = compute_signature("key2", "ts", b"body", "secret")
        assert sig1 != sig2

    def test_different_timestamp_different_sig(self):
        sig1 = compute_signature("key", "ts1", b"body", "secret")
        sig2 = compute_signature("key", "ts2", b"body", "secret")
        assert sig1 != sig2

    def test_different_secret_different_sig(self):
        sig1 = compute_signature("key", "ts", b"body", "secret1")
        sig2 = compute_signature("key", "ts", b"body", "secret2")
        assert sig1 != sig2

    def test_empty_body(self):
        sig = compute_signature("key", "ts", b"", "secret")
        assert len(sig) == 64  # Still produces a valid signature


class TestVerifySignature:
    """Test HMAC signature verification."""

    def test_valid_signature(self):
        api_key = "test-key"
        timestamp = "2026-01-31T10:00:00Z"
        body = b"test body content"
        secret = "test-secret"

        signature = compute_signature(api_key, timestamp, body, secret)
        # Should not raise
        verify_signature(api_key, timestamp, signature, body, secret)

    def test_invalid_signature(self):
        with pytest.raises(SignatureVerificationFailedError):
            verify_signature("key", "ts", "wrong_sig", b"body", "secret")

    def test_tampered_body(self):
        api_key = "key"
        timestamp = "ts"
        body = b"original"
        secret = "secret"

        sig = compute_signature(api_key, timestamp, body, secret)

        with pytest.raises(SignatureVerificationFailedError):
            verify_signature(api_key, timestamp, sig, b"tampered", secret)


class TestAuthenticate:
    """Test full authentication flow."""

    def test_full_auth_success(self, api_key_secrets, valid_timestamp):
        api_key = "test-key-001"
        body = b"request body"
        secret = api_key_secrets[api_key]
        signature = compute_signature(api_key, valid_timestamp, body, secret)

        result = authenticate(
            api_key=api_key,
            timestamp=valid_timestamp,
            signature=signature,
            body=body,
            api_key_secrets=api_key_secrets,
            trace_id="test-trace",
        )

        assert result == api_key

    def test_auth_invalid_timestamp(self, api_key_secrets):
        with pytest.raises(AuthenticationFailedError):
            authenticate(
                api_key="test-key-001",
                timestamp="not-a-date",
                signature="any",
                body=b"body",
                api_key_secrets=api_key_secrets,
            )

    def test_auth_expired_timestamp(self, api_key_secrets):
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with pytest.raises(TimestampExpiredError):
            authenticate(
                api_key="test-key-001",
                timestamp=old_ts,
                signature="any",
                body=b"body",
                api_key_secrets=api_key_secrets,
            )

    def test_auth_unknown_api_key(self, api_key_secrets, valid_timestamp):
        with pytest.raises(InvalidAPIKeyError):
            authenticate(
                api_key="nonexistent-key",
                timestamp=valid_timestamp,
                signature="any",
                body=b"body",
                api_key_secrets=api_key_secrets,
            )

    def test_auth_wrong_signature(self, api_key_secrets, valid_timestamp):
        with pytest.raises(SignatureVerificationFailedError):
            authenticate(
                api_key="test-key-001",
                timestamp=valid_timestamp,
                signature="0000000000000000000000000000000000000000000000000000000000000000",
                body=b"body",
                api_key_secrets=api_key_secrets,
            )

    def test_auth_different_keys(self, api_key_secrets, valid_timestamp):
        """Each API key produces a different valid signature."""
        body = b"same body"

        for api_key in api_key_secrets:
            secret = api_key_secrets[api_key]
            sig = compute_signature(api_key, valid_timestamp, body, secret)

            result = authenticate(
                api_key=api_key,
                timestamp=valid_timestamp,
                signature=sig,
                body=body,
                api_key_secrets=api_key_secrets,
            )
            assert result == api_key
