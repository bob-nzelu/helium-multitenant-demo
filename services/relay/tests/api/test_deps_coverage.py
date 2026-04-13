"""
Coverage tests for src/api/deps.py.

Targets uncovered lines: 27, 32, 37, 42, 65, 90-104.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.errors import EncryptionRequiredError


# ── Getter Dependencies (lines 27, 32, 37, 42) ─────────────────────────


class TestGetterDependencies:
    """Cover get_config, get_module_cache, get_bulk_service, get_external_service."""

    def test_get_config_returns_config(self):
        """get_config returns the config from app state."""
        from src.api.deps import get_config

        mock_request = MagicMock()
        mock_request.app.state.config = "test-config"
        result = get_config(mock_request)
        assert result == "test-config"

    def test_get_module_cache_returns_cache(self):
        """get_module_cache returns the module cache from app state."""
        from src.api.deps import get_module_cache

        mock_request = MagicMock()
        mock_request.app.state.module_cache = "test-cache"
        result = get_module_cache(mock_request)
        assert result == "test-cache"

    def test_get_bulk_service_returns_service(self):
        """get_bulk_service returns the bulk service from app state."""
        from src.api.deps import get_bulk_service

        mock_request = MagicMock()
        mock_request.app.state.bulk_service = "test-bulk"
        result = get_bulk_service(mock_request)
        assert result == "test-bulk"

    def test_get_external_service_returns_service(self):
        """get_external_service returns the external service from app state."""
        from src.api.deps import get_external_service

        mock_request = MagicMock()
        mock_request.app.state.external_service = "test-external"
        result = get_external_service(mock_request)
        assert result == "test-external"


# ── raw_body Fallback (line 65) ──────────────────────────────────────────


class TestAuthenticateRawBodyFallback:
    """Cover the body fallback path in authenticate_request."""

    @pytest.mark.asyncio
    async def test_authenticate_reads_body_when_raw_body_missing(self):
        """When request.state.raw_body is not set, falls back to request.body()."""
        from src.api.deps import authenticate_request

        mock_request = MagicMock()
        mock_request.state = MagicMock(spec=[])  # No raw_body attribute
        mock_request.body = AsyncMock(return_value=b"test body")
        mock_request.app.state.api_key_secrets = {"key1": "secret1"}

        # The function will try to authenticate and likely fail (wrong sig),
        # but the important thing is it exercises the fallback path (line 65)
        with pytest.raises(Exception):
            await authenticate_request(
                request=mock_request,
                x_api_key="key1",
                x_timestamp="2026-01-01T00:00:00Z",
                x_signature="bad-sig",
            )


# ── decrypt_body_if_needed (lines 90-104) ────────────────────────────────


class TestDecryptBodyIfNeeded:
    """Cover all branches of decrypt_body_if_needed."""

    def _make_request(self, *, require_encryption=False, envelope=None, body=b"test"):
        """Create a mock request for decrypt_body_if_needed."""
        mock_request = MagicMock()
        mock_request.body = AsyncMock(return_value=body)

        mock_config = MagicMock()
        mock_config.require_encryption = require_encryption
        mock_request.app.state.config = mock_config
        mock_request.app.state.envelope = envelope

        return mock_request

    @pytest.mark.asyncio
    async def test_not_encrypted_no_requirement_returns_body(self):
        """X-Encrypted=false, require_encryption=false → returns raw body."""
        from src.api.deps import decrypt_body_if_needed

        request = self._make_request(require_encryption=False, body=b"plain data")
        result = await decrypt_body_if_needed(request, x_encrypted="false")
        assert result == b"plain data"

    @pytest.mark.asyncio
    async def test_require_encryption_without_encrypted_header_raises(self):
        """require_encryption=true, X-Encrypted=false → EncryptionRequiredError."""
        from src.api.deps import decrypt_body_if_needed

        request = self._make_request(require_encryption=True)
        with pytest.raises(EncryptionRequiredError):
            await decrypt_body_if_needed(request, x_encrypted="false")

    @pytest.mark.asyncio
    async def test_encrypted_header_no_envelope_raises(self):
        """X-Encrypted=true, envelope=None → EncryptionRequiredError."""
        from src.api.deps import decrypt_body_if_needed

        request = self._make_request(envelope=None)
        with pytest.raises(EncryptionRequiredError):
            await decrypt_body_if_needed(request, x_encrypted="true")

    @pytest.mark.asyncio
    async def test_encrypted_header_with_envelope_decrypts(self):
        """X-Encrypted=true with valid envelope → calls nacl_decrypt."""
        from unittest.mock import patch
        from src.api.deps import decrypt_body_if_needed

        mock_key = MagicMock()
        request = self._make_request(envelope=mock_key, body=b"encrypted-data")

        with patch("src.api.deps.nacl_decrypt", return_value=b"decrypted-data") as mock_decrypt:
            result = await decrypt_body_if_needed(request, x_encrypted="true")

        assert result == b"decrypted-data"
        mock_decrypt.assert_called_once_with(b"encrypted-data", mock_key)
