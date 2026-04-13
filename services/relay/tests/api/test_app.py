"""
Tests for create_app and lifespan

HeartBeat HTTP mocking is handled by the autouse fixture in tests/api/conftest.py.
"""

import pytest
from asgi_lifespan import LifespanManager

from src.api.app import create_app
from src.config import RelayConfig


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
        internal_service_token="test-internal-token",
    )


@pytest.fixture
def test_secrets():
    return {
        "test-key-001": "secret-001",
    }


class TestCreateApp:
    """Test app factory."""

    def test_create_app_returns_fastapi(self, test_config, test_secrets):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        assert app.title == "Relay-API"

    def test_create_app_has_routes(self, test_config, test_secrets):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/ingest" in route_paths
        assert "/internal/refresh-cache" in route_paths


class TestLifespan:
    """Test startup/shutdown lifecycle."""

    @pytest.mark.asyncio
    async def test_lifespan_loads_module_cache(self, test_config, test_secrets):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        async with LifespanManager(app):
            assert app.state.module_cache.is_loaded is True
            assert app.state.bulk_service is not None
            assert app.state.external_service is not None

    @pytest.mark.asyncio
    async def test_lifespan_stores_config(self, test_config, test_secrets):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        async with LifespanManager(app):
            assert app.state.config.instance_id == "relay-test"

    @pytest.mark.asyncio
    async def test_lifespan_cleanup(self, test_config, test_secrets):
        app = create_app(config=test_config, api_key_secrets=test_secrets)
        async with LifespanManager(app):
            cache = app.state.module_cache
            assert cache.is_loaded is True
        # After exit, cleanup should have run
        assert cache.is_loaded is False
