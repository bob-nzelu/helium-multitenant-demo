"""
Tests for GET /metrics endpoint.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-metrics-test",
        require_encryption=False,
        internal_service_token="test-token",
    )


@pytest.fixture
async def client(test_config):
    app = create_app(config=test_config, api_key_secrets={})
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    @pytest.mark.asyncio
    async def test_metrics_returns_200(self, client):
        """Metrics endpoint returns 200."""
        response = await client.get("/metrics")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_content_type_is_text(self, client):
        """Response content type is text/plain (Prometheus format)."""
        response = await client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_metrics_contains_info_metric(self, client):
        """Response contains helium_relay_info metric."""
        response = await client.get("/metrics")
        text = response.text
        assert "helium_relay_info" in text

    @pytest.mark.asyncio
    async def test_metrics_contains_up_metric(self, client):
        """Response contains helium_relay_up metric."""
        response = await client.get("/metrics")
        text = response.text
        assert "helium_relay_up 1" in text

    @pytest.mark.asyncio
    async def test_metrics_contains_module_cache_metric(self, client):
        """Response contains module cache loaded metric."""
        response = await client.get("/metrics")
        text = response.text
        assert "helium_relay_module_cache_loaded" in text

    @pytest.mark.asyncio
    async def test_metrics_contains_redis_metric(self, client):
        """Response contains redis connected metric."""
        response = await client.get("/metrics")
        text = response.text
        assert "helium_relay_redis_connected" in text

    @pytest.mark.asyncio
    async def test_metrics_instance_id_in_info(self, client):
        """Info metric contains instance_id label."""
        response = await client.get("/metrics")
        text = response.text
        assert 'instance_id="relay-metrics-test"' in text

    @pytest.mark.asyncio
    async def test_metrics_has_help_lines(self, client):
        """Prometheus format includes HELP comments."""
        response = await client.get("/metrics")
        text = response.text
        assert "# HELP" in text

    @pytest.mark.asyncio
    async def test_metrics_has_type_lines(self, client):
        """Prometheus format includes TYPE declarations."""
        response = await client.get("/metrics")
        text = response.text
        assert "# TYPE" in text

    @pytest.mark.asyncio
    async def test_metrics_no_auth_required(self, client):
        """Metrics endpoint does not require authentication."""
        response = await client.get("/metrics")
        assert response.status_code == 200
