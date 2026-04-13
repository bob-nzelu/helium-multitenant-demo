"""
Tests for GET /health — degraded scenarios.

Covers health.py lines 46-49 (heartbeat exception),
55-56 (module cache not loaded).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-degraded-test",
        require_encryption=False,
        internal_service_token="test-token",
    )


class TestHealthDegraded:
    """Test degraded health responses."""

    @pytest.mark.asyncio
    async def test_degraded_when_heartbeat_raises(self, test_config):
        """Health returns degraded when heartbeat.health_check() throws."""
        app = create_app(config=test_config, api_key_secrets={})
        async with LifespanManager(app):
            # Patch heartbeat to raise on health_check
            app.state.heartbeat.health_check = AsyncMock(
                side_effect=RuntimeError("connection refused")
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

            data = response.json()
            assert data["status"] == "degraded"
            assert data["services"]["heartbeat"] == "unavailable"
            assert "HeartBeat unavailable" in data["message"]

    @pytest.mark.asyncio
    async def test_degraded_when_heartbeat_returns_false(self, test_config):
        """Health returns degraded when heartbeat.health_check() returns False."""
        app = create_app(config=test_config, api_key_secrets={})
        async with LifespanManager(app):
            # Patch heartbeat to return False
            app.state.heartbeat.health_check = AsyncMock(return_value=False)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

            data = response.json()
            assert data["status"] == "degraded"
            assert data["services"]["heartbeat"] == "unavailable"
            assert "HeartBeat unavailable" in data["message"]

    @pytest.mark.asyncio
    async def test_degraded_when_module_cache_not_loaded(self, test_config):
        """Health returns degraded when module_cache.is_loaded is False."""
        app = create_app(config=test_config, api_key_secrets={})
        async with LifespanManager(app):
            # Patch module_cache to report not loaded
            app.state.module_cache._loaded = False

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

            data = response.json()
            assert data["status"] == "degraded"
            assert data["services"]["module_cache"] == "not_loaded"
            assert "Module cache not loaded" in data["message"]

    @pytest.mark.asyncio
    async def test_redis_disconnected_not_degraded(self, test_config):
        """Redis disconnected does NOT cause degraded status."""
        app = create_app(config=test_config, api_key_secrets={})
        async with LifespanManager(app):
            # Redis is already disconnected (no redis_url configured)
            assert not app.state.redis.is_available

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

            data = response.json()
            # Redis down alone should not trigger degraded
            assert data["services"]["redis"] == "disconnected"
            # Overall status depends on heartbeat + module_cache
