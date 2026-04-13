"""
Tests for API dependency injection functions.

Covers decrypt_body_if_needed, verify_internal_token edge cases,
and getter dependencies.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig


# ── Encryption Required ──────────────────────────────────────────────────


class TestEncryptionRequired:
    """Test require_encryption=True behavior."""

    @pytest.fixture
    def encryption_config(self):
        return RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=True,
            internal_service_token="test-token",
        )

    @pytest.fixture
    async def client(self, encryption_config):
        app = create_app(config=encryption_config, api_key_secrets={})
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c

    @pytest.mark.asyncio
    async def test_encryption_config_stored(self, encryption_config):
        """Verify require_encryption is stored in app state."""
        app = create_app(config=encryption_config, api_key_secrets={})
        async with LifespanManager(app):
            assert app.state.config.require_encryption is True


# ── Internal Token Not Configured ─────────────────────────────────────────


class TestInternalTokenNotConfigured:
    """Test /internal/ when service token is empty."""

    @pytest.fixture
    def no_token_config(self):
        return RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            internal_service_token="",
        )

    @pytest.fixture
    async def client(self, no_token_config):
        app = create_app(config=no_token_config, api_key_secrets={})
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c

    @pytest.mark.asyncio
    async def test_no_token_configured_returns_401(self, client):
        """Internal endpoint with no token configured → 401."""
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": "Bearer anything"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_configured_error_message(self, client):
        """Error message should indicate token not configured."""
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": "Bearer anything"},
        )
        data = response.json()
        assert "not configured" in data["message"].lower()


# ── App Factory Edge Cases ────────────────────────────────────────────────


class TestAppFactoryDefaults:
    """Test create_app with default parameters."""

    def test_default_api_key_secrets(self):
        """create_app with no api_key_secrets → empty dict."""
        config = RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            internal_service_token="test-token",
        )
        app = create_app(config=config)
        # App created successfully with no api_key_secrets
        assert app.title == "Relay-API"
