"""
Tests for GET /health endpoint.

HeartBeat HTTP mocking is handled by the autouse fixture in tests/api/conftest.py.
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
        instance_id="relay-health-test",
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


class TestHealthEndpoint:
    """Tests for GET /health."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        """Health endpoint always returns 200."""
        response = await client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_status_healthy(self, client):
        """With all stubs healthy, returns status=healthy."""
        response = await client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_required_fields(self, client):
        """Response contains all required fields from spec."""
        response = await client.get("/health")
        data = response.json()
        assert "status" in data
        assert "instance_id" in data
        assert "relay_type" in data
        assert "version" in data
        assert "services" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_health_instance_id(self, client):
        """Instance ID matches config."""
        response = await client.get("/health")
        data = response.json()
        assert data["instance_id"] == "relay-health-test"

    @pytest.mark.asyncio
    async def test_health_relay_type_is_bulk(self, client):
        """Relay type is always 'bulk'."""
        response = await client.get("/health")
        data = response.json()
        assert data["relay_type"] == "bulk"

    @pytest.mark.asyncio
    async def test_health_version_matches_app(self, client):
        """Version matches FastAPI app version."""
        response = await client.get("/health")
        data = response.json()
        assert data["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_health_services_contains_heartbeat(self, client):
        """Services dict includes heartbeat status."""
        response = await client.get("/health")
        data = response.json()
        assert "heartbeat" in data["services"]

    @pytest.mark.asyncio
    async def test_health_services_contains_module_cache(self, client):
        """Services dict includes module_cache status."""
        response = await client.get("/health")
        data = response.json()
        assert "module_cache" in data["services"]

    @pytest.mark.asyncio
    async def test_health_services_contains_redis(self, client):
        """Services dict includes redis status."""
        response = await client.get("/health")
        data = response.json()
        assert "redis" in data["services"]

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, client):
        """Health endpoint does not require authentication headers."""
        response = await client.get("/health")
        # Should not return 401/422 for missing auth headers
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_message_none_when_healthy(self, client):
        """Message is null when all services are healthy."""
        response = await client.get("/health")
        data = response.json()
        assert data["message"] is None

    @pytest.mark.asyncio
    async def test_health_timestamp_format(self, client):
        """Timestamp is ISO 8601 UTC format."""
        response = await client.get("/health")
        data = response.json()
        assert data["timestamp"].endswith("Z")
        assert "T" in data["timestamp"]
