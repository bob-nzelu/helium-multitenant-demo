"""
Coverage tests for src/api/app.py.

Targets uncovered lines: 54 (config=None → from_env), 62-63 (dev API key loading),
100 (module cache not loaded warning).
"""

import os
import pytest
from unittest.mock import patch
from asgi_lifespan import LifespanManager

from src.api.app import create_app
from src.config import RelayConfig


class TestCreateAppDefaults:
    """Cover create_app edge cases."""

    @pytest.mark.asyncio
    async def test_create_app_no_config_uses_from_env(self):
        """create_app(config=None) calls RelayConfig.from_env()."""
        # Set minimum env vars so from_env() works predictably
        env = {
            "RELAY_HOST": "127.0.0.1",
            "RELAY_PORT": "8082",
            "RELAY_REQUIRE_ENCRYPTION": "false",
            "RELAY_INTERNAL_SERVICE_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=False):
            app = create_app(config=None, api_key_secrets={})
            async with LifespanManager(app):
                assert app.state.config.host == "127.0.0.1"
                assert app.state.config.port == 8082

    @pytest.mark.asyncio
    async def test_create_app_loads_dev_api_key(self):
        """create_app with api_key_secrets=None loads dev key from env."""
        env = {
            "RELAY_DEV_API_KEY": "dev-key-12345",
            "RELAY_DEV_API_SECRET": "dev-secret-12345",
        }
        config = RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            internal_service_token="test-token",
        )
        with patch.dict(os.environ, env, clear=False):
            app = create_app(config=config, api_key_secrets=None)
            async with LifespanManager(app):
                assert "dev-key-12345" in app.state.api_key_secrets
                assert app.state.api_key_secrets["dev-key-12345"] == "dev-secret-12345"

    @pytest.mark.asyncio
    async def test_create_app_no_dev_keys_no_env(self):
        """create_app with no dev keys in env → empty api_key_secrets."""
        config = RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-test",
            require_encryption=False,
            internal_service_token="test-token",
        )
        # Ensure dev keys are not set
        env_remove = {"RELAY_DEV_API_KEY": "", "RELAY_DEV_API_SECRET": ""}
        with patch.dict(os.environ, env_remove, clear=False):
            app = create_app(config=config, api_key_secrets=None)
            async with LifespanManager(app):
                # Empty string keys are falsy, so no dev key loaded
                assert app.state.api_key_secrets == {}
