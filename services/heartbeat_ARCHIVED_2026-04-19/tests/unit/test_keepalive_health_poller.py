"""
Tests for HealthPoller — polls child service /health endpoints.

Tests cover:
    - check_health() with various responses (200, 500, timeout, connection error)
    - poll_all() concurrent polling
    - Client lifecycle (lazy init, close)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.keepalive.health_poller import HealthPoller
from src.keepalive.process_handle import ProcessHandle


@pytest.fixture
def poller():
    return HealthPoller()


@pytest.fixture
def mock_handle():
    h = MagicMock(spec=ProcessHandle)
    h.service_name = "core"
    h.health_endpoint = "http://localhost:8000/health"
    h.status = "healthy"
    h.is_alive.return_value = True
    return h


class TestCheckHealth:
    @pytest.mark.asyncio
    async def test_healthy_response(self, poller):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_response(self, poller):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "degraded"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "degraded"

    @pytest.mark.asyncio
    async def test_unhealthy_status_in_json(self, poller):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "error"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "unhealthy"

    @pytest.mark.asyncio
    async def test_non_200_response(self, poller):
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "unhealthy"

    @pytest.mark.asyncio
    async def test_connection_error(self, poller):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "unhealthy"

    @pytest.mark.asyncio
    async def test_timeout_error(self, poller):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timed out")
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "unhealthy"

    @pytest.mark.asyncio
    async def test_200_invalid_json(self, poller):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("bad json")

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.is_closed = False
        poller._client = mock_client

        result = await poller.check_health("core", "http://localhost:8000/health")
        assert result == "healthy"  # 200 with bad JSON = treat as healthy


class TestPollAll:
    @pytest.mark.asyncio
    async def test_poll_all_filters_checkable(self, poller):
        """Only polls services with health_endpoint + checkable status + alive."""
        handles = {
            "core": MagicMock(
                health_endpoint="http://localhost:8000/health",
                status="healthy",
                is_alive=MagicMock(return_value=True),
            ),
            "stopped_svc": MagicMock(
                health_endpoint="http://localhost:8001/health",
                status="stopped",
                is_alive=MagicMock(return_value=False),
            ),
            "no_endpoint": MagicMock(
                health_endpoint=None,
                status="healthy",
                is_alive=MagicMock(return_value=True),
            ),
        }

        with patch.object(poller, "check_health", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = "healthy"
            results = await poller.poll_all(handles)

        # Only "core" should be polled
        assert "core" in results
        assert "stopped_svc" not in results
        assert "no_endpoint" not in results

    @pytest.mark.asyncio
    async def test_poll_all_empty(self, poller):
        results = await poller.poll_all({})
        assert results == {}

    @pytest.mark.asyncio
    async def test_poll_all_exception_handled(self, poller):
        handles = {
            "core": MagicMock(
                health_endpoint="http://localhost:8000/health",
                status="healthy",
                is_alive=MagicMock(return_value=True),
            ),
        }

        with patch.object(poller, "check_health", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = Exception("network error")
            results = await poller.poll_all(handles)

        assert results["core"] == "unhealthy"


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_lazy_init(self, poller):
        assert poller._client is None
        client = await poller._get_client()
        assert client is not None
        await poller.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, poller):
        await poller.close()  # No client yet
        poller._client = AsyncMock()
        poller._client.is_closed = False
        await poller.close()
        assert poller._client is None

    @pytest.mark.asyncio
    async def test_recreates_closed_client(self, poller):
        mock_client = AsyncMock()
        mock_client.is_closed = True
        poller._client = mock_client

        # Should create a new client since the old one is closed
        with patch("httpx.AsyncClient") as MockClient:
            new_client = AsyncMock()
            new_client.is_closed = False
            MockClient.return_value = new_client
            client = await poller._get_client()
            assert client is new_client
            await poller.close()
