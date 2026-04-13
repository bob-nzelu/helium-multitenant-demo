"""
Tests for POST /internal/refresh-cache route
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig


# ── Fixtures ──────────────────────────────────────────────────────────────


INTERNAL_TOKEN = "test-internal-token"


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        internal_service_token=INTERNAL_TOKEN,
    )


@pytest.fixture
async def client(test_config):
    app = create_app(config=test_config, api_key_secrets={})
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ── Valid Token ──────────────────────────────────────────────────────────


class TestRefreshCacheValid:
    """Test /internal/refresh-cache with valid token."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_200(self, client):
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_token_response_shape(self, client):
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        )
        data = response.json()
        assert data["status"] == "ok"
        assert "modules_updated" in data
        assert "keys_updated" in data

    @pytest.mark.asyncio
    async def test_refresh_returns_no_updates(self, client):
        """First refresh after startup → checksums match → no updates."""
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        )
        data = response.json()
        assert data["modules_updated"] == []
        assert data["keys_updated"] is False


# ── Invalid Token ────────────────────────────────────────────────────────


class TestRefreshCacheInvalid:
    """Test /internal/refresh-cache authentication failures."""

    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(self, client):
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_422(self, client):
        response = await client.post("/internal/refresh-cache")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_malformed_auth_header_returns_401(self, client):
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": "Basic dGVzdA=="},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_no_bearer_prefix_returns_401(self, client):
        response = await client.post(
            "/internal/refresh-cache",
            headers={"Authorization": INTERNAL_TOKEN},
        )
        assert response.status_code == 401


# ── Method Not Allowed ───────────────────────────────────────────────────


class TestRefreshCacheMethod:
    """Test wrong HTTP methods."""

    @pytest.mark.asyncio
    async def test_get_returns_405(self, client):
        response = await client.get(
            "/internal/refresh-cache",
            headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        )
        assert response.status_code == 405
