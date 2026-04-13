"""
Tests for Lifecycle API endpoints.

Tests cover:
    - GET /api/lifecycle/services — list all
    - GET /api/lifecycle/services/{name} — single + not found
    - POST /api/lifecycle/services/{name}/start
    - POST /api/lifecycle/services/{name}/stop
    - POST /api/lifecycle/services/{name}/restart
    - GET /api/lifecycle/startup-order
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.internal.lifecycle import router


# Create a minimal test app with the lifecycle router
_test_app = FastAPI()
_test_app.include_router(router)


@pytest.fixture
def lifecycle_client():
    """Test client with auth bypassed."""
    # Override the auth dependency to skip credential verification
    from src.auth.dependencies import verify_service_credentials

    _test_app.dependency_overrides[verify_service_credentials] = lambda: {
        "service_name": "admin",
        "role": "admin",
    }

    with TestClient(_test_app) as c:
        yield c

    _test_app.dependency_overrides.clear()


class TestListServices:
    def test_list_services(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.get_status = AsyncMock(
                return_value={
                    "services": {
                        "core": {"status": "healthy", "pid": 1234},
                    },
                    "total": 1,
                    "healthy": 1,
                    "unhealthy": 0,
                }
            )
            mock_ka.return_value = manager

            response = lifecycle_client.get("/api/lifecycle/services")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert "core" in data["services"]


class TestGetService:
    def test_get_existing_service(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.get_status = AsyncMock(
                return_value={
                    "services": {
                        "core": {"status": "healthy", "pid": 1234},
                    },
                }
            )
            mock_ka.return_value = manager

            response = lifecycle_client.get("/api/lifecycle/services/core")

        assert response.status_code == 200
        data = response.json()
        assert data["service_name"] == "core"
        assert data["source"] == "manager"

    def test_get_service_from_db(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.get_status = AsyncMock(
                return_value={"services": {}}
            )
            mock_ka.return_value = manager

            with patch(
                "src.database.registry.get_registry_database"
            ) as mock_db:
                mock_db.return_value.get_managed_service.return_value = {
                    "current_status": "stopped",
                    "current_pid": None,
                    "restart_count": 0,
                    "startup_priority": 1,
                    "auto_start": 1,
                    "auto_restart": 1,
                    "restart_policy": "immediate_3",
                    "health_endpoint": "http://localhost:8000/health",
                }

                response = lifecycle_client.get("/api/lifecycle/services/core")

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "database"

    def test_get_service_not_found(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.get_status = AsyncMock(
                return_value={"services": {}}
            )
            mock_ka.return_value = manager

            with patch(
                "src.database.registry.get_registry_database"
            ) as mock_db:
                mock_db.return_value.get_managed_service.return_value = None

                response = lifecycle_client.get(
                    "/api/lifecycle/services/nonexistent"
                )

        assert response.status_code == 404


class TestStartService:
    def test_start_success(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.start_service = AsyncMock(
                return_value={"status": "started", "service_name": "core", "pid": 1234}
            )
            mock_ka.return_value = manager

            response = lifecycle_client.post("/api/lifecycle/services/core/start")

        assert response.status_code == 200
        assert response.json()["status"] == "started"

    def test_start_error(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.start_service = AsyncMock(
                return_value={"status": "error", "message": "not found"}
            )
            mock_ka.return_value = manager

            response = lifecycle_client.post(
                "/api/lifecycle/services/nonexistent/start"
            )

        assert response.status_code == 400


class TestStopService:
    def test_stop_success(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.stop_service = AsyncMock(
                return_value={"status": "stopped", "service_name": "core"}
            )
            mock_ka.return_value = manager

            response = lifecycle_client.post("/api/lifecycle/services/core/stop")

        assert response.status_code == 200
        assert response.json()["status"] == "stopped"


class TestRestartService:
    def test_restart_success(self, lifecycle_client):
        with patch(
            "src.keepalive.manager.get_keepalive_manager"
        ) as mock_ka:
            manager = MagicMock()
            manager.restart_service = AsyncMock(
                return_value={"status": "started", "service_name": "core", "pid": 9999}
            )
            mock_ka.return_value = manager

            response = lifecycle_client.post(
                "/api/lifecycle/services/core/restart"
            )

        assert response.status_code == 200
        assert response.json()["status"] == "started"


class TestStartupOrder:
    def test_get_startup_order(self, lifecycle_client):
        with patch(
            "src.database.registry.get_registry_database"
        ) as mock_db:
            mock_db.return_value.get_startup_order.return_value = [
                {"service_name": "core", "startup_priority": 1},
                {"service_name": "relay", "startup_priority": 2},
            ]

            response = lifecycle_client.get("/api/lifecycle/startup-order")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["startup_order"][0]["service_name"] == "core"
