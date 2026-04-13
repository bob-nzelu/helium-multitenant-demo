"""
Tests for Wazuh Security Event Emitter (P2-B)

Tests cover:
    1. WazuhEventEmitter — emit events to JSONL + SQLite
    2. Convenience methods (auth_failure, brute_force, credential, upload)
    3. JSONL file output format
    4. SQLite security_events table immutability
    5. Security API endpoints (GET /api/security/events, stats)
    6. Singleton lifecycle
"""

import json
import os
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def security_db(tmp_path):
    """
    Create a temp blob.db with security_events table.
    Applies the migration SQL that creates the table + immutability triggers.
    """
    db_path = str(tmp_path / "test_blob.db")
    conn = sqlite3.connect(db_path)

    # Apply the migration
    migration_path = (
        Path(__file__).parent.parent.parent
        / "databases" / "migrations" / "blob" / "003_add_security_events.sql"
    )
    with open(migration_path, "r") as f:
        conn.executescript(f.read())

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def log_path(tmp_path):
    """Temp JSONL log file path."""
    return str(tmp_path / "security_events.jsonl")


@pytest.fixture
def emitter(security_db, log_path):
    """WazuhEventEmitter with both DB and JSONL output."""
    from src.observability.wazuh import WazuhEventEmitter
    return WazuhEventEmitter(db_path=security_db, log_path=log_path)


@pytest.fixture
def security_client(test_db, security_db, tmp_path, monkeypatch):
    """
    FastAPI test client with blob.db that has security_events table.
    Uses the security_db (which has the migration applied).
    """
    # We need a blob.db with BOTH blob tables and security_events.
    # Start from test_db (has blob schema + seed), add security_events.
    conn = sqlite3.connect(test_db)
    migration_path = (
        Path(__file__).parent.parent.parent
        / "databases" / "migrations" / "blob" / "003_add_security_events.sql"
    )
    with open(migration_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    blob_root = str(tmp_path / "blobs")
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.config_db import reset_config_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()

    get_blob_database(test_db)

    from src.main import app
    with TestClient(app) as c:
        yield c

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — WazuhEventEmitter
# ══════════════════════════════════════════════════════════════════════════


class TestWazuhEventEmitter:
    """Core emit functionality."""

    def test_emit_writes_to_db(self, emitter, security_db):
        """emit() inserts a row into security_events table."""
        row_id = emitter.emit(
            event_class="authentication",
            event_type="auth_failure",
            message="Invalid API key provided",
            severity="medium",
            actor_service="relay",
            actor_ip="10.0.1.5",
        )

        assert row_id is not None

        conn = sqlite3.connect(security_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM security_events WHERE id = ?", (row_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["event_class"] == "authentication"
        assert row["event_type"] == "auth_failure"
        assert row["severity"] == "medium"
        assert row["actor_service"] == "relay"
        assert row["actor_ip"] == "10.0.1.5"
        assert row["checksum"] is not None

    def test_emit_writes_to_jsonl(self, emitter, log_path):
        """emit() appends a JSON line to the log file."""
        emitter.emit(
            event_class="file_activity",
            event_type="file_uploaded",
            message="Test upload",
        )

        assert os.path.exists(log_path)

        with open(log_path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_class"] == "file_activity"
        assert event["source"] == "heartbeat"
        assert "checksum" in event

    def test_emit_multiple_events_append(self, emitter, log_path):
        """Multiple emits append to the JSONL file."""
        emitter.emit("auth", "fail", "First")
        emitter.emit("auth", "fail", "Second")
        emitter.emit("auth", "fail", "Third")

        with open(log_path, "r") as f:
            lines = f.readlines()

        assert len(lines) == 3

    def test_emit_without_db_path(self, log_path):
        """Emitter with no db_path still writes JSONL."""
        from src.observability.wazuh import WazuhEventEmitter
        emitter = WazuhEventEmitter(db_path=None, log_path=log_path)

        row_id = emitter.emit("auth", "test", "No DB")
        assert row_id is None  # No DB write

        with open(log_path, "r") as f:
            assert len(f.readlines()) == 1  # JSONL still written

    def test_emit_without_log_path(self, security_db):
        """Emitter with no log_path still writes to DB."""
        from src.observability.wazuh import WazuhEventEmitter
        emitter = WazuhEventEmitter(db_path=security_db, log_path=None)

        row_id = emitter.emit("auth", "test", "No JSONL")
        assert row_id is not None


class TestWazuhConvenienceMethods:
    """Convenience emit methods."""

    def test_emit_auth_failure(self, emitter, security_db):
        """emit_auth_failure creates authentication event."""
        row_id = emitter.emit_auth_failure(
            actor_ip="192.168.1.100",
            actor_service="unknown",
            reason="API key not found",
            endpoint="/api/blobs/write",
        )

        conn = sqlite3.connect(security_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM security_events WHERE id = ?", (row_id,)).fetchone()
        conn.close()

        assert row["event_class"] == "authentication"
        assert row["event_type"] == "auth_failure"
        assert row["severity"] == "medium"

    def test_emit_brute_force(self, emitter, security_db):
        """emit_brute_force creates security_finding event."""
        row_id = emitter.emit_brute_force(
            actor_ip="10.0.0.99",
            attempt_count=15,
            window_seconds=300,
        )

        conn = sqlite3.connect(security_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM security_events WHERE id = ?", (row_id,)).fetchone()
        conn.close()

        assert row["event_class"] == "security_finding"
        assert row["event_type"] == "brute_force"
        assert row["severity"] == "high"
        assert "15" in row["message"]

    def test_emit_credential_event(self, emitter, security_db):
        """emit_credential_event creates credential_lifecycle event."""
        row_id = emitter.emit_credential_event(
            event_type="key_rotated",
            credential_id="cred-abc-123",
            performed_by="admin",
        )

        conn = sqlite3.connect(security_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM security_events WHERE id = ?", (row_id,)).fetchone()
        conn.close()

        assert row["event_class"] == "credential_lifecycle"
        assert row["event_type"] == "key_rotated"

    def test_emit_upload_event(self, emitter, security_db):
        """emit_upload_event creates file_activity event."""
        row_id = emitter.emit_upload_event(
            blob_uuid="test-uuid-123",
            file_size=2048576,
            actor_service="relay",
        )

        conn = sqlite3.connect(security_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM security_events WHERE id = ?", (row_id,)).fetchone()
        conn.close()

        assert row["event_class"] == "file_activity"
        assert "2048576" in row["message"]


class TestSecurityEventsImmutability:
    """Security events table immutability (triggers)."""

    def test_update_blocked(self, emitter, security_db):
        """UPDATE on security_events is blocked by trigger."""
        emitter.emit("auth", "test", "Immutable event")

        conn = sqlite3.connect(security_db)
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE security_events SET message = 'tampered' WHERE id = 1"
            )
        conn.close()

    def test_delete_blocked(self, emitter, security_db):
        """DELETE on security_events is blocked by trigger."""
        emitter.emit("auth", "test", "Cannot delete me")

        conn = sqlite3.connect(security_db)
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("DELETE FROM security_events WHERE id = 1")
        conn.close()


class TestWazuhSingleton:
    """Singleton lifecycle tests."""

    def test_get_returns_none_before_init(self):
        """get_wazuh_emitter returns None before initialization."""
        from src.observability.wazuh import get_wazuh_emitter, reset_wazuh_emitter
        reset_wazuh_emitter()

        assert get_wazuh_emitter() is None

        reset_wazuh_emitter()

    def test_init_and_get_singleton(self, security_db, log_path):
        """init_wazuh_emitter creates retrievable singleton."""
        from src.observability.wazuh import (
            init_wazuh_emitter, get_wazuh_emitter, reset_wazuh_emitter,
        )
        reset_wazuh_emitter()

        emitter = init_wazuh_emitter(db_path=security_db, log_path=log_path)
        assert get_wazuh_emitter() is emitter

        reset_wazuh_emitter()


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — Security Events Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestSecurityAPI:
    """HTTP endpoint tests for security events."""

    def test_list_security_events_empty(self, security_client):
        """GET /api/security/events returns empty list when no events."""
        resp = security_client.get("/api/security/events")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_security_event_stats_empty(self, security_client):
        """GET /api/security/events/stats returns zero counts."""
        resp = security_client.get("/api/security/events/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
