"""
Tests for Primary/Satellite Implementation (Q6)

Tests cover:
    1. PrimaryClient — HTTP client methods
    2. Primary API — satellite registration, heartbeat, revoke, list
    3. Satellite API — mode guards, proxy endpoints
    4. Migration — satellite_registrations table
    5. Mode enforcement — Primary endpoints blocked in Satellite mode and vice versa
"""

import sqlite3
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def satellite_registry_db(registry_db_path):
    """
    Registry database with satellite_registrations table.
    Applies the 003 migration on top of the base schema.
    """
    from src.database.registry import RegistryDatabase, reset_registry_database

    reset_registry_database()
    db = RegistryDatabase(registry_db_path)

    # Apply migration
    migration_path = (
        Path(__file__).parent.parent.parent
        / "databases" / "migrations" / "registry" / "003_add_satellite_registrations.sql"
    )
    with db.get_connection() as conn:
        with open(migration_path, "r") as f:
            conn.executescript(f.read())

    yield db

    reset_registry_database()


@pytest.fixture
def primary_client_fixture(test_db, satellite_registry_db, tmp_path, monkeypatch):
    """
    Full app setup for Primary mode with satellite table.
    """
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    blob_root = str(tmp_path / "blobs")
    import os
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.registry import set_registry_database, reset_registry_database
    from src.database.config_db import reset_config_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.clients.primary_client import reset_primary_client

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()

    get_blob_database(test_db)
    set_registry_database(satellite_registry_db)

    yield test_db

    reset_blob_database()
    reset_registry_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()


@pytest.fixture
def primary_test_client(primary_client_fixture):
    """FastAPI test client in Primary mode with satellite support."""
    from src.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def satellite_client_fixture(test_db, tmp_path, monkeypatch):
    """
    Full app setup for Satellite mode (no PrimaryClient initialized).
    PRIMARY_URL deliberately NOT set — tests verify 503 when client missing.
    """
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "satellite")

    blob_root = str(tmp_path / "blobs")
    import os
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.config_db import reset_config_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.clients.primary_client import reset_primary_client

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()

    get_blob_database(test_db)

    yield test_db

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()


@pytest.fixture
def satellite_test_client(satellite_client_fixture):
    """FastAPI test client in Satellite mode."""
    from src.main import app
    with TestClient(app) as c:
        yield c


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — PrimaryClient
# ══════════════════════════════════════════════════════════════════════════


class TestPrimaryClient:
    """PrimaryClient instantiation and singleton."""

    def test_client_creation(self):
        """PrimaryClient initializes with primary_url."""
        from src.clients.primary_client import PrimaryClient
        client = PrimaryClient("http://10.0.1.5:9000")
        assert client.primary_url == "http://10.0.1.5:9000"

    def test_url_trailing_slash_stripped(self):
        """Trailing slash is stripped from primary_url."""
        from src.clients.primary_client import PrimaryClient
        client = PrimaryClient("http://10.0.1.5:9000/")
        assert client.primary_url == "http://10.0.1.5:9000"

    def test_singleton_lifecycle(self):
        """init/get/reset singleton lifecycle."""
        from src.clients.primary_client import (
            init_primary_client, get_primary_client, reset_primary_client,
        )
        reset_primary_client()

        assert get_primary_client() is None

        client = init_primary_client("http://10.0.1.5:9000")
        assert get_primary_client() is client

        reset_primary_client()
        assert get_primary_client() is None


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — Migration
# ══════════════════════════════════════════════════════════════════════════


class TestSatelliteMigration:
    """Migration 003: satellite_registrations table."""

    def test_table_created(self, satellite_registry_db):
        """satellite_registrations table exists after migration."""
        rows = satellite_registry_db.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='satellite_registrations'"
        )
        assert len(rows) == 1

    def test_table_columns(self, satellite_registry_db):
        """Table has expected columns."""
        with satellite_registry_db.get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(satellite_registrations)")
            columns = {row[1] for row in cursor.fetchall()}

        expected = {
            "satellite_id", "display_name", "base_url", "status",
            "last_heartbeat_at", "last_heartbeat_status",
            "region", "version", "registered_at", "updated_at",
        }
        assert expected.issubset(columns)

    def test_status_constraint(self, satellite_registry_db):
        """Status CHECK constraint blocks invalid values."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        with satellite_registry_db.get_connection() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO satellite_registrations
                       (satellite_id, display_name, base_url, status, registered_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("test", "Test", "http://test", "INVALID", now, now),
                )


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — Primary Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestPrimaryAPI:
    """Primary mode — satellite management endpoints."""

    def test_register_satellite(self, primary_test_client):
        """POST /primary/satellites/register creates a registration."""
        resp = primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-lagos-1",
                "display_name": "Lagos Branch",
                "base_url": "http://10.0.2.5:9000",
                "region": "lagos",
                "version": "2.0.0",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "registered"
        assert data["satellite_id"] == "satellite-lagos-1"

    def test_list_satellites_empty(self, primary_test_client):
        """GET /primary/satellites returns empty list initially."""
        resp = primary_test_client.get("/primary/satellites")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_list_satellites_after_register(self, primary_test_client):
        """GET /primary/satellites lists registered satellites."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-abuja-1",
                "display_name": "Abuja Branch",
                "base_url": "http://10.0.3.5:9000",
            },
        )
        resp = primary_test_client.get("/primary/satellites")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["satellites"][0]["satellite_id"] == "satellite-abuja-1"

    def test_get_satellite(self, primary_test_client):
        """GET /primary/satellites/{id} returns specific satellite."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-ph-1",
                "display_name": "Port Harcourt",
                "base_url": "http://10.0.4.5:9000",
                "region": "rivers",
            },
        )
        resp = primary_test_client.get("/primary/satellites/satellite-ph-1")
        assert resp.status_code == 200
        assert resp.json()["region"] == "rivers"

    def test_get_satellite_not_found(self, primary_test_client):
        """GET /primary/satellites/{id} returns 404 for unknown satellite."""
        resp = primary_test_client.get("/primary/satellites/nonexistent")
        assert resp.status_code == 404

    def test_heartbeat(self, primary_test_client):
        """POST /primary/satellites/{id}/heartbeat updates last_heartbeat_at."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-hb-test",
                "display_name": "HB Test",
                "base_url": "http://10.0.5.5:9000",
            },
        )
        resp = primary_test_client.post(
            "/primary/satellites/satellite-hb-test/heartbeat",
            json={"status": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

        # Verify heartbeat updated
        detail = primary_test_client.get("/primary/satellites/satellite-hb-test").json()
        assert detail["last_heartbeat_status"] == "ok"
        assert detail["last_heartbeat_at"] is not None

    def test_heartbeat_not_found(self, primary_test_client):
        """POST /primary/satellites/{id}/heartbeat returns 404 for unknown."""
        resp = primary_test_client.post(
            "/primary/satellites/nonexistent/heartbeat",
            json={"status": "ok"},
        )
        assert resp.status_code == 404

    def test_revoke_satellite(self, primary_test_client):
        """POST /primary/satellites/{id}/revoke sets status to 'revoked'."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-revoke-test",
                "display_name": "Revoke Test",
                "base_url": "http://10.0.6.5:9000",
            },
        )
        resp = primary_test_client.post("/primary/satellites/satellite-revoke-test/revoke")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

        # Verify it's gone from active list
        active = primary_test_client.get("/primary/satellites").json()
        assert active["count"] == 0

        # But visible with active_only=false
        all_sats = primary_test_client.get("/primary/satellites?active_only=false").json()
        assert all_sats["count"] == 1
        assert all_sats["satellites"][0]["status"] == "revoked"

    def test_revoke_not_found(self, primary_test_client):
        """POST /primary/satellites/{id}/revoke returns 404 for unknown."""
        resp = primary_test_client.post("/primary/satellites/nonexistent/revoke")
        assert resp.status_code == 404

    def test_re_register_revoked_satellite(self, primary_test_client):
        """Re-registering a revoked satellite reactivates it."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-reactivate",
                "display_name": "Reactivate Test",
                "base_url": "http://10.0.7.5:9000",
            },
        )
        primary_test_client.post("/primary/satellites/satellite-reactivate/revoke")

        # Re-register
        resp = primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-reactivate",
                "display_name": "Reactivate Test v2",
                "base_url": "http://10.0.7.5:9000",
            },
        )
        assert resp.status_code == 200

        detail = primary_test_client.get("/primary/satellites/satellite-reactivate").json()
        assert detail["status"] == "active"
        assert detail["display_name"] == "Reactivate Test v2"

    def test_heartbeat_does_not_reactivate_revoked(self, primary_test_client):
        """Heartbeat from revoked satellite keeps status as 'revoked'."""
        primary_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "satellite-revoked-hb",
                "display_name": "Revoked HB",
                "base_url": "http://10.0.8.5:9000",
            },
        )
        primary_test_client.post("/primary/satellites/satellite-revoked-hb/revoke")

        primary_test_client.post(
            "/primary/satellites/satellite-revoked-hb/heartbeat",
            json={"status": "ok"},
        )

        detail = primary_test_client.get(
            "/primary/satellites/satellite-revoked-hb"
        ).json()
        assert detail["status"] == "revoked"


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — Satellite Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestSatelliteAPI:
    """Satellite mode — proxy endpoint behavior."""

    def test_satellite_health(self, satellite_test_client):
        """GET /satellite/health returns local health + primary info."""
        resp = satellite_test_client.get("/satellite/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "satellite"
        assert data["local"] == "ok"
        assert "primary" in data

    def test_satellite_blob_write_no_client(self, satellite_test_client):
        """POST /satellite/blobs/write returns 503 when PrimaryClient not initialized."""
        resp = satellite_test_client.post(
            "/satellite/blobs/write",
            files={"file": ("test.pdf", b"data", "application/pdf")},
            data={"source": "satellite", "company_id": "default"},
        )
        assert resp.status_code == 503

    def test_satellite_blob_register_no_client(self, satellite_test_client):
        """POST /satellite/blobs/register returns 503 when PrimaryClient not initialized."""
        resp = satellite_test_client.post(
            "/satellite/blobs/register",
            json={"blob_uuid": "test", "blob_path": "/test"},
        )
        assert resp.status_code == 503

    def test_satellite_config_no_client(self, satellite_test_client):
        """GET /satellite/config/{s}/{k} returns 503 when no PrimaryClient."""
        resp = satellite_test_client.get("/satellite/config/heartbeat/test_key")
        assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════════
# MODE ENFORCEMENT TESTS
# ══════════════════════════════════════════════════════════════════════════


class TestModeEnforcement:
    """Endpoints respect Primary vs Satellite mode."""

    def test_primary_endpoints_blocked_in_satellite_mode(self, satellite_test_client):
        """Primary satellite-management endpoints return 403 in Satellite mode."""
        resp = satellite_test_client.get("/primary/satellites")
        assert resp.status_code == 403

        resp = satellite_test_client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": "test",
                "display_name": "Test",
                "base_url": "http://test",
            },
        )
        assert resp.status_code == 403

    def test_satellite_endpoints_blocked_in_primary_mode(self, primary_test_client):
        """Satellite proxy endpoints return 403 in Primary mode."""
        resp = primary_test_client.get("/satellite/health")
        assert resp.status_code == 403

        resp = primary_test_client.get("/satellite/config/heartbeat/test_key")
        assert resp.status_code == 403
