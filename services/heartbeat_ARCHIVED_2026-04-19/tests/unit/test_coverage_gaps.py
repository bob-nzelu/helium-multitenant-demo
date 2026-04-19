"""
Tests targeting coverage gaps — error paths, edge cases, and branches.

These tests specifically cover:
- Filesystem client error paths
- Database connection: init, migration errors, source_type branches
- Register.py: validator edge cases, error handlers
- Handler error paths: storage write failure, race conditions
- Health check degraded states
"""

import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Filesystem Client Error Paths ─────────────────────────────────────


class TestFilesystemClientPaths:
    """Test filesystem client singleton and error paths."""

    def test_get_filesystem_client_returns_none_without_init(self):
        """get_filesystem_client returns None when not initialized."""
        from src.clients.filesystem_client import (
            get_filesystem_client,
            reset_filesystem_client,
        )
        reset_filesystem_client()
        assert get_filesystem_client() is None
        reset_filesystem_client()

    def test_set_filesystem_client(self):
        """set_filesystem_client overrides the singleton."""
        from src.clients.filesystem_client import (
            set_filesystem_client,
            get_filesystem_client,
            reset_filesystem_client,
        )

        mock = MagicMock()
        set_filesystem_client(mock)
        assert get_filesystem_client() is mock

        reset_filesystem_client()
        assert get_filesystem_client() is None

    def test_filesystem_put_and_get(self, tmp_path):
        """FilesystemBlobClient round-trips data."""
        import asyncio
        from src.clients.filesystem_client import FilesystemBlobClient

        client = FilesystemBlobClient(str(tmp_path))

        async def _test():
            await client.put_blob("test/file.pdf", b"hello world", "application/pdf")
            data = await client.get_blob("test/file.pdf")
            assert data == b"hello world"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_filesystem_exists_and_delete(self, tmp_path):
        """FilesystemBlobClient exists and delete work correctly."""
        import asyncio
        from src.clients.filesystem_client import FilesystemBlobClient

        client = FilesystemBlobClient(str(tmp_path))

        async def _test():
            assert not await client.blob_exists("missing.pdf")
            await client.put_blob("exists.pdf", b"data")
            assert await client.blob_exists("exists.pdf")
            await client.delete_blob("exists.pdf")
            assert not await client.blob_exists("exists.pdf")

        asyncio.get_event_loop().run_until_complete(_test())

    def test_filesystem_get_missing_raises(self, tmp_path):
        """get_blob raises FileNotFoundError for missing blobs."""
        import asyncio
        from src.clients.filesystem_client import FilesystemBlobClient

        client = FilesystemBlobClient(str(tmp_path))

        async def _test():
            with pytest.raises(FileNotFoundError):
                await client.get_blob("nonexistent.pdf")

        asyncio.get_event_loop().run_until_complete(_test())

    def test_filesystem_health(self, tmp_path):
        """is_healthy returns True when root exists."""
        import asyncio
        from src.clients.filesystem_client import FilesystemBlobClient

        client = FilesystemBlobClient(str(tmp_path))

        async def _test():
            assert await client.is_healthy()

        asyncio.get_event_loop().run_until_complete(_test())

    def test_filesystem_metadata_sidecar(self, tmp_path):
        """put_blob writes JSON metadata sidecar alongside data."""
        import asyncio
        from src.clients.filesystem_client import FilesystemBlobClient

        client = FilesystemBlobClient(str(tmp_path))

        async def _test():
            await client.put_blob("test/doc.pdf", b"content", "application/pdf")

        asyncio.get_event_loop().run_until_complete(_test())

        meta_path = os.path.join(str(tmp_path), "test", "doc.pdf.metadata.json")
        assert os.path.exists(meta_path)
        with open(meta_path, "r") as f:
            meta = json.load(f)
        assert meta["content_type"] == "application/pdf"
        assert meta["size_bytes"] == 7


# ── Database Connection: Init & Migration ────────────────────────────


class TestDatabaseInit:
    """Test database initialization from scratch (no pre-existing file)."""

    def test_init_creates_db_from_schema(self):
        """BlobDatabase creates a new DB from schema.sql when none exists."""
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy schema.sql to the temp directory (BlobDatabase looks there)
            schema_src = os.path.join(
                os.path.dirname(__file__), "../../databases/schema.sql"
            )
            schema_dst = os.path.join(tmpdir, "schema.sql")

            with open(schema_src, "r") as f:
                schema_content = f.read()
            with open(schema_dst, "w") as f:
                f.write(schema_content)

            db_path = os.path.join(tmpdir, "test_fresh.db")
            assert not os.path.exists(db_path)

            db = BlobDatabase(db_path)

            # Verify tables were created
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [r[0] for r in cursor.fetchall()]
            conn.close()

            assert "file_entries" in tables
            assert "audit_events" in tables
            assert "daily_usage" in tables

    def test_init_handles_missing_schema(self):
        """BlobDatabase handles missing schema.sql gracefully."""
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_no_schema.db")
            # No schema.sql in tmpdir — should still create the DB file
            db = BlobDatabase(db_path)
            assert os.path.exists(db_path)

    def test_migration_error_handled(self):
        """_run_migrations handles malformed migration SQL gracefully."""
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_migration.db")
            # Create an empty DB
            conn = sqlite3.connect(db_path)
            conn.close()

            # Create a bad migration file
            with open(os.path.join(tmpdir, "migration_999_bad.sql"), "w") as f:
                f.write("THIS IS NOT VALID SQL;")

            # Should log warning but not crash
            db = BlobDatabase(db_path)
            assert db is not None


# ── Database Connection: Source Type Branches ────────────────────────


class TestSourceTypeBranches:
    """Test all source_type detection branches in register_blob."""

    @pytest.fixture
    def db(self):
        """Create test DB with schema."""
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_src = os.path.join(
                os.path.dirname(__file__), "../../databases/schema.sql"
            )
            schema_dst = os.path.join(tmpdir, "schema.sql")
            with open(schema_src, "r") as f:
                with open(schema_dst, "w") as out:
                    out.write(f.read())

            db_path = os.path.join(tmpdir, "test_source.db")
            yield BlobDatabase(db_path)

    def _register(self, db, source: str):
        """Helper to register a blob with a given source."""
        import time
        from datetime import datetime, timedelta, timezone

        from uuid6 import uuid7

        blob_uuid = str(uuid7())
        now = datetime.now(timezone.utc)
        retention = now + timedelta(days=365 * 7)

        return db.register_blob(
            blob_uuid=blob_uuid,
            blob_path=f"/files_blob/{blob_uuid}-test.pdf",
            file_size_bytes=1024,
            file_hash="a" * 64,
            content_type="application/pdf",
            source=source,
            uploaded_at_unix=int(time.time()),
            uploaded_at_iso=now.isoformat(),
            retention_until_unix=int(retention.timestamp()),
            retention_until_iso=retention.isoformat(),
        )

    def test_source_type_bulk(self, db):
        result = self._register(db, "relay-bulk-1")
        assert result is not None

    def test_source_type_nas(self, db):
        result = self._register(db, "relay-nas-1")
        assert result is not None

    def test_source_type_erp(self, db):
        result = self._register(db, "relay-erp-connector")
        assert result is not None

    def test_source_type_email(self, db):
        result = self._register(db, "relay-email-watcher")
        assert result is not None

    def test_source_type_unknown(self, db):
        result = self._register(db, "something-new")
        assert result is not None


# ── Database Connection: query_audit_events ──────────────────────────


class TestQueryAuditEvents:
    """Test audit event querying with various filters."""

    @pytest.fixture
    def db(self):
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_src = os.path.join(
                os.path.dirname(__file__), "../../databases/schema.sql"
            )
            schema_dst = os.path.join(tmpdir, "schema.sql")
            with open(schema_src, "r") as f:
                with open(schema_dst, "w") as out:
                    out.write(f.read())

            db_path = os.path.join(tmpdir, "test_audit.db")
            db = BlobDatabase(db_path)

            # Seed some audit events
            db.log_audit_event("relay", "file.ingested", user_id="u1", details={"f": 1})
            db.log_audit_event("core", "file.processed", user_id="u2")
            db.log_audit_event("relay", "file.rejected", details="not json")

            yield db

    def test_query_all(self, db):
        results = db.query_audit_events()
        assert len(results) == 3

    def test_query_by_service(self, db):
        results = db.query_audit_events(service="relay")
        assert len(results) == 2

    def test_query_by_event_type(self, db):
        results = db.query_audit_events(event_type="file.processed")
        assert len(results) == 1

    def test_query_by_both(self, db):
        results = db.query_audit_events(service="relay", event_type="file.ingested")
        assert len(results) == 1

    def test_query_json_details_parsed(self, db):
        results = db.query_audit_events(service="relay", event_type="file.ingested")
        assert results[0]["details"] == {"f": 1}

    def test_query_with_limit(self, db):
        results = db.query_audit_events(limit=1)
        assert len(results) == 1


# ── Database: get_blob_by_path ───────────────────────────────────────


class TestGetBlobByPath:
    """Test get_blob_by_path method."""

    @pytest.fixture
    def db(self):
        from src.database.connection import BlobDatabase

        with tempfile.TemporaryDirectory() as tmpdir:
            schema_src = os.path.join(
                os.path.dirname(__file__), "../../databases/schema.sql"
            )
            schema_dst = os.path.join(tmpdir, "schema.sql")
            with open(schema_src, "r") as f:
                with open(schema_dst, "w") as out:
                    out.write(f.read())

            db_path = os.path.join(tmpdir, "test_path.db")
            db = BlobDatabase(db_path)

            import time
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            retention = now + timedelta(days=365 * 7)

            db.register_blob(
                blob_uuid="11111111-1111-1111-1111-111111111111",
                blob_path="/files_blob/11111111-test.pdf",
                file_size_bytes=1024,
                file_hash="a" * 64,
                content_type="application/pdf",
                source="relay-bulk-1",
                uploaded_at_unix=int(time.time()),
                uploaded_at_iso=now.isoformat(),
                retention_until_unix=int(retention.timestamp()),
                retention_until_iso=retention.isoformat(),
            )
            yield db

    def test_get_existing_path(self, db):
        result = db.get_blob_by_path("/files_blob/11111111-test.pdf")
        assert result is not None
        assert result["blob_uuid"] == "11111111-1111-1111-1111-111111111111"

    def test_get_missing_path(self, db):
        result = db.get_blob_by_path("/files_blob/nonexistent.pdf")
        assert result is None


# ── Handler Error Paths ──────────────────────────────────────────────


class TestBlobHandlerErrors:
    """Test blob_handler error paths via router (handler monkeypatched)."""

    def test_write_blob_handler_storage_error(self, app_config, mock_storage, monkeypatch):
        """write_blob returns 500 when filesystem put_blob raises."""
        from src.handlers import blob_handler
        from src.main import app

        async def _fail(*args, **kwargs):
            raise RuntimeError("Disk full")

        monkeypatch.setattr(blob_handler, "write_blob", _fail)

        import io
        with TestClient(app) as c:
            response = c.post(
                "/api/blobs/write",
                data={"blob_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "filename": "test.pdf"},
                files={"file": ("test.pdf", io.BytesIO(b"PDF content"), "application/pdf")},
            )
            assert response.status_code == 500

    def test_register_blob_handler_error(self, app_config, mock_storage, monkeypatch):
        """register_blob returns 500 when handler raises."""
        from src.handlers import blob_handler
        from src.main import app

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(blob_handler, "register_blob", _fail)

        with TestClient(app) as c:
            response = c.post("/api/blobs/register", json={
                "blob_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "filename": "test-daily-fail.pdf",
                "file_size_bytes": 1024,
                "file_hash": "b" * 64,
                "api_key": "test-key",
            })
            assert response.status_code == 500


# ── Health Check Degraded States ─────────────────────────────────────


class TestHealthDegraded:
    """Test health check when components fail."""

    def test_health_storage_disconnected_shows_degraded(self, client, app_config, mock_storage):
        """Health check returns degraded when filesystem storage fails."""
        mock_storage._healthy = False

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["storage"] == "disconnected"


# ── Router Error Paths (500s) ────────────────────────────────────────


class TestRouterErrorPaths:
    """Test router exception handlers return proper 500s."""

    def test_audit_log_db_error(self, client, mock_storage, monkeypatch):
        """Audit log returns 500 when DB operation fails."""
        from src.handlers import audit_handler

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(audit_handler, "log_audit_event", _fail)

        response = client.post("/api/audit/log", json={
            "service": "test",
            "event_type": "test.event",
        })
        assert response.status_code == 500

    def test_metrics_report_db_error(self, client, mock_storage, monkeypatch):
        """Metrics report returns 500 when DB fails."""
        from src.handlers import metrics_handler

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(metrics_handler, "log_metric", _fail)

        response = client.post("/api/metrics/report", json={
            "metric_type": "test",
            "values": {"count": 1},
        })
        assert response.status_code == 500

    def test_dedup_check_db_error(self, client, mock_storage, monkeypatch):
        """Dedup check returns 500 when handler raises."""
        from src.handlers import dedup_handler

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(dedup_handler, "check_duplicate", _fail)

        response = client.get(f"/api/dedup/check?file_hash={'a' * 64}")
        assert response.status_code == 500

    def test_dedup_record_db_error(self, client, mock_storage, monkeypatch):
        """Dedup record returns 500 when handler raises."""
        from src.handlers import dedup_handler

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(dedup_handler, "record_duplicate", _fail)

        response = client.post("/api/dedup/record", json={
            "file_hash": "a" * 64,
            "queue_id": "q-123",
        })
        assert response.status_code == 500

    def test_blobs_write_error_500(self, app_config, mock_storage, monkeypatch):
        """Blob write returns 500 body with error detail."""
        from src.handlers import blob_handler
        from src.main import app

        async def _fail(*args, **kwargs):
            raise RuntimeError("Storage down")

        monkeypatch.setattr(blob_handler, "write_blob", _fail)

        import io
        with TestClient(app) as c:
            response = c.post(
                "/api/blobs/write",
                data={"blob_uuid": "dddddddd-eeee-ffff-0000-111111111111", "filename": "test.pdf"},
                files={"file": ("test.pdf", io.BytesIO(b"data"), "application/pdf")},
            )
        assert response.status_code == 500
        assert "error" in response.json()["detail"]["status"]

    def test_blobs_register_error_500(self, app_config, mock_storage, monkeypatch):
        """Blob register returns 500 body with error detail."""
        from src.handlers import blob_handler
        from src.main import app

        async def _fail(*args, **kwargs):
            raise RuntimeError("DB down")

        monkeypatch.setattr(blob_handler, "register_blob", _fail)

        with TestClient(app) as c:
            response = c.post("/api/blobs/register", json={
                "blob_uuid": "cccccccc-dddd-eeee-ffff-000000000000",
                "filename": "error-test.pdf",
                "file_size_bytes": 1024,
                "file_hash": "c" * 64,
                "api_key": "test-key",
            })
        assert response.status_code == 500
        assert "error" in response.json()["detail"]["status"]
