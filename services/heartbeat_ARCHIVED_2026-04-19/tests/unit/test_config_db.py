"""
Tests for config.db — ConfigDatabase + Config API (Q5)

Tests cover:
    1. Config entry CRUD (get, set, delete, readonly protection)
    2. Tier limits (get by tier, get all, set/upsert)
    3. Feature flags (get, set, enable/disable, delete)
    4. Database catalog (register, list by service, list by tenant, summary)
    5. Config API endpoints (HTTP CRUD for all 4 tables)
    6. Singleton lifecycle (get/set/reset)
"""

import pytest
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — ConfigDatabase class
# ══════════════════════════════════════════════════════════════════════════


class TestConfigEntries:
    """Config entry CRUD operations."""

    def test_set_and_get_config_entry(self, config_db):
        """Set a config entry, then retrieve it."""
        config_db.set_config_entry(
            service_name="relay",
            config_key="max_retries",
            config_value="3",
            value_type="int",
            description="Max retry attempts",
        )

        entry = config_db.get_config_entry("relay", "max_retries")
        assert entry is not None
        assert entry["config_value"] == "3"
        assert entry["value_type"] == "int"
        assert entry["description"] == "Max retry attempts"

    def test_get_config_value_convenience(self, config_db):
        """get_config_value returns just the value string."""
        config_db.set_config_entry("core", "batch_size", "50")

        assert config_db.get_config_value("core", "batch_size") == "50"
        assert config_db.get_config_value("core", "nonexistent") is None

    def test_upsert_config_entry(self, config_db):
        """Setting the same key again updates it (upsert)."""
        config_db.set_config_entry("relay", "timeout", "30")
        config_db.set_config_entry("relay", "timeout", "60")

        assert config_db.get_config_value("relay", "timeout") == "60"

    def test_get_all_config_filtered(self, config_db):
        """Filter config entries by service_name."""
        config_db.set_config_entry("relay", "key1", "v1")
        config_db.set_config_entry("relay", "key2", "v2")
        config_db.set_config_entry("core", "key3", "v3")

        relay_entries = config_db.get_all_config("relay")
        assert len(relay_entries) == 2

        all_entries = config_db.get_all_config()
        assert len(all_entries) == 3

    def test_delete_config_entry(self, config_db):
        """Delete a config entry."""
        config_db.set_config_entry("relay", "temp_key", "temp_val")
        assert config_db.get_config_entry("relay", "temp_key") is not None

        count = config_db.delete_config_entry("relay", "temp_key")
        assert count == 1
        assert config_db.get_config_entry("relay", "temp_key") is None

    def test_delete_nonexistent_returns_zero(self, config_db):
        """Deleting a nonexistent entry returns 0."""
        count = config_db.delete_config_entry("relay", "no_such_key")
        assert count == 0

    def test_readonly_config_entry_blocks_update(self, config_db):
        """Read-only entries cannot be updated via set_config_entry."""
        # Insert a readonly entry directly
        with config_db.get_connection() as conn:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO config_entries
                   (service_name, config_key, config_value, value_type,
                    is_readonly, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                ("_shared", "locked_key", "immutable", "string", now, now),
            )
            conn.commit()

        with pytest.raises(ValueError, match="read-only"):
            config_db.set_config_entry("_shared", "locked_key", "new_value")

    def test_readonly_config_entry_blocks_delete(self, config_db):
        """Read-only entries cannot be deleted."""
        with config_db.get_connection() as conn:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO config_entries
                   (service_name, config_key, config_value, value_type,
                    is_readonly, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                ("_shared", "locked2", "immutable", "string", now, now),
            )
            conn.commit()

        with pytest.raises(ValueError, match="read-only"):
            config_db.delete_config_entry("_shared", "locked2")


class TestTierLimits:
    """Tier limit operations."""

    def test_set_and_get_tier_limit(self, config_db):
        """Set a tier limit and retrieve it."""
        config_db.set_tier_limit("test", "daily_upload_limit", "100", "int")

        limit = config_db.get_tier_limit("test", "daily_upload_limit")
        assert limit is not None
        assert limit["limit_value"] == "100"

    def test_get_tier_limit_value_convenience(self, config_db):
        """get_tier_limit_value returns just the value string."""
        config_db.set_tier_limit("pro", "max_file_size_mb", "100")

        assert config_db.get_tier_limit_value("pro", "max_file_size_mb") == "100"
        assert config_db.get_tier_limit_value("pro", "nonexistent") is None

    def test_get_all_limits_for_tier(self, config_db):
        """Get all limits for a single tier."""
        config_db.set_tier_limit("standard", "daily_upload_limit", "1000")
        config_db.set_tier_limit("standard", "max_file_size_mb", "50")
        config_db.set_tier_limit("pro", "daily_upload_limit", "5000")

        standard = config_db.get_all_limits_for_tier("standard")
        assert len(standard) == 2

    def test_get_all_tier_limits(self, config_db):
        """Get limits across all tiers."""
        config_db.set_tier_limit("test", "k1", "v1")
        config_db.set_tier_limit("standard", "k1", "v2")
        config_db.set_tier_limit("pro", "k1", "v3")

        all_limits = config_db.get_all_tier_limits()
        assert len(all_limits) == 3

    def test_upsert_tier_limit(self, config_db):
        """Setting the same tier+key updates the value."""
        config_db.set_tier_limit("test", "daily_upload_limit", "100")
        config_db.set_tier_limit("test", "daily_upload_limit", "200")

        assert config_db.get_tier_limit_value("test", "daily_upload_limit") == "200"


class TestFeatureFlags:
    """Feature flag operations."""

    def test_set_and_get_feature_flag(self, config_db):
        """Create a feature flag and retrieve it."""
        config_db.set_feature_flag("sse_events", True, "global", "SSE streaming")

        flag = config_db.get_feature_flag("sse_events")
        assert flag is not None
        assert flag["is_enabled"] == 1
        assert flag["scope"] == "global"

    def test_is_feature_enabled(self, config_db):
        """Check boolean feature flag status."""
        config_db.set_feature_flag("wazuh_logging", False)

        assert config_db.is_feature_enabled("wazuh_logging") is False
        assert config_db.is_feature_enabled("nonexistent", default=True) is True
        assert config_db.is_feature_enabled("nonexistent", default=False) is False

    def test_toggle_feature_flag(self, config_db):
        """Toggle a flag from off to on."""
        config_db.set_feature_flag("reconciliation", False)
        assert config_db.is_feature_enabled("reconciliation") is False

        config_db.set_feature_flag("reconciliation", True)
        assert config_db.is_feature_enabled("reconciliation") is True

    def test_get_all_feature_flags(self, config_db):
        """List all flags."""
        config_db.set_feature_flag("flag_a", True, "global")
        config_db.set_feature_flag("flag_b", False, "heartbeat")

        all_flags = config_db.get_all_feature_flags()
        assert len(all_flags) == 2

    def test_get_feature_flags_by_scope(self, config_db):
        """Filter flags by scope."""
        config_db.set_feature_flag("flag_global", True, "global")
        config_db.set_feature_flag("flag_hb", False, "heartbeat")

        global_flags = config_db.get_all_feature_flags("global")
        assert len(global_flags) == 1
        assert global_flags[0]["flag_name"] == "flag_global"

    def test_delete_feature_flag(self, config_db):
        """Delete a feature flag."""
        config_db.set_feature_flag("temp_flag", True)
        assert config_db.get_feature_flag("temp_flag") is not None

        count = config_db.delete_feature_flag("temp_flag")
        assert count == 1
        assert config_db.get_feature_flag("temp_flag") is None

    def test_delete_nonexistent_flag_returns_zero(self, config_db):
        """Deleting nonexistent flag returns 0."""
        assert config_db.delete_feature_flag("no_such_flag") == 0


class TestDatabaseCatalog:
    """Database catalog operations."""

    def test_register_and_get_database(self, config_db):
        """Register a database and retrieve it."""
        config_db.register_database(
            db_logical_name="sync",
            db_category="operational",
            tenant_id="pikwik-001",
            owner_service="float-sdk",
            db_physical_name="sync_pikwik_abc.db",
            db_path="/data/sync_pikwik_abc.db",
            schema_version="3",
            description="Float SDK sync database",
        )

        entry = config_db.get_database_entry("sync", "pikwik-001")
        assert entry is not None
        assert entry["owner_service"] == "float-sdk"
        assert entry["db_engine"] == "sqlite"
        assert entry["status"] == "active"

    def test_register_upsert(self, config_db):
        """Re-registering updates the entry."""
        config_db.register_database(
            db_logical_name="blob",
            db_category="operational",
            tenant_id="global",
            owner_service="heartbeat",
            db_physical_name="blob.db",
            db_path="/db/blob.db",
            schema_version="1",
        )
        config_db.register_database(
            db_logical_name="blob",
            db_category="operational",
            tenant_id="global",
            owner_service="heartbeat",
            db_physical_name="blob.db",
            db_path="/db/blob.db",
            schema_version="2",
        )

        entry = config_db.get_database_entry("blob", "global")
        assert entry["schema_version"] == "2"

    def test_get_databases_by_service(self, config_db):
        """List databases owned by a service."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db")
        config_db.register_database("registry", "operational", "global", "heartbeat", "registry.db", "/db/registry.db")
        config_db.register_database("sync", "operational", "pikwik-001", "float-sdk", "sync.db", "/db/sync.db")

        hb_dbs = config_db.get_databases_by_service("heartbeat")
        assert len(hb_dbs) == 2

    def test_get_databases_by_tenant(self, config_db):
        """List databases for a tenant."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db")
        config_db.register_database("sync", "operational", "pikwik-001", "float-sdk", "sync.db", "/db/sync.db")
        config_db.register_database("his", "reference", "pikwik-001", "his", "his.db", "/db/his.db")

        tenant_dbs = config_db.get_databases_by_tenant("pikwik-001")
        assert len(tenant_dbs) == 2

    def test_get_full_catalog(self, config_db):
        """Get the complete catalog."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db")
        config_db.register_database("sync", "operational", "pikwik-001", "float-sdk", "sync.db", "/db/sync.db")

        catalog = config_db.get_full_catalog()
        assert len(catalog) == 2

    def test_get_full_catalog_filtered_by_status(self, config_db):
        """Filter catalog by status."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db", status="active")
        config_db.register_database("old", "operational", "global", "heartbeat", "old.db", "/db/old.db", status="archived")

        active = config_db.get_full_catalog("active")
        assert len(active) == 1
        assert active[0]["db_logical_name"] == "blob"

    def test_update_database_status(self, config_db):
        """Update status of a catalog entry."""
        config_db.register_database("sync", "operational", "pikwik-001", "float-sdk", "sync.db", "/db/sync.db")

        count = config_db.update_database_status("sync", "pikwik-001", "migrating")
        assert count == 1

        entry = config_db.get_database_entry("sync", "pikwik-001")
        assert entry["status"] == "migrating"

    def test_update_database_size(self, config_db):
        """Update size of a catalog entry."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db")

        config_db.update_database_size("blob", "global", 1024000)

        entry = config_db.get_database_entry("blob", "global")
        assert entry["size_bytes"] == 1024000

    def test_catalog_summary(self, config_db):
        """Get summary statistics."""
        config_db.register_database("blob", "operational", "global", "heartbeat", "blob.db", "/db/blob.db", size_bytes=500000)
        config_db.register_database("registry", "operational", "global", "heartbeat", "registry.db", "/db/registry.db", size_bytes=200000)
        config_db.register_database("old", "operational", "global", "heartbeat", "old.db", "/db/old.db", status="archived", size_bytes=100000)

        summary = config_db.get_catalog_summary()
        assert summary["total_databases"] == 3
        assert summary["by_status"]["active"] == 2
        assert summary["by_status"]["archived"] == 1
        assert summary["by_service"]["heartbeat"] == 3
        assert summary["total_size_bytes"] == 800000


class TestConfigDatabaseSingleton:
    """Singleton lifecycle tests."""

    def test_get_without_init_raises(self):
        """Getting singleton without path raises RuntimeError."""
        from src.database.config_db import get_config_database, reset_config_database
        reset_config_database()

        with pytest.raises(RuntimeError, match="not initialized"):
            get_config_database()

        reset_config_database()

    def test_set_and_reset_singleton(self, config_db):
        """set/reset singleton lifecycle."""
        from src.database.config_db import (
            get_config_database, set_config_database, reset_config_database,
        )
        reset_config_database()

        set_config_database(config_db)
        assert get_config_database() is config_db

        reset_config_database()

        with pytest.raises(RuntimeError):
            get_config_database()

        reset_config_database()


class TestSeededConfigDatabase:
    """Tests against seeded config.db (verifies seed data loaded)."""

    def test_seed_shared_config_loaded(self, seeded_config_db):
        """Seed data should include _shared config entries."""
        entry = seeded_config_db.get_config_entry("_shared", "tenant_id")
        assert entry is not None
        assert entry["config_value"] == "dev-tenant-001"

    def test_seed_tier_limits_loaded(self, seeded_config_db):
        """Seed data should include tier limits for all 4 tiers."""
        test_limits = seeded_config_db.get_all_limits_for_tier("test")
        assert len(test_limits) >= 5  # at least 5 limits per tier

        enterprise_limits = seeded_config_db.get_all_limits_for_tier("enterprise")
        assert len(enterprise_limits) >= 5

    def test_seed_feature_flags_loaded(self, seeded_config_db):
        """Seed data should include feature flags."""
        flags = seeded_config_db.get_all_feature_flags()
        assert len(flags) >= 4

        # audit_checksums should be enabled
        assert seeded_config_db.is_feature_enabled("audit_checksums") is True
        # sse_events should be disabled
        assert seeded_config_db.is_feature_enabled("sse_events") is False

    def test_seed_database_catalog_loaded(self, seeded_config_db):
        """Seed data should register HeartBeat's own 3 databases."""
        hb_dbs = seeded_config_db.get_databases_by_service("heartbeat")
        assert len(hb_dbs) == 3

        names = {db["db_logical_name"] for db in hb_dbs}
        assert names == {"blob", "registry", "config"}


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — Config HTTP Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestConfigAPI:
    """HTTP endpoint tests for config entries."""

    def test_list_config_entries(self, config_client):
        """GET /api/config returns entries."""
        resp = config_client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert data["count"] >= 0

    def test_get_config_entry(self, config_client):
        """GET /api/config/{service}/{key} returns seeded entry."""
        resp = config_client.get("/api/config/_shared/tenant_id")
        assert resp.status_code == 200
        assert resp.json()["config_value"] == "dev-tenant-001"

    def test_get_config_entry_not_found(self, config_client):
        """GET /api/config/{service}/{key} returns 404 for missing key."""
        resp = config_client.get("/api/config/relay/nonexistent")
        assert resp.status_code == 404

    def test_set_config_entry(self, config_client):
        """PUT /api/config/{service}/{key} creates/updates entry."""
        resp = config_client.put(
            "/api/config/relay/batch_size",
            json={"config_value": "25", "value_type": "int"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify it was set
        resp2 = config_client.get("/api/config/relay/batch_size")
        assert resp2.status_code == 200
        assert resp2.json()["config_value"] == "25"

    def test_delete_config_entry(self, config_client):
        """DELETE /api/config/{service}/{key} removes entry."""
        # Create one first
        config_client.put(
            "/api/config/test_svc/temp_key",
            json={"config_value": "temp"},
        )

        resp = config_client.delete("/api/config/test_svc/temp_key")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_config_entry_not_found(self, config_client):
        """DELETE /api/config/{service}/{key} returns 404 for missing key."""
        resp = config_client.delete("/api/config/relay/nonexistent")
        assert resp.status_code == 404


class TestTierLimitsAPI:
    """HTTP endpoint tests for tier limits."""

    def test_list_all_tier_limits(self, config_client):
        """GET /api/tiers lists all tier limits."""
        resp = config_client.get("/api/tiers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 20  # 4 tiers x 6 limits each in seed

    def test_get_tier_limits(self, config_client):
        """GET /api/tiers/{tier} returns limits for a specific tier."""
        resp = config_client.get("/api/tiers/enterprise")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "enterprise"
        assert data["count"] >= 5

    def test_get_tier_limits_not_found(self, config_client):
        """GET /api/tiers/{tier} returns 404 for unknown tier."""
        resp = config_client.get("/api/tiers/mythical")
        assert resp.status_code == 404


class TestFeatureFlagsAPI:
    """HTTP endpoint tests for feature flags."""

    def test_list_feature_flags(self, config_client):
        """GET /api/flags lists all flags."""
        resp = config_client.get("/api/flags")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 4

    def test_get_feature_flag(self, config_client):
        """GET /api/flags/{name} returns a specific flag."""
        resp = config_client.get("/api/flags/audit_checksums")
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] == 1

    def test_get_feature_flag_not_found(self, config_client):
        """GET /api/flags/{name} returns 404 for unknown flag."""
        resp = config_client.get("/api/flags/nonexistent_flag")
        assert resp.status_code == 404

    def test_set_feature_flag(self, config_client):
        """PUT /api/flags/{name} creates/updates a flag."""
        resp = config_client.put(
            "/api/flags/new_feature",
            json={"is_enabled": True, "scope": "global", "description": "Test flag"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is True

    def test_delete_feature_flag(self, config_client):
        """DELETE /api/flags/{name} removes a flag."""
        # Create one
        config_client.put(
            "/api/flags/temp_flag",
            json={"is_enabled": False},
        )
        resp = config_client.delete("/api/flags/temp_flag")
        assert resp.status_code == 200

    def test_delete_feature_flag_not_found(self, config_client):
        """DELETE /api/flags/{name} returns 404 for unknown flag."""
        resp = config_client.delete("/api/flags/nonexistent_flag")
        assert resp.status_code == 404


class TestDatabaseCatalogAPI:
    """HTTP endpoint tests for database catalog."""

    def test_list_databases(self, config_client):
        """GET /api/databases lists the full catalog."""
        resp = config_client.get("/api/databases")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 3  # seed registers 3 HeartBeat databases

    def test_list_databases_for_service(self, config_client):
        """GET /api/databases/{service} lists databases for that service."""
        resp = config_client.get("/api/databases/heartbeat")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 3

    def test_database_catalog_summary(self, config_client):
        """GET /api/databases/summary returns stats."""
        resp = config_client.get("/api/databases/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_databases" in data
        assert "by_status" in data
        assert "by_service" in data

    def test_register_database(self, config_client):
        """POST /api/databases/register adds a new database."""
        resp = config_client.post(
            "/api/databases/register",
            json={
                "db_logical_name": "invoices",
                "db_category": "operational",
                "tenant_id": "pikwik-001",
                "owner_service": "core",
                "db_physical_name": "invoices_pikwik.db",
                "db_path": "/data/invoices_pikwik.db",
                "schema_version": "1",
                "description": "Core invoice storage",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"

        # Verify it appears in catalog
        resp2 = config_client.get("/api/databases/core")
        assert resp2.status_code == 200
        assert resp2.json()["count"] >= 1
