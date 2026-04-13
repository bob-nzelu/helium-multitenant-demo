"""
Tests for ServiceKeys container
"""

import pytest
from datetime import datetime, timezone, timedelta

from src.core.service_keys import ServiceKeys


class TestServiceKeysCreation:
    """Test ServiceKeys dataclass creation."""

    def test_create_from_values(self):
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=90)
        keys = ServiceKeys(
            firs_public_key_pem="-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
            csid="TEST-CSID-123",
            csid_expires_at=expires,
            certificate="base64-cert-data",
            loaded_at=now,
        )
        assert keys.firs_public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert keys.csid == "TEST-CSID-123"
        assert keys.csid_expires_at == expires
        assert keys.certificate == "base64-cert-data"
        assert keys.loaded_at == now

    def test_frozen(self):
        """ServiceKeys should be immutable."""
        now = datetime.now(timezone.utc)
        keys = ServiceKeys(
            firs_public_key_pem="test",
            csid="csid",
            csid_expires_at=now + timedelta(days=1),
            certificate="cert",
            loaded_at=now,
        )
        with pytest.raises(AttributeError):
            keys.csid = "new-csid"

    def test_from_api_response_iso_z(self):
        """Parse API response with Z-suffix timestamp."""
        data = {
            "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----",
            "csid": "PROD-CSID-456",
            "csid_expires_at": "2026-06-01T00:00:00Z",
            "certificate": "cert-data",
        }
        keys = ServiceKeys.from_api_response(data)
        assert keys.csid == "PROD-CSID-456"
        assert keys.csid_expires_at.year == 2026
        assert keys.csid_expires_at.month == 6
        assert keys.csid_expires_at.tzinfo is not None

    def test_from_api_response_iso_offset(self):
        """Parse API response with +00:00 offset timestamp."""
        data = {
            "firs_public_key_pem": "key",
            "csid": "CSID",
            "csid_expires_at": "2026-12-31T23:59:59+00:00",
            "certificate": "",
        }
        keys = ServiceKeys.from_api_response(data)
        assert keys.csid_expires_at.year == 2026
        assert keys.csid_expires_at.month == 12

    def test_from_api_response_no_certificate(self):
        """Certificate is optional in API response."""
        data = {
            "firs_public_key_pem": "key",
            "csid": "CSID",
            "csid_expires_at": "2026-06-01T00:00:00Z",
        }
        keys = ServiceKeys.from_api_response(data)
        assert keys.certificate == ""

    def test_loaded_at_is_set(self):
        """from_api_response sets loaded_at to now."""
        before = datetime.now(timezone.utc)
        data = {
            "firs_public_key_pem": "key",
            "csid": "CSID",
            "csid_expires_at": "2030-01-01T00:00:00Z",
        }
        keys = ServiceKeys.from_api_response(data)
        after = datetime.now(timezone.utc)
        assert before <= keys.loaded_at <= after


class TestServiceKeysExpiry:
    """Test CSID expiry checks."""

    def test_csid_not_expired(self):
        keys = ServiceKeys(
            firs_public_key_pem="key",
            csid="csid",
            csid_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            certificate="",
            loaded_at=datetime.now(timezone.utc),
        )
        assert keys.is_csid_expired is False

    def test_csid_expired(self):
        keys = ServiceKeys(
            firs_public_key_pem="key",
            csid="csid",
            csid_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            certificate="",
            loaded_at=datetime.now(timezone.utc),
        )
        assert keys.is_csid_expired is True

    def test_csid_expiring_soon(self):
        """Within 24 hours = expiring soon."""
        keys = ServiceKeys(
            firs_public_key_pem="key",
            csid="csid",
            csid_expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            certificate="",
            loaded_at=datetime.now(timezone.utc),
        )
        assert keys.is_csid_expiring_soon is True

    def test_csid_not_expiring_soon(self):
        """More than 24 hours = not expiring soon."""
        keys = ServiceKeys(
            firs_public_key_pem="key",
            csid="csid",
            csid_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            certificate="",
            loaded_at=datetime.now(timezone.utc),
        )
        assert keys.is_csid_expiring_soon is False
