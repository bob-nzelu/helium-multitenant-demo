"""
Tests for Registry API Endpoints (src/api/internal/registry.py)

Uses FastAPI TestClient with test databases.

Covers:
- POST /api/registry/register (service self-registration)
- GET  /api/registry/discover (full catalog)
- GET  /api/registry/discover/{service_name}
- POST /api/registry/health/{instance_id}
- GET  /api/registry/config/{service_name}
- POST /api/registry/credentials/generate
- POST /api/registry/credentials/{id}/rotate
- POST /api/registry/credentials/{id}/revoke
- GET  /api/registry/credentials/{service_name} (list)
"""

import pytest


# ── Registration Tests ───────────────────────────────────────────────


class TestRegistration:
    """POST /api/registry/register"""

    def test_register_service_success(self, registry_client):
        """Register a service with endpoints."""
        response = registry_client.post("/api/registry/register", json={
            "service_instance_id": "relay-bulk-1",
            "service_name": "relay",
            "display_name": "Relay Bulk Upload",
            "base_url": "http://127.0.0.1:8082",
            "health_url": "http://127.0.0.1:8082/health",
            "endpoints": [
                {"method": "POST", "path": "/api/v1/upload", "description": "Upload files"},
                {"method": "GET", "path": "/api/v1/status", "description": "Check status"},
            ],
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "registered"
        assert data["endpoints_registered"] == 2
        assert data["instance"] is not None
        assert "catalog" in data

    def test_register_service_minimal(self, registry_client):
        """Register with no endpoints."""
        response = registry_client.post("/api/registry/register", json={
            "service_instance_id": "core-primary",
            "service_name": "core",
            "display_name": "Core Processing",
            "base_url": "http://127.0.0.1:8080",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["endpoints_registered"] == 0

    def test_register_service_re_register(self, registry_client):
        """Re-registering updates the instance (upsert)."""
        payload = {
            "service_instance_id": "relay-bulk-1",
            "service_name": "relay",
            "display_name": "Relay v1",
            "base_url": "http://127.0.0.1:8082",
        }
        registry_client.post("/api/registry/register", json=payload)

        payload["display_name"] = "Relay v2"
        payload["base_url"] = "http://127.0.0.1:9999"
        response = registry_client.post("/api/registry/register", json=payload)
        assert response.status_code == 200
        assert response.json()["instance"]["base_url"] == "http://127.0.0.1:9999"

    def test_register_service_invalid_payload(self, registry_client):
        """Missing required fields returns 422."""
        response = registry_client.post("/api/registry/register", json={
            "service_instance_id": "relay-1",
            # Missing: service_name, display_name, base_url
        })
        assert response.status_code == 422


# ── Discovery Tests ──────────────────────────────────────────────────


class TestDiscovery:
    """GET /api/registry/discover and /discover/{service_name}"""

    def _register_services(self, client):
        """Helper: register relay and core."""
        client.post("/api/registry/register", json={
            "service_instance_id": "relay-bulk-1",
            "service_name": "relay",
            "display_name": "Relay Bulk",
            "base_url": "http://127.0.0.1:8082",
            "endpoints": [
                {"method": "POST", "path": "/upload"},
            ],
        })
        client.post("/api/registry/register", json={
            "service_instance_id": "core-primary",
            "service_name": "core",
            "display_name": "Core",
            "base_url": "http://127.0.0.1:8080",
            "endpoints": [
                {"method": "POST", "path": "/enqueue"},
            ],
        })

    def test_discover_all(self, registry_client):
        """GET /discover returns all services."""
        self._register_services(registry_client)

        response = registry_client.get("/api/registry/discover")
        assert response.status_code == 200
        data = response.json()
        assert "services" in data
        assert "catalog" in data
        assert len(data["catalog"]) >= 2

    def test_discover_specific_service(self, registry_client):
        """GET /discover/{name} returns that service's instances and endpoints."""
        self._register_services(registry_client)

        response = registry_client.get("/api/registry/discover/relay")
        assert response.status_code == 200
        data = response.json()
        assert data["service_name"] == "relay"
        assert len(data["instances"]) == 1
        assert len(data["endpoints"]) >= 1

    def test_discover_nonexistent_service(self, registry_client):
        """GET /discover/{unknown} returns 404."""
        response = registry_client.get("/api/registry/discover/unknown-service")
        assert response.status_code == 404

    def test_discover_empty_registry(self, registry_client):
        """GET /discover with empty registry returns empty catalog."""
        response = registry_client.get("/api/registry/discover")
        assert response.status_code == 200
        data = response.json()
        assert data["catalog"] == []


# ── Health Reporting Tests ───────────────────────────────────────────


class TestHealthReporting:
    """POST /api/registry/health/{instance_id}"""

    def test_report_health_success(self, registry_client):
        """Report healthy status for a registered instance."""
        registry_client.post("/api/registry/register", json={
            "service_instance_id": "relay-1",
            "service_name": "relay",
            "display_name": "Relay",
            "base_url": "http://127.0.0.1:8082",
        })

        response = registry_client.post(
            "/api/registry/health/relay-1",
            json={"status": "healthy"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is True
        assert data["status"] == "healthy"

    def test_report_degraded_status(self, registry_client):
        """Report degraded status."""
        registry_client.post("/api/registry/register", json={
            "service_instance_id": "core-1",
            "service_name": "core",
            "display_name": "Core",
            "base_url": "http://127.0.0.1:8080",
        })

        response = registry_client.post(
            "/api/registry/health/core-1",
            json={"status": "degraded"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"

    def test_report_health_nonexistent_instance(self, registry_client):
        """Health report for non-registered instance returns updated=False."""
        response = registry_client.post(
            "/api/registry/health/ghost",
            json={"status": "healthy"},
        )
        assert response.status_code == 200
        assert response.json()["updated"] is False

    def test_report_health_invalid_status(self, registry_client):
        """Invalid health status rejected by pydantic validation."""
        response = registry_client.post(
            "/api/registry/health/some-id",
            json={"status": "invalid-status"},
        )
        assert response.status_code == 422


# ── Service Config Tests ─────────────────────────────────────────────


class TestServiceConfig:
    """GET /api/registry/config/{service_name}"""

    def test_get_config_empty(self, registry_client):
        """Empty config returns empty list."""
        response = registry_client.get("/api/registry/config/relay")
        assert response.status_code == 200
        data = response.json()
        assert data["service_name"] == "relay"
        assert data["config"] == []

    def test_get_config_with_data(self, registry_client, registry_db):
        """Config with pre-set values is returned."""
        # Set config directly on the DB (no config set API yet)
        registry_db.set_config("relay", "max_uploads", "10")
        registry_db.set_config("relay", "timeout", "30")

        response = registry_client.get("/api/registry/config/relay")
        assert response.status_code == 200
        data = response.json()
        assert len(data["config"]) == 2


# ── Credential Generation Tests ──────────────────────────────────────


class TestCredentialGeneration:
    """POST /api/registry/credentials/generate"""

    def test_generate_credential(self, registry_client):
        """Generate a new credential returns key + secret."""
        response = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            "issued_to": "relay-bulk-1",
            "permissions": ["blob.write", "blob.read"],
        })
        assert response.status_code == 201
        data = response.json()
        assert data["api_key"].startswith("rl_test_")
        assert "api_secret" in data  # Plaintext only at creation
        assert data["service_name"] == "relay"
        assert data["issued_to"] == "relay-bulk-1"

    def test_generate_credential_minimal(self, registry_client):
        """Generate with minimal fields."""
        response = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "core",
            "issued_to": "core-primary",
        })
        assert response.status_code == 201
        assert response.json()["api_key"].startswith("cr_test_")

    def test_generate_credential_invalid(self, registry_client):
        """Missing required fields returns 422."""
        response = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            # Missing: issued_to
        })
        assert response.status_code == 422


# ── Credential Rotation Tests ────────────────────────────────────────


class TestCredentialRotation:
    """POST /api/registry/credentials/{id}/rotate"""

    def test_rotate_credential(self, registry_client):
        """Rotate produces new key + secret."""
        # First generate
        gen_resp = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            "issued_to": "relay-1",
        })
        cred_id = gen_resp.json()["credential_id"]
        old_key = gen_resp.json()["api_key"]

        # Rotate
        rot_resp = registry_client.post(f"/api/registry/credentials/{cred_id}/rotate")
        assert rot_resp.status_code == 200
        data = rot_resp.json()
        assert data["new_api_key"] != old_key
        assert "new_api_secret" in data

    def test_rotate_nonexistent_credential(self, registry_client):
        """Rotating non-existent credential returns 404."""
        response = registry_client.post("/api/registry/credentials/nonexistent/rotate")
        assert response.status_code == 404


# ── Credential Revocation Tests ──────────────────────────────────────


class TestCredentialRevocation:
    """POST /api/registry/credentials/{id}/revoke"""

    def test_revoke_credential(self, registry_client):
        """Revoke sets status to 'revoked'."""
        gen_resp = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "edge",
            "issued_to": "edge-primary",
        })
        cred_id = gen_resp.json()["credential_id"]

        rev_resp = registry_client.post(
            f"/api/registry/credentials/{cred_id}/revoke",
            json={"reason": "Compromised key"},
        )
        assert rev_resp.status_code == 200
        assert rev_resp.json()["status"] == "revoked"

    def test_revoke_nonexistent_credential(self, registry_client):
        """Revoking non-existent credential returns 404."""
        response = registry_client.post(
            "/api/registry/credentials/nonexistent/revoke",
            json={},
        )
        assert response.status_code == 404

    def test_revoke_with_no_reason(self, registry_client):
        """Revoke without reason still succeeds."""
        gen_resp = registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            "issued_to": "relay-1",
        })
        cred_id = gen_resp.json()["credential_id"]

        rev_resp = registry_client.post(
            f"/api/registry/credentials/{cred_id}/revoke",
            json={},
        )
        assert rev_resp.status_code == 200


# ── Credential Listing Tests ─────────────────────────────────────────


class TestCredentialListing:
    """GET /api/registry/credentials/{service_name}"""

    def test_list_credentials_empty(self, registry_client):
        """No credentials for a service returns empty list."""
        response = registry_client.get("/api/registry/credentials/relay")
        assert response.status_code == 200
        data = response.json()
        assert data["service_name"] == "relay"
        assert data["credentials"] == []

    def test_list_credentials_with_data(self, registry_client):
        """List credentials after generating some."""
        registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            "issued_to": "relay-bulk-1",
        })
        registry_client.post("/api/registry/credentials/generate", json={
            "service_name": "relay",
            "issued_to": "relay-nas-1",
        })

        response = registry_client.get("/api/registry/credentials/relay")
        assert response.status_code == 200
        data = response.json()
        assert len(data["credentials"]) == 2

        # Verify no secret hashes in response
        for cred in data["credentials"]:
            assert "api_secret_hash" not in cred
