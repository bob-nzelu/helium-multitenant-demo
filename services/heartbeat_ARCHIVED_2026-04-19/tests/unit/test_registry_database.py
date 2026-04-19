"""
Tests for RegistryDatabase (src/database/registry.py)

Covers:
- Service instance CRUD (register, get, deactivate, health)
- Endpoint catalog (register, query, full catalog)
- Credential CRUD (create, lookup, rotate, status, last_used)
- Key rotation log
- Service config (get, set, upsert)
- Singleton lifecycle
"""

import json
import pytest

from src.database.registry import (
    RegistryDatabase,
    get_registry_database,
    set_registry_database,
    reset_registry_database,
)


# ── Service Instance Tests ────────────────────────────────────────────


class TestServiceInstances:
    """Service instance registration, discovery, and lifecycle."""

    def test_register_new_instance(self, registry_db):
        """Register a new service instance."""
        result = registry_db.register_instance(
            instance_id="relay-bulk-1",
            service_name="relay",
            display_name="Relay Bulk Upload",
            base_url="http://127.0.0.1:8082",
            health_url="http://127.0.0.1:8082/health",
        )
        assert result >= 1

        instance = registry_db.get_instance("relay-bulk-1")
        assert instance is not None
        assert instance["service_name"] == "relay"
        assert instance["display_name"] == "Relay Bulk Upload"
        assert instance["base_url"] == "http://127.0.0.1:8082"
        assert instance["is_active"] == 1

    def test_register_instance_upsert(self, registry_db):
        """Re-registering an instance updates its fields (upsert)."""
        registry_db.register_instance(
            instance_id="core-primary",
            service_name="core",
            display_name="Core v1",
            base_url="http://127.0.0.1:8080",
        )

        registry_db.register_instance(
            instance_id="core-primary",
            service_name="core",
            display_name="Core v2",
            base_url="http://127.0.0.1:9090",
            version="3.0.0",
        )

        instance = registry_db.get_instance("core-primary")
        assert instance["display_name"] == "Core v2"
        assert instance["base_url"] == "http://127.0.0.1:9090"
        assert instance["version"] == "3.0.0"

    def test_get_instances_by_service(self, registry_db):
        """Query instances filtered by service_name."""
        registry_db.register_instance("relay-bulk-1", "relay", "Bulk 1", "http://a:1")
        registry_db.register_instance("relay-nas-1", "relay", "NAS 1", "http://a:2")
        registry_db.register_instance("core-primary", "core", "Core", "http://b:1")

        relays = registry_db.get_instances_by_service("relay")
        assert len(relays) == 2

        cores = registry_db.get_instances_by_service("core")
        assert len(cores) == 1
        assert cores[0]["service_instance_id"] == "core-primary"

    def test_get_instances_active_only(self, registry_db):
        """Deactivated instances are excluded by default."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_instance("relay-2", "relay", "R2", "http://a:2")
        registry_db.deactivate_instance("relay-2")

        active = registry_db.get_instances_by_service("relay", active_only=True)
        assert len(active) == 1
        assert active[0]["service_instance_id"] == "relay-1"

        all_inst = registry_db.get_instances_by_service("relay", active_only=False)
        assert len(all_inst) == 2

    def test_get_instance_not_found(self, registry_db):
        """Querying a non-existent instance returns None."""
        assert registry_db.get_instance("nonexistent") is None

    def test_get_all_instances(self, registry_db):
        """Get all registered instances."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_instance("core-1", "core", "C1", "http://b:1")

        all_inst = registry_db.get_all_instances()
        assert len(all_inst) == 2

    def test_deactivate_instance(self, registry_db):
        """Deactivating sets is_active=0."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        updated = registry_db.deactivate_instance("relay-1")
        assert updated == 1

        inst = registry_db.get_instance("relay-1")
        assert inst["is_active"] == 0

    def test_deactivate_nonexistent_instance(self, registry_db):
        """Deactivating non-existent instance returns 0."""
        assert registry_db.deactivate_instance("ghost") == 0

    def test_update_health_status(self, registry_db):
        """Health status updates are recorded."""
        registry_db.register_instance("core-1", "core", "Core", "http://b:1")
        updated = registry_db.update_health_status("core-1", "healthy")
        assert updated == 1

        inst = registry_db.get_instance("core-1")
        assert inst["last_health_status"] == "healthy"
        assert inst["last_health_check_at"] is not None

    def test_update_health_nonexistent(self, registry_db):
        """Health update on non-existent instance returns 0."""
        assert registry_db.update_health_status("ghost", "down") == 0


# ── Endpoint Catalog Tests ───────────────────────────────────────────


class TestEndpointCatalog:
    """Endpoint registration and discovery."""

    def test_register_endpoints(self, registry_db):
        """Register endpoints for an instance."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        count = registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/api/v1/upload", "description": "Upload files"},
            {"method": "GET", "path": "/api/v1/status", "description": "Check status"},
        ])
        assert count == 2

    def test_register_endpoints_replaces_existing(self, registry_db):
        """Re-registering endpoints replaces the old set."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/api/v1/upload"},
            {"method": "GET", "path": "/api/v1/status"},
        ])

        # Replace with 1 endpoint
        count = registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/api/v2/upload"},
        ])
        assert count == 1

        catalog = registry_db.get_endpoint_catalog("relay")
        assert len(catalog) == 1
        assert catalog[0]["path"] == "/api/v2/upload"

    def test_get_endpoint_catalog(self, registry_db):
        """Query endpoint catalog includes base_url from instance."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/api/v1/upload"},
        ])

        catalog = registry_db.get_endpoint_catalog("relay")
        assert len(catalog) == 1
        assert catalog[0]["base_url"] == "http://a:1"
        assert catalog[0]["method"] == "POST"
        assert catalog[0]["path"] == "/api/v1/upload"

    def test_get_full_catalog(self, registry_db):
        """Full catalog spans all services."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_instance("core-1", "core", "C1", "http://b:1")
        registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/upload"},
        ])
        registry_db.register_endpoints("core-1", [
            {"method": "POST", "path": "/enqueue"},
            {"method": "GET", "path": "/status"},
        ])

        full = registry_db.get_full_catalog()
        assert len(full) == 3

    def test_catalog_excludes_inactive_instances(self, registry_db):
        """Full catalog excludes deactivated instances."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/upload"},
        ])
        registry_db.deactivate_instance("relay-1")

        catalog = registry_db.get_endpoint_catalog("relay")
        assert len(catalog) == 0

    def test_register_endpoints_empty_list(self, registry_db):
        """Registering empty endpoints clears existing ones."""
        registry_db.register_instance("relay-1", "relay", "R1", "http://a:1")
        registry_db.register_endpoints("relay-1", [
            {"method": "POST", "path": "/upload"},
        ])
        count = registry_db.register_endpoints("relay-1", [])
        assert count == 0

        catalog = registry_db.get_endpoint_catalog("relay")
        assert len(catalog) == 0


# ── Credential Tests ─────────────────────────────────────────────────


class TestCredentials:
    """API credential CRUD operations."""

    def test_create_credential(self, registry_db):
        """Create a credential and look it up by key."""
        registry_db.create_credential(
            credential_id="cred-001",
            api_key="rl_test_abc123",
            api_secret_hash="$2b$12$fakehashvalue",
            service_name="relay",
            issued_to="relay-bulk-1",
            permissions=["blob.write", "blob.read"],
        )

        cred = registry_db.get_credential_by_key("rl_test_abc123")
        assert cred is not None
        assert cred["credential_id"] == "cred-001"
        assert cred["service_name"] == "relay"
        assert cred["issued_to"] == "relay-bulk-1"
        assert cred["status"] == "active"
        assert json.loads(cred["permissions"]) == ["blob.write", "blob.read"]

    def test_get_credential_not_found(self, registry_db):
        """Non-existent key returns None."""
        assert registry_db.get_credential_by_key("nonexistent") is None

    def test_get_credentials_for_service(self, registry_db):
        """List credentials for a service (no secret hashes)."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash1", "relay", "relay-bulk-1",
        )
        registry_db.create_credential(
            "cred-002", "rl_test_002", "$hash2", "relay", "relay-nas-1",
        )

        creds = registry_db.get_credentials_for_service("relay")
        assert len(creds) == 2
        # Verify no secret hash in results
        for c in creds:
            assert "api_secret_hash" not in c

    def test_rotate_credential(self, registry_db):
        """Rotating updates key and hash."""
        registry_db.create_credential(
            "cred-001", "rl_test_old", "$oldhash", "relay", "relay-1",
        )

        updated = registry_db.rotate_credential(
            "cred-001", "rl_test_new", "$newhash",
        )
        assert updated == 1

        cred = registry_db.get_credential_by_key("rl_test_new")
        assert cred is not None
        assert cred["api_secret_hash"] == "$newhash"
        assert cred["last_rotated_at"] is not None

        # Old key should not be found
        assert registry_db.get_credential_by_key("rl_test_old") is None

    def test_update_credential_status(self, registry_db):
        """Change credential status."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash", "relay", "relay-1",
        )

        updated = registry_db.update_credential_status("cred-001", "revoked")
        assert updated == 1

        cred = registry_db.get_credential_by_key("rl_test_001")
        assert cred["status"] == "revoked"

    def test_update_credential_last_used(self, registry_db):
        """Stamp last_used_at on authentication."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash", "relay", "relay-1",
        )

        updated = registry_db.update_credential_last_used("rl_test_001")
        assert updated == 1

        cred = registry_db.get_credential_by_key("rl_test_001")
        assert cred["last_used_at"] is not None


# ── Key Rotation Log Tests ───────────────────────────────────────────


class TestKeyRotationLog:
    """Immutable audit trail for key lifecycle events."""

    def test_log_key_rotation(self, registry_db):
        """Log entries are recorded."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash", "relay", "relay-1",
        )

        row_id = registry_db.log_key_rotation(
            credential_id="cred-001",
            action="created",
            performed_by="installer",
            reason="Initial creation",
        )
        assert row_id > 0

    def test_log_rotation_with_old_prefix(self, registry_db):
        """Rotation log records the old key prefix."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash", "relay", "relay-1",
        )

        row_id = registry_db.log_key_rotation(
            credential_id="cred-001",
            action="rotated",
            performed_by="admin",
            old_key_prefix="rl_test_",
            reason="Scheduled rotation",
        )
        assert row_id > 0

    def test_multiple_log_entries(self, registry_db):
        """Multiple log entries for same credential."""
        registry_db.create_credential(
            "cred-001", "rl_test_001", "$hash", "relay", "relay-1",
        )

        registry_db.log_key_rotation("cred-001", "created", "installer")
        registry_db.log_key_rotation("cred-001", "rotated", "admin")
        registry_db.log_key_rotation("cred-001", "revoked", "admin")

        logs = registry_db.execute_query(
            "SELECT * FROM key_rotation_log WHERE credential_id = ? ORDER BY id",
            ("cred-001",),
        )
        assert len(logs) == 3
        assert [l["action"] for l in logs] == ["created", "rotated", "revoked"]


# ── Service Config Tests ─────────────────────────────────────────────


class TestServiceConfig:
    """Key-value config per service."""

    def test_set_and_get_config(self, registry_db):
        """Set a config value and retrieve it."""
        registry_db.set_config("relay", "max_concurrent_uploads", "10")
        val = registry_db.get_config("relay", "max_concurrent_uploads")
        assert val == "10"

    def test_get_config_not_found(self, registry_db):
        """Non-existent config returns None."""
        assert registry_db.get_config("relay", "nonexistent") is None

    def test_set_config_upsert(self, registry_db):
        """Setting an existing key updates the value."""
        registry_db.set_config("relay", "timeout", "30")
        registry_db.set_config("relay", "timeout", "60")

        val = registry_db.get_config("relay", "timeout")
        assert val == "60"

    def test_get_all_config(self, registry_db):
        """Get all config for a service."""
        registry_db.set_config("core", "processing_timeout", "300")
        registry_db.set_config("core", "max_batch_size", "50")

        configs = registry_db.get_all_config("core")
        assert len(configs) == 2
        keys = {c["config_key"] for c in configs}
        assert keys == {"processing_timeout", "max_batch_size"}

    def test_get_all_config_empty(self, registry_db):
        """Empty config returns empty list."""
        assert registry_db.get_all_config("nonexistent") == []


# ── Singleton Tests ──────────────────────────────────────────────────


class TestSingleton:
    """Singleton lifecycle management."""

    def test_reset_then_get_raises_without_path(self):
        """Getting singleton without path after reset raises RuntimeError."""
        reset_registry_database()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_registry_database()

    def test_set_and_get_singleton(self, registry_db):
        """Set singleton and retrieve it."""
        reset_registry_database()
        set_registry_database(registry_db)
        db = get_registry_database()
        assert db is registry_db

    def test_get_creates_singleton_with_path(self, registry_db_path):
        """First call with path creates the singleton."""
        reset_registry_database()
        db = get_registry_database(registry_db_path)
        assert db is not None
        assert isinstance(db, RegistryDatabase)
        reset_registry_database()


# ── Execute Helpers Tests ────────────────────────────────────────────


class TestExecuteHelpers:
    """Low-level execute_query, execute_insert, execute_update."""

    def test_execute_query_returns_dicts(self, registry_db):
        """execute_query returns list of dicts (not sqlite3.Row)."""
        registry_db.register_instance("test-1", "test", "Test", "http://a:1")
        rows = registry_db.execute_query(
            "SELECT * FROM service_instances WHERE service_instance_id = ?",
            ("test-1",),
        )
        assert len(rows) == 1
        assert isinstance(rows[0], dict)

    def test_execute_query_empty(self, registry_db):
        """execute_query returns empty list for no results."""
        rows = registry_db.execute_query(
            "SELECT * FROM service_instances WHERE service_instance_id = ?",
            ("ghost",),
        )
        assert rows == []

    def test_execute_insert_returns_rowid(self, registry_db):
        """execute_insert returns the lastrowid."""
        row_id = registry_db.execute_insert(
            """INSERT INTO service_config
               (service_name, config_key, config_value, created_at, updated_at)
               VALUES (?, ?, ?, '2026-01-01', '2026-01-01')""",
            ("test", "key1", "val1"),
        )
        assert row_id > 0

    def test_execute_update_returns_rowcount(self, registry_db):
        """execute_update returns affected row count."""
        registry_db.register_instance("test-1", "test", "T", "http://a:1")
        count = registry_db.execute_update(
            "UPDATE service_instances SET display_name = ? WHERE service_instance_id = ?",
            ("Updated", "test-1"),
        )
        assert count == 1
