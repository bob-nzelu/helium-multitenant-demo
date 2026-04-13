"""
Tests for Root Health and Info Endpoints

Tests GET /health and GET / endpoints.
"""

import pytest


class TestHealthCheck:
    """Tests for GET /health."""

    def test_health_returns_200(self, client, mock_storage):
        """Health endpoint returns 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_has_required_fields(self, client, mock_storage):
        """Health response includes all required fields."""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "mode" in data
        assert "service" in data
        assert "storage" in data
        assert "database" in data
        assert "timestamp" in data

    def test_health_mode_is_primary(self, client, mock_storage):
        """Health shows mode=primary in test config."""
        data = client.get("/health").json()
        assert data["mode"] == "primary"
        assert data["service"] == "heartbeat"

    def test_health_database_connected(self, client, mock_storage):
        """Database shows connected when working."""
        data = client.get("/health").json()
        assert data["database"] == "connected"

    def test_health_storage_connected(self, client, mock_storage):
        """Storage shows connected when mock is healthy."""
        data = client.get("/health").json()
        assert data["storage"] == "connected"

    def test_health_storage_disconnected(self, client, mock_storage):
        """Storage shows disconnected when unhealthy."""
        mock_storage._healthy = False
        data = client.get("/health").json()
        assert data["storage"] == "disconnected"
        assert data["status"] == "degraded"


class TestRootEndpoint:
    """Tests for GET /."""

    def test_root_returns_200(self, client, mock_storage):
        """Root endpoint returns 200."""
        response = client.get("/")
        assert response.status_code == 200

    def test_root_has_service_info(self, client, mock_storage):
        """Root shows service name and version."""
        data = client.get("/").json()
        assert data["service"] == "heartbeat"
        assert data["version"] == "2.0.0"
        assert data["mode"] == "primary"

    def test_root_lists_endpoints(self, client, mock_storage):
        """Root lists available endpoints."""
        data = client.get("/").json()
        assert "endpoints" in data
        assert "blob_write" in data["endpoints"]
        assert "dedup_check" in data["endpoints"]
        assert "audit_log" in data["endpoints"]
        assert "registry_register" in data["endpoints"]
        assert "registry_discover" in data["endpoints"]
