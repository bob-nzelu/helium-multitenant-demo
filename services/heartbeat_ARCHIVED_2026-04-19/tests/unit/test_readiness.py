"""
Tests for GET /api/status/readiness endpoint.

Tests cover:
    - Ready when HeartBeat healthy + all managed services healthy
    - Not ready when managed service unhealthy
    - Ready with no managed services
    - Degraded HeartBeat
    - KeepAlive manager unavailable (graceful fallback)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestReadinessEndpoint:
    @pytest.mark.asyncio
    async def test_ready_no_managed_services(self):
        """HeartBeat healthy + no managed services = ready."""
        from src.api.internal.readiness import get_readiness

        with patch("src.api.internal.readiness.get_config") as mock_config:
            mock_config.return_value.tier = "test"

            with patch("src.api.internal.readiness.get_blob_database") as mock_db:
                mock_db.return_value.execute_query.return_value = [{"count": 1}]

                with patch(
                    "src.keepalive.manager.get_keepalive_manager"
                ) as mock_ka:
                    manager = MagicMock()
                    manager.get_status = AsyncMock(
                        return_value={"services": {}, "total": 0, "healthy": 0}
                    )
                    mock_ka.return_value = manager

                    result = await get_readiness()

        assert result["ready"] is True
        assert result["tier"] == "test"
        assert "heartbeat" in result["services"]
        assert result["services"]["heartbeat"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_ready_all_services_healthy(self):
        from src.api.internal.readiness import get_readiness

        with patch("src.api.internal.readiness.get_config") as mock_config:
            mock_config.return_value.tier = "standard"

            with patch("src.api.internal.readiness.get_blob_database") as mock_db:
                mock_db.return_value.execute_query.return_value = [{"count": 1}]

                with patch(
                    "src.keepalive.manager.get_keepalive_manager"
                ) as mock_ka:
                    manager = MagicMock()
                    manager.get_status = AsyncMock(
                        return_value={
                            "services": {
                                "core": {"status": "healthy", "pid": 1234},
                                "relay": {"status": "healthy", "pid": 5678},
                            },
                            "total": 2,
                            "healthy": 2,
                        }
                    )
                    mock_ka.return_value = manager

                    result = await get_readiness()

        assert result["ready"] is True
        assert result["total_services"] == 3  # 2 managed + heartbeat
        assert result["healthy_services"] == 3

    @pytest.mark.asyncio
    async def test_not_ready_service_unhealthy(self):
        from src.api.internal.readiness import get_readiness

        with patch("src.api.internal.readiness.get_config") as mock_config:
            mock_config.return_value.tier = "standard"

            with patch("src.api.internal.readiness.get_blob_database") as mock_db:
                mock_db.return_value.execute_query.return_value = [{"count": 1}]

                with patch(
                    "src.keepalive.manager.get_keepalive_manager"
                ) as mock_ka:
                    manager = MagicMock()
                    manager.get_status = AsyncMock(
                        return_value={
                            "services": {
                                "core": {"status": "healthy", "pid": 1234},
                                "relay": {"status": "unhealthy", "pid": None},
                            },
                            "total": 2,
                            "healthy": 1,
                        }
                    )
                    mock_ka.return_value = manager

                    result = await get_readiness()

        assert result["ready"] is False
        assert result["healthy_services"] == 2  # heartbeat + core

    @pytest.mark.asyncio
    async def test_degraded_heartbeat(self):
        from src.api.internal.readiness import get_readiness

        with patch("src.api.internal.readiness.get_config") as mock_config:
            mock_config.return_value.tier = "test"

            with patch("src.api.internal.readiness.get_blob_database") as mock_db:
                mock_db.return_value.execute_query.side_effect = Exception("db error")

                with patch(
                    "src.keepalive.manager.get_keepalive_manager"
                ) as mock_ka:
                    manager = MagicMock()
                    manager.get_status = AsyncMock(
                        return_value={"services": {}, "total": 0, "healthy": 0}
                    )
                    mock_ka.return_value = manager

                    result = await get_readiness()

        assert result["ready"] is False
        assert result["services"]["heartbeat"]["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_keepalive_unavailable(self):
        """Graceful fallback when KeepAlive manager not initialized."""
        from src.api.internal.readiness import get_readiness

        with patch("src.api.internal.readiness.get_config") as mock_config:
            mock_config.return_value.tier = "test"

            with patch("src.api.internal.readiness.get_blob_database") as mock_db:
                mock_db.return_value.execute_query.return_value = [{"count": 1}]

                # Simulate import error / uninitialized manager
                with patch(
                    "src.keepalive.manager.get_keepalive_manager",
                    side_effect=RuntimeError("not initialized"),
                ):
                    result = await get_readiness()

        # Should still return a valid response
        assert result["ready"] is True  # HeartBeat healthy, no managed services found
        assert "heartbeat" in result["services"]
