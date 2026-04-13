"""
Tests for Architecture Metadata API (Q3)

Tests cover:
    1. GET /api/architecture/services — service boundary definitions
    2. GET /api/architecture/data-flows — blob lifecycle, ingestion, sync
    3. Response structure validation
"""

import pytest


class TestArchitectureServices:
    """Service boundary endpoint tests."""

    def test_get_services(self, client):
        """GET /api/architecture/services returns all services."""
        resp = client.get("/api/architecture/services")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert data["count"] == 6

    def test_services_contain_heartbeat(self, client):
        """Services list includes HeartBeat with correct role."""
        resp = client.get("/api/architecture/services")
        services = resp.json()["services"]
        heartbeat = next(s for s in services if s["name"] == "HeartBeat")
        assert heartbeat["role"] == "Infrastructure Hub"
        assert heartbeat["port"] == 9000
        assert "blob.db" in heartbeat["databases"]

    def test_services_have_required_fields(self, client):
        """Every service has required fields."""
        resp = client.get("/api/architecture/services")
        for svc in resp.json()["services"]:
            assert "name" in svc
            assert "role" in svc
            assert "description" in svc
            assert "owns" in svc
            assert "does_not_own" in svc
            assert "deployment_modes" in svc

    def test_services_all_six_present(self, client):
        """All 6 Helium services are listed."""
        resp = client.get("/api/architecture/services")
        names = {s["name"] for s in resp.json()["services"]}
        assert names == {"HeartBeat", "Relay", "Core", "Edge", "Float", "HIS"}


class TestArchitectureDataFlows:
    """Data flow endpoint tests."""

    def test_get_data_flows(self, client):
        """GET /api/architecture/data-flows returns all flows."""
        resp = client.get("/api/architecture/data-flows")
        assert resp.status_code == 200
        data = resp.json()
        assert "flows" in data
        assert data["count"] == 3

    def test_blob_lifecycle_states(self, client):
        """Blob lifecycle flow has correct states."""
        resp = client.get("/api/architecture/data-flows")
        lifecycle = resp.json()["flows"]["blob_lifecycle"]
        states = {s["state"] for s in lifecycle["states"]}
        assert states == {"uploaded", "processing", "preview_pending", "finalized", "error"}

    def test_ingestion_flow_steps(self, client):
        """Ingestion flow has 7 steps."""
        resp = client.get("/api/architecture/data-flows")
        ingestion = resp.json()["flows"]["ingestion_flow"]
        assert len(ingestion["steps"]) == 7
        assert ingestion["steps"][0]["step"] == 1

    def test_sync_protocol_mechanisms(self, client):
        """Sync protocol lists all synchronization mechanisms."""
        resp = client.get("/api/architecture/data-flows")
        sync = resp.json()["flows"]["sync_protocol"]
        mechanisms = {m["mechanism"] for m in sync["mechanisms"]}
        assert "SSE Event Streaming" in mechanisms
        assert "Service Registry" in mechanisms
        assert "API Contracts" in mechanisms
