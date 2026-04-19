"""
Tests for Reconciliation Engine (P2-E)

Tests cover:
    1. ReconciliationEngine — 5-phase consistency checks
    2. Report generation and serialization
    3. Finding persistence to notifications table
    4. API endpoints — trigger and history
"""

import os
import time
import sqlite3
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def clean_db_path(tmp_path):
    """Create a fresh blob database with schema only (NO seed data)."""
    db_path = str(tmp_path / "recon_blob.db")

    schema_path = Path(__file__).parent.parent.parent / "databases" / "schema.sql"
    conn = sqlite3.connect(db_path)
    if schema_path.exists():
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
    conn.commit()
    conn.close()

    # Insert the relay-test source that our helper uses
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO relay_services (instance_id, relay_type, is_active, created_at) "
        "VALUES ('relay-test', 'bulk', 1, datetime('now'))"
    )
    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def recon_db(clean_db_path, monkeypatch):
    """Blob database singleton ready for reconciliation tests (no seed data)."""
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", clean_db_path)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    from src.database.connection import reset_blob_database, get_blob_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.database.config_db import reset_config_database
    from src.clients.primary_client import reset_primary_client

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()

    db = get_blob_database(clean_db_path)

    yield db

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()


@pytest.fixture
def blob_storage(tmp_path):
    """Temporary filesystem blob storage root."""
    root = str(tmp_path / "blob_storage")
    os.makedirs(os.path.join(root, "files_blob"), exist_ok=True)
    return root


@pytest.fixture
def engine(recon_db, blob_storage, monkeypatch):
    """ReconciliationEngine with clean test DB and filesystem."""
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_storage)

    from src.handlers.reconciliation_handler import ReconciliationEngine
    return ReconciliationEngine(db=recon_db, filesystem_root=blob_storage)


@pytest.fixture
def recon_client(clean_db_path, blob_storage, monkeypatch):
    """FastAPI test client configured for reconciliation tests (no seed data)."""
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", clean_db_path)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_storage)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.config import reset_config
    from src.clients.filesystem_client import FilesystemBlobClient, set_filesystem_client, reset_filesystem_client
    from src.database.config_db import reset_config_database
    from src.clients.primary_client import reset_primary_client

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()

    get_blob_database(clean_db_path)

    fs_client = FilesystemBlobClient(blob_storage)
    set_filesystem_client(fs_client)

    from src.main import app
    with TestClient(app) as c:
        yield c

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()


def _insert_blob(db, blob_uuid, blob_path, status="uploaded", uploaded_at_unix=None):
    """Helper: insert a file_entries row with all NOT NULL columns (canonical v1.4.0)."""
    if uploaded_at_unix is None:
        uploaded_at_unix = int(time.time())
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    retention_unix = uploaded_at_unix + (7 * 365 * 86400)  # 7 years
    file_display_id = f"HB-{blob_uuid}"
    batch_display_id = f"HBB-{blob_uuid}"

    # Ensure batch exists first
    db.execute_insert(
        """INSERT OR IGNORE INTO blob_batches
           (batch_display_id, batch_uuid, source, file_count, status,
            pending_sync, upload_status, uploaded_at_unix, uploaded_at_iso,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            batch_display_id, blob_uuid, "relay-test", 1, status,
            0, status, uploaded_at_unix, now,
            now, now,
        ),
    )

    db.execute_insert(
        """INSERT INTO file_entries
           (file_display_id, blob_uuid, blob_path, original_filename,
            batch_display_id, source, file_size_bytes,
            file_hash, content_type, status, pending_sync,
            uploaded_at_unix, uploaded_at_iso,
            retention_until_unix, retention_until_iso,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            file_display_id, blob_uuid, blob_path, "test.pdf",
            batch_display_id, "relay-test", 1024,
            "a" * 64, "application/pdf", status, 0,
            uploaded_at_unix, now,
            retention_unix, now,
            now, now,
        ),
    )


def _create_file(blob_storage, filename):
    """Helper: create a file in files_blob/."""
    filepath = os.path.join(blob_storage, "files_blob", filename)
    with open(filepath, "wb") as f:
        f.write(b"test data")


# ══════════════════════════════════════════════════════════════════════════
# UNIT TESTS — ReconciliationEngine
# ══════════════════════════════════════════════════════════════════════════


class TestReconciliationReport:
    """Report model tests."""

    def test_empty_report(self, engine):
        """Engine runs with empty DB and filesystem."""
        report = engine.run()
        assert report.total_findings == 0
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.completed_at is not None
        assert report.duration_seconds >= 0

    def test_report_to_dict(self, engine):
        """Report serializes to dict."""
        report = engine.run()
        d = report.to_dict()
        assert "run_id" in d
        assert "findings" in d
        assert "phase_summaries" in d
        assert isinstance(d["findings"], list)


class TestPhaseOrphanDetection:
    """Phase 1: Files on disk with no DB entry."""

    def test_no_orphans(self, engine, recon_db, blob_storage):
        """No findings when all files have DB entries."""
        _insert_blob(recon_db, "uuid-1", "/files_blob/uuid-1-test.pdf")
        _create_file(blob_storage, "uuid-1-test.pdf")

        report = engine.run()
        orphan_findings = [f for f in report.findings if f.phase == "orphan_detection"]
        assert len(orphan_findings) == 0

    def test_orphan_detected(self, engine, blob_storage):
        """Orphaned file (no DB entry) is detected."""
        _create_file(blob_storage, "orphan-uuid-test.pdf")

        report = engine.run()
        orphan_findings = [f for f in report.findings if f.finding_type == "orphaned_blob"]
        assert len(orphan_findings) == 1
        assert "orphan-uuid-test.pdf" in orphan_findings[0].message

    def test_metadata_sidecar_ignored(self, engine, blob_storage):
        """Metadata sidecar files (.metadata.json) are not flagged."""
        filepath = os.path.join(blob_storage, "files_blob", "test.metadata.json")
        with open(filepath, "w") as f:
            f.write("{}")

        report = engine.run()
        orphan_findings = [f for f in report.findings if f.finding_type == "orphaned_blob"]
        assert len(orphan_findings) == 0


class TestPhaseMissingFiles:
    """Phase 2: DB entries with no file on disk."""

    def test_no_missing(self, engine, recon_db, blob_storage):
        """No findings when all DB entries have files."""
        _insert_blob(recon_db, "uuid-exists", "/files_blob/uuid-exists-test.pdf")
        _create_file(blob_storage, "uuid-exists-test.pdf")

        report = engine.run()
        missing = [f for f in report.findings if f.finding_type == "missing_blob"]
        assert len(missing) == 0

    def test_missing_detected(self, engine, recon_db):
        """DB entry without file on disk is detected as error."""
        _insert_blob(recon_db, "uuid-gone", "/files_blob/uuid-gone-test.pdf")
        # Don't create the file

        report = engine.run()
        missing = [f for f in report.findings if f.finding_type == "missing_blob"]
        assert len(missing) == 1
        assert missing[0].severity == "error"
        assert missing[0].blob_uuid == "uuid-gone"


class TestPhaseStuckProcessing:
    """Phase 3: Blobs stuck in non-terminal status."""

    def test_no_stuck(self, engine, recon_db, blob_storage):
        """Recent uploads are not flagged."""
        _insert_blob(recon_db, "uuid-recent", "/files_blob/uuid-recent.pdf", status="uploaded")
        _create_file(blob_storage, "uuid-recent.pdf")

        report = engine.run()
        stuck = [f for f in report.findings if f.finding_type == "stuck_blob"]
        assert len(stuck) == 0

    def test_stuck_detected(self, engine, recon_db, blob_storage):
        """Blob uploaded > 24h ago still in 'processing' is flagged."""
        old_ts = int(time.time()) - (25 * 3600)  # 25 hours ago
        _insert_blob(
            recon_db, "uuid-stuck", "/files_blob/uuid-stuck.pdf",
            status="processing", uploaded_at_unix=old_ts,
        )
        _create_file(blob_storage, "uuid-stuck.pdf")

        report = engine.run()
        stuck = [f for f in report.findings if f.finding_type == "stuck_blob"]
        assert len(stuck) == 1
        assert stuck[0].blob_uuid == "uuid-stuck"
        assert "24" in stuck[0].message or "25" in stuck[0].message


class TestPhaseBatchIntegrity:
    """Phase 5: Batch entry count mismatches."""

    def test_batch_mismatch(self, engine, recon_db):
        """Batch claiming 3 files but having 1 entry is flagged."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())
        retention_unix = now_unix + (7 * 365 * 86400)

        # Insert a batch claiming 3 files (canonical: batch_display_id PK)
        recon_db.execute_insert(
            """INSERT INTO blob_batches
               (batch_display_id, batch_uuid, source, queue_mode, file_count,
                status, pending_sync, upload_status,
                uploaded_at_unix, uploaded_at_iso,
                retention_until_unix, retention_until_iso,
                total_size_bytes,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("HBB-batch-test", "batch-test", "relay-test", "bulk", 3,
             "finalized", 0, "finalized",
             now_unix, now,
             retention_unix, now,
             3072,
             now, now),
        )

        # Insert only 1 entry
        _insert_blob(recon_db, "uuid-batch-1", "/files_blob/uuid-batch-1.pdf", status="finalized")
        recon_db.execute_insert(
            "INSERT INTO blob_batch_entries (batch_display_id, file_display_id, sequence_number, created_at) VALUES (?, ?, ?, ?)",
            ("HBB-batch-test", "HB-uuid-batch-1", 1, now),
        )

        report = engine.run()
        batch_findings = [f for f in report.findings if f.finding_type == "batch_mismatch"]
        # We expect at least 1 finding for HBB-batch-test (claims 3, has 1)
        # _insert_blob auto-creates HBB-uuid-batch-1 with file_count=1 but no junction row,
        # so that's a second mismatch finding — both are valid
        assert len(batch_findings) >= 1
        test_batch_finding = [f for f in batch_findings if "HBB-batch-test" in f.message]
        assert len(test_batch_finding) == 1
        assert "3" in test_batch_finding[0].message


class TestFindingPersistence:
    """Findings are written to the notifications table."""

    def test_findings_persisted(self, engine, recon_db, blob_storage):
        """Findings from a run are written to notifications."""
        _create_file(blob_storage, "orphan-persist.pdf")

        report = engine.run()
        assert report.total_findings >= 1

        rows = recon_db.execute_query(
            "SELECT * FROM notifications WHERE created_by_service LIKE 'reconciliation/%'"
        )
        assert len(rows) >= 1
        assert rows[0]["notification_type"] == "orphaned_blob"


# ══════════════════════════════════════════════════════════════════════════
# API TESTS — Reconciliation Endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestReconciliationAPI:
    """HTTP endpoint tests for reconciliation."""

    def test_trigger_empty(self, recon_client):
        """POST /api/reconciliation/trigger runs successfully with empty data."""
        resp = recon_client.post("/api/reconciliation/trigger")
        assert resp.status_code == 200
        data = resp.json()
        assert "run_id" in data
        assert "phase_summaries" in data
        assert data["total_findings"] == 0

    def test_history_empty(self, recon_client):
        """GET /api/reconciliation/history returns empty before any runs."""
        resp = recon_client.get("/api/reconciliation/history")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_trigger_then_history(self, recon_client, blob_storage):
        """Findings from trigger appear in history."""
        _create_file(blob_storage, "api-orphan.pdf")

        resp = recon_client.post("/api/reconciliation/trigger")
        assert resp.status_code == 200
        assert resp.json()["total_findings"] >= 1

        resp = recon_client.get("/api/reconciliation/history")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1
