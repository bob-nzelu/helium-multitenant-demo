"""
Tests for Audit Immutability (Q4 — Demo Question)

Tests:
    1. Immutability trigger blocks UPDATE on audit_events
    2. Immutability trigger blocks DELETE on audit_events
    3. Immutability trigger blocks UPDATE on blob_cleanup_history
    4. Immutability trigger blocks DELETE on blob_cleanup_history
    5. Checksum chain — first event uses genesis hash
    6. Checksum chain — second event chains from first
    7. Checksum chain — verify_chain on intact chain
    8. Checksum chain — verify_chain detects tampered row
    9. Verify endpoint — returns correct response
    10. Chain status endpoint — genesis state
"""

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure HeartBeat root is on sys.path
heartbeat_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(heartbeat_root))

from src.database.audit_guard import (
    GENESIS_HASH,
    compute_audit_checksum,
    get_last_checksum,
    insert_audited_event,
    verify_chain,
)
from src.database.migrator import DatabaseMigrator


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def immutable_db(tmp_path):
    """
    Create a blob.db with schema + migration 002 (immutability triggers).

    Returns the db_path.
    """
    db_path = str(tmp_path / "blob.db")
    schema_path = Path(__file__).parent.parent.parent / "databases" / "schema.sql"

    conn = sqlite3.connect(db_path)
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    # Apply migration 002 to add triggers + checksum_chain column
    migrations_dir = str(
        Path(__file__).parent.parent.parent / "databases" / "migrations" / "blob"
    )
    migrator = DatabaseMigrator(
        db_path=db_path,
        migrations_dir=migrations_dir,
        db_name="blob",
    )
    results = migrator.apply_pending()

    # Verify migrations applied
    applied = [r for r in results if r.status == "applied"]
    assert len(applied) >= 1, f"Expected migrations to apply, got: {results}"

    return db_path


@pytest.fixture
def registry_immutable_db(tmp_path):
    """
    Create a registry.db with schema + migration 002 (immutability triggers).

    Returns the db_path.
    """
    db_path = str(tmp_path / "databases" / "registry.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    schema_path = Path(__file__).parent.parent.parent / "databases" / "registry_schema.sql"

    # Copy schema to expected location so RegistryDatabase finds it
    import shutil
    schema_dst = tmp_path / "databases" / "registry_schema.sql"
    shutil.copy(str(schema_path), str(schema_dst))

    conn = sqlite3.connect(db_path)
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    # Apply registry migrations
    migrations_dir = str(
        Path(__file__).parent.parent.parent / "databases" / "migrations" / "registry"
    )
    migrator = DatabaseMigrator(
        db_path=db_path,
        migrations_dir=migrations_dir,
        db_name="registry",
    )
    migrator.apply_pending()

    return db_path


# ── Test Immutability Triggers ────────────────────────────────────────────

class TestImmutabilityTriggers:
    """Tests that SQLite triggers prevent UPDATE/DELETE on audit tables."""

    def test_audit_events_blocks_update(self, immutable_db):
        """UPDATE on audit_events is blocked by trigger."""
        conn = sqlite3.connect(immutable_db)

        # Insert an audit event (directly, bypassing checksum for this test)
        conn.execute(
            """INSERT INTO audit_events (service, event_type, created_at, created_at_unix)
               VALUES ('test', 'test.event', '2026-01-01T00:00:00Z', 1735689600)"""
        )
        conn.commit()

        # Try to UPDATE — should be blocked
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE audit_events SET service = 'hacked' WHERE id = 1"
            )
        conn.close()

    def test_audit_events_blocks_delete(self, immutable_db):
        """DELETE on audit_events is blocked by trigger."""
        conn = sqlite3.connect(immutable_db)

        conn.execute(
            """INSERT INTO audit_events (service, event_type, created_at, created_at_unix)
               VALUES ('test', 'test.event', '2026-01-01T00:00:00Z', 1735689600)"""
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("DELETE FROM audit_events WHERE id = 1")
        conn.close()

    @pytest.mark.skip(reason="blob_cleanup_history dropped in canonical schema v1.4.0 — table replaced by blob_downloads")
    def test_blob_cleanup_history_blocks_update(self, immutable_db):
        """UPDATE on blob_cleanup_history is blocked by trigger."""
        pass

    @pytest.mark.skip(reason="blob_cleanup_history dropped in canonical schema v1.4.0 — table replaced by blob_downloads")
    def test_blob_cleanup_history_blocks_delete(self, immutable_db):
        """DELETE on blob_cleanup_history is blocked by trigger."""
        pass
        conn.close()

    def test_key_rotation_log_blocks_update(self, registry_immutable_db):
        """UPDATE on key_rotation_log is blocked by trigger."""
        conn = sqlite3.connect(registry_immutable_db)

        # Need a credential first (FK constraint)
        conn.execute(
            """INSERT INTO api_credentials
               (credential_id, api_key, api_secret_hash, service_name, issued_to, status, created_at, updated_at)
               VALUES ('cred-1', 'rl_test_abc', '$2b$12$hash', 'relay', 'relay-1', 'active', '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO key_rotation_log
               (credential_id, action, performed_by, created_at)
               VALUES ('cred-1', 'created', 'installer', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE key_rotation_log SET action = 'hacked' WHERE id = 1"
            )
        conn.close()

    def test_key_rotation_log_blocks_delete(self, registry_immutable_db):
        """DELETE on key_rotation_log is blocked by trigger."""
        conn = sqlite3.connect(registry_immutable_db)

        conn.execute(
            """INSERT INTO api_credentials
               (credential_id, api_key, api_secret_hash, service_name, issued_to, status, created_at, updated_at)
               VALUES ('cred-1', 'rl_test_abc', '$2b$12$hash', 'relay', 'relay-1', 'active', '2026-01-01', '2026-01-01')"""
        )
        conn.execute(
            """INSERT INTO key_rotation_log
               (credential_id, action, performed_by, created_at)
               VALUES ('cred-1', 'created', 'installer', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("DELETE FROM key_rotation_log WHERE id = 1")
        conn.close()


# ── Test Checksum Chain ───────────────────────────────────────────────────

class TestChecksumChain:
    """Tests for the SHA-256 checksum chain on audit events."""

    def test_first_event_uses_genesis_hash(self, immutable_db):
        """First audited event chains from GENESIS_HASH."""
        result = insert_audited_event(
            db_path=immutable_db,
            service="heartbeat",
            event_type="system.startup",
            details={"mode": "primary"},
        )

        # Verify the checksum was computed using genesis
        expected = compute_audit_checksum(
            event_id=result["event_id"],
            service="heartbeat",
            event_type="system.startup",
            details=json.dumps({"mode": "primary"}),
            created_at=result["created_at"],
            prev_checksum=GENESIS_HASH,
        )

        assert result["checksum_chain"] == expected

    def test_second_event_chains_from_first(self, immutable_db):
        """Second event's checksum uses first event's checksum as input."""
        event1 = insert_audited_event(
            db_path=immutable_db,
            service="relay",
            event_type="file.ingested",
        )

        event2 = insert_audited_event(
            db_path=immutable_db,
            service="core",
            event_type="file.processed",
        )

        # event2's checksum should chain from event1's checksum
        expected = compute_audit_checksum(
            event_id=event2["event_id"],
            service="core",
            event_type="file.processed",
            details=None,
            created_at=event2["created_at"],
            prev_checksum=event1["checksum_chain"],
        )

        assert event2["checksum_chain"] == expected

    def test_verify_intact_chain(self, immutable_db):
        """verify_chain returns verified=True for an intact chain."""
        # Insert 3 chained events
        insert_audited_event(immutable_db, "relay", "file.ingested")
        insert_audited_event(immutable_db, "core", "file.processed")
        insert_audited_event(immutable_db, "heartbeat", "system.metric")

        result = verify_chain(immutable_db)

        assert result["verified"] is True
        assert result["chain_length"] == 3
        assert result["tampered_rows"] == []

    def test_verify_detects_tampered_row(self, immutable_db):
        """verify_chain detects a row whose data was modified outside triggers."""
        # Insert events
        insert_audited_event(immutable_db, "relay", "file.ingested")
        event2 = insert_audited_event(immutable_db, "core", "file.processed")
        insert_audited_event(immutable_db, "heartbeat", "system.metric")

        # Tamper with the middle row by directly modifying the DB
        # We have to drop the trigger first to simulate a bypass (e.g. raw SQLite access)
        conn = sqlite3.connect(immutable_db)
        conn.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        conn.execute(
            "UPDATE audit_events SET service = 'tampered' WHERE id = ?",
            (event2["event_id"],),
        )
        conn.commit()
        # Re-create trigger (optional — verification doesn't need it)
        conn.close()

        result = verify_chain(immutable_db)

        assert result["verified"] is False
        assert event2["event_id"] in result["tampered_rows"]

    def test_get_last_checksum_empty(self, immutable_db):
        """get_last_checksum returns GENESIS_HASH when no events exist."""
        assert get_last_checksum(immutable_db) == GENESIS_HASH

    def test_get_last_checksum_after_insert(self, immutable_db):
        """get_last_checksum returns last event's checksum."""
        event = insert_audited_event(
            immutable_db, "heartbeat", "test.event"
        )

        assert get_last_checksum(immutable_db) == event["checksum_chain"]


# ── Test API Endpoints ────────────────────────────────────────────────────

class TestAuditVerifyAPI:
    """Tests for the audit verification API endpoints."""

    @pytest.fixture
    def audit_client(self, tmp_path, monkeypatch):
        """Create a test client with immutable blob.db."""
        from src.database.connection import reset_blob_database, get_blob_database
        from src.database.registry import reset_registry_database
        from src.config import reset_config
        from src.clients.filesystem_client import reset_filesystem_client
        from fastapi.testclient import TestClient

        # Create blob.db with schema
        db_path = str(tmp_path / "blob.db")
        schema_path = Path(__file__).parent.parent.parent / "databases" / "schema.sql"

        conn = sqlite3.connect(db_path)
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()

        # Apply blob migrations (adds checksum_chain + triggers)
        migrations_dir = str(
            Path(__file__).parent.parent.parent / "databases" / "migrations" / "blob"
        )
        migrator = DatabaseMigrator(
            db_path=db_path,
            migrations_dir=migrations_dir,
            db_name="blob",
        )
        migrator.apply_pending()

        # Set up environment
        monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", db_path)
        monkeypatch.setenv("HEARTBEAT_MODE", "primary")
        monkeypatch.setenv("HEARTBEAT_AUTO_MIGRATE", "false")  # Already migrated

        blob_root = str(tmp_path / "blobs")
        os.makedirs(blob_root, exist_ok=True)
        monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

        reset_blob_database()
        reset_config()
        reset_filesystem_client()
        reset_registry_database()

        get_blob_database(db_path)

        from src.main import app
        with TestClient(app) as c:
            yield c, db_path

        reset_blob_database()
        reset_config()
        reset_filesystem_client()
        reset_registry_database()

    def test_verify_endpoint_empty_chain(self, audit_client):
        """GET /api/audit/verify returns verified=True for empty chain."""
        client, _ = audit_client

        resp = client.get("/api/audit/verify")
        assert resp.status_code == 200

        data = resp.json()
        assert data["verified"] is True
        assert data["chain_length"] == 0

    def test_verify_endpoint_with_events(self, audit_client):
        """GET /api/audit/verify works with chained events."""
        client, db_path = audit_client

        # Insert chained events
        insert_audited_event(db_path, "relay", "file.ingested")
        insert_audited_event(db_path, "core", "file.processed")

        resp = client.get("/api/audit/verify")
        assert resp.status_code == 200

        data = resp.json()
        assert data["verified"] is True
        assert data["chain_length"] == 2

    def test_chain_status_endpoint_genesis(self, audit_client):
        """GET /api/audit/chain/status returns genesis state."""
        client, _ = audit_client

        resp = client.get("/api/audit/chain/status")
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "genesis"
        assert data["has_chained_events"] is False
