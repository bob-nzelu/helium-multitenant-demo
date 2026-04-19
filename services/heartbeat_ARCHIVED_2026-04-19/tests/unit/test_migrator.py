"""
Tests for HeartBeat SQL Migration Framework (P2-C)

Tests:
    1. MigrationFile.from_path — parse valid migration filename
    2. MigrationFile.from_path — reject invalid filename
    3. discover_migrations — finds and sorts files
    4. discover_migrations — detects duplicate versions
    5. discover_migrations — returns empty for missing dir
    6. ensure_table — creates schema_migrations table
    7. apply_pending — applies migrations in order
    8. apply_pending — skips already-applied migrations
    9. apply_pending — records checksum in schema_migrations
    10. apply_pending — stops on error (stop_on_error=True)
    11. apply_pending — handles empty migration file
    12. detect_drift — detects modified migration file
    13. detect_drift — detects missing migration file
    14. get_status — returns complete status
    15. verify_integrity — detects version gaps
    16. apply_pending via startup — integration with BlobDatabase
"""

import hashlib
import os
import sqlite3

import pytest

# Ensure HeartBeat root is on sys.path (same as conftest.py)
import sys
from pathlib import Path
heartbeat_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(heartbeat_root))

from src.database.migrator import (
    DatabaseMigrator,
    MigrationFile,
    MigrationRecord,
    MigrationResult,
    MigrationStatus,
)
from src.errors import DatabaseError


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    """Create a temporary SQLite database."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS dummy (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def migrations_dir(tmp_path):
    """Create a temporary migrations directory."""
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    return str(mdir)


@pytest.fixture
def migrator(db_path, migrations_dir):
    """Create a DatabaseMigrator instance."""
    return DatabaseMigrator(
        db_path=db_path,
        migrations_dir=migrations_dir,
        db_name="test",
    )


def _write_migration(migrations_dir: str, filename: str, sql: str) -> str:
    """Helper to write a migration SQL file."""
    path = os.path.join(migrations_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(sql)
    return path


# ── Test MigrationFile Parsing ────────────────────────────────────────────

class TestMigrationFileParsing:
    """Tests for MigrationFile.from_path()."""

    def test_parse_valid_migration_filename(self, migrations_dir):
        """Test parsing a well-formed migration filename."""
        path = _write_migration(
            migrations_dir,
            "001_add_users_table.sql",
            "CREATE TABLE users (id INTEGER PRIMARY KEY);",
        )

        mf = MigrationFile.from_path(path)

        assert mf.version == 1
        assert mf.filename == "001_add_users_table.sql"
        assert mf.description == "add users table"
        assert mf.filepath == path
        assert len(mf.checksum) == 64  # SHA-256 hex

    def test_reject_invalid_filename_no_number(self, migrations_dir):
        """Test that non-numeric prefix is rejected."""
        path = _write_migration(
            migrations_dir,
            "abc_bad_name.sql",
            "SELECT 1;",
        )

        with pytest.raises(ValueError, match="Invalid migration filename"):
            MigrationFile.from_path(path)

    def test_reject_invalid_filename_no_underscore(self, migrations_dir):
        """Test that filename without underscore separator is rejected."""
        path = _write_migration(
            migrations_dir,
            "001.sql",
            "SELECT 1;",
        )

        with pytest.raises(ValueError, match="Invalid migration filename"):
            MigrationFile.from_path(path)

    def test_checksum_changes_with_content(self, migrations_dir):
        """Verify checksum reflects file content."""
        path = _write_migration(
            migrations_dir,
            "001_test.sql",
            "CREATE TABLE a (id INTEGER);",
        )
        mf1 = MigrationFile.from_path(path)

        # Modify the file
        with open(path, "w") as f:
            f.write("CREATE TABLE b (id INTEGER);")
        mf2 = MigrationFile.from_path(path)

        assert mf1.checksum != mf2.checksum


# ── Test Discovery ────────────────────────────────────────────────────────

class TestDiscoverMigrations:
    """Tests for DatabaseMigrator.discover_migrations()."""

    def test_discovers_and_sorts_files(self, migrator, migrations_dir):
        """Migrations are discovered and sorted by version."""
        _write_migration(migrations_dir, "003_third.sql", "SELECT 3;")
        _write_migration(migrations_dir, "001_first.sql", "SELECT 1;")
        _write_migration(migrations_dir, "002_second.sql", "SELECT 2;")

        results = migrator.discover_migrations()

        assert len(results) == 3
        assert results[0].version == 1
        assert results[1].version == 2
        assert results[2].version == 3

    def test_detects_duplicate_versions(self, migrator, migrations_dir):
        """Duplicate version numbers raise DatabaseError."""
        _write_migration(migrations_dir, "001_first.sql", "SELECT 1;")
        _write_migration(migrations_dir, "001_also_first.sql", "SELECT 1;")

        with pytest.raises(DatabaseError, match="Duplicate migration version"):
            migrator.discover_migrations()

    def test_returns_empty_for_missing_directory(self, db_path):
        """Missing migrations dir returns empty list (not an error)."""
        migrator = DatabaseMigrator(
            db_path=db_path,
            migrations_dir="/nonexistent/path/migrations",
            db_name="test",
        )
        assert migrator.discover_migrations() == []

    def test_skips_non_sql_files(self, migrator, migrations_dir):
        """Non-.sql files are ignored."""
        _write_migration(migrations_dir, "001_real.sql", "SELECT 1;")
        # Write a non-SQL file
        with open(os.path.join(migrations_dir, "README.md"), "w") as f:
            f.write("# Migrations")

        results = migrator.discover_migrations()
        assert len(results) == 1
        assert results[0].filename == "001_real.sql"


# ── Test Table Creation ───────────────────────────────────────────────────

class TestEnsureTable:
    """Tests for DatabaseMigrator.ensure_table()."""

    def test_creates_schema_migrations_table(self, migrator, db_path):
        """ensure_table creates the schema_migrations table."""
        migrator.ensure_table()

        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_idempotent_creation(self, migrator, db_path):
        """Calling ensure_table twice doesn't error."""
        migrator.ensure_table()
        migrator.ensure_table()  # Should not raise

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM schema_migrations")
        assert cursor.fetchone()[0] == 0
        conn.close()


# ── Test Apply ────────────────────────────────────────────────────────────

class TestApplyPending:
    """Tests for DatabaseMigrator.apply_pending()."""

    def test_applies_migrations_in_order(self, migrator, migrations_dir, db_path):
        """Pending migrations are applied in version order."""
        _write_migration(
            migrations_dir,
            "001_create_alpha.sql",
            "CREATE TABLE alpha (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "002_create_beta.sql",
            "CREATE TABLE beta (id INTEGER PRIMARY KEY);",
        )

        results = migrator.apply_pending()

        assert len(results) == 2
        assert results[0].version == 1
        assert results[0].status == "applied"
        assert results[1].version == 2
        assert results[1].status == "applied"

        # Verify tables actually exist
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "alpha" in tables
        assert "beta" in tables

    def test_skips_already_applied(self, migrator, migrations_dir):
        """Applied migrations are not re-applied."""
        _write_migration(
            migrations_dir,
            "001_first.sql",
            "CREATE TABLE first_table (id INTEGER PRIMARY KEY);",
        )

        # Apply once
        results1 = migrator.apply_pending()
        assert len(results1) == 1
        assert results1[0].status == "applied"

        # Apply again — should find nothing pending
        results2 = migrator.apply_pending()
        assert len(results2) == 0

    def test_records_checksum(self, migrator, migrations_dir, db_path):
        """Applied migration's checksum is recorded in schema_migrations."""
        sql = "CREATE TABLE checksummed (id INTEGER PRIMARY KEY);"
        _write_migration(migrations_dir, "001_checksummed.sql", sql)

        expected_checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

        migrator.apply_pending()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM schema_migrations WHERE version = 1")
        row = dict(cursor.fetchone())
        conn.close()

        assert row["checksum"] == expected_checksum
        assert row["filename"] == "001_checksummed.sql"
        assert row["description"] == "checksummed"
        assert row["execution_time_ms"] >= 0

    def test_stops_on_error(self, migrator, migrations_dir, db_path):
        """Subsequent migrations are skipped when one fails."""
        _write_migration(
            migrations_dir,
            "001_good.sql",
            "CREATE TABLE good (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "002_bad.sql",
            "THIS IS NOT VALID SQL;",
        )
        _write_migration(
            migrations_dir,
            "003_never_reached.sql",
            "CREATE TABLE never (id INTEGER PRIMARY KEY);",
        )

        results = migrator.apply_pending(stop_on_error=True)

        assert len(results) == 3
        assert results[0].status == "applied"
        assert results[1].status == "error"
        assert results[1].error is not None
        assert results[2].status == "skipped"

        # Verify good table exists, never table does not
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "good" in tables
        assert "never" not in tables

    def test_handles_empty_migration_file(self, migrator, migrations_dir, db_path):
        """Empty SQL file is recorded as applied (no-op)."""
        _write_migration(migrations_dir, "001_empty.sql", "")

        results = migrator.apply_pending()

        assert len(results) == 1
        assert results[0].status == "applied"

        # Should be recorded in schema_migrations
        applied = migrator.get_applied()
        assert len(applied) == 1
        assert applied[0].version == 1


# ── Test Drift Detection ─────────────────────────────────────────────────

class TestDriftDetection:
    """Tests for DatabaseMigrator.detect_drift()."""

    def test_detects_modified_file(self, migrator, migrations_dir):
        """Drift detected when applied file is modified on disk."""
        path = _write_migration(
            migrations_dir,
            "001_original.sql",
            "CREATE TABLE original (id INTEGER PRIMARY KEY);",
        )

        # Apply it
        migrator.apply_pending()

        # Modify the file on disk
        with open(path, "w") as f:
            f.write("CREATE TABLE modified (id INTEGER PRIMARY KEY);")

        has_drift, details = migrator.detect_drift()

        assert has_drift is True
        assert len(details) == 1
        assert "checksum mismatch" in details[0]

    def test_detects_missing_file(self, migrator, migrations_dir):
        """Drift detected when applied file is deleted from disk."""
        path = _write_migration(
            migrations_dir,
            "001_will_be_deleted.sql",
            "CREATE TABLE temp (id INTEGER PRIMARY KEY);",
        )

        # Apply it
        migrator.apply_pending()

        # Delete the file
        os.remove(path)

        has_drift, details = migrator.detect_drift()

        assert has_drift is True
        assert len(details) == 1
        assert "file missing from disk" in details[0]

    def test_no_drift_when_clean(self, migrator, migrations_dir):
        """No drift when files match applied checksums."""
        _write_migration(
            migrations_dir,
            "001_clean.sql",
            "CREATE TABLE clean (id INTEGER PRIMARY KEY);",
        )

        migrator.apply_pending()

        has_drift, details = migrator.detect_drift()

        assert has_drift is False
        assert len(details) == 0


# ── Test Status & Integrity ──────────────────────────────────────────────

class TestStatusAndIntegrity:
    """Tests for get_status() and verify_integrity()."""

    def test_get_status_comprehensive(self, migrator, migrations_dir):
        """get_status returns complete migration state."""
        _write_migration(
            migrations_dir,
            "001_applied.sql",
            "CREATE TABLE applied (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "002_pending.sql",
            "CREATE TABLE pending (id INTEGER PRIMARY KEY);",
        )

        # Apply only the first
        migrator.ensure_table()
        migrator.apply_pending()

        # Add a third migration (now 002 is applied, add 003)
        _write_migration(
            migrations_dir,
            "003_also_pending.sql",
            "CREATE TABLE also_pending (id INTEGER PRIMARY KEY);",
        )

        status = migrator.get_status()

        assert status.db_name == "test"
        assert status.current_version == 2  # both 001 and 002 applied
        assert status.latest_available == 3
        assert status.pending_count == 1  # only 003 pending
        assert len(status.applied) == 2
        assert len(status.pending) == 1
        assert status.pending[0].version == 3
        assert status.drift_detected is False

    def test_verify_integrity_version_gaps(self, migrator, migrations_dir, db_path):
        """verify_integrity detects version gaps in applied migrations."""
        _write_migration(
            migrations_dir,
            "001_first.sql",
            "CREATE TABLE first (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "003_third.sql",
            "CREATE TABLE third (id INTEGER PRIMARY KEY);",
        )

        migrator.apply_pending()

        is_healthy, issues = migrator.verify_integrity()

        assert is_healthy is False
        assert any("Version gap" in issue for issue in issues)

    def test_get_current_version_empty(self, migrator):
        """Current version is 0 when no migrations applied."""
        assert migrator.get_current_version() == 0

    def test_get_current_version_after_apply(self, migrator, migrations_dir):
        """Current version reflects the latest applied migration."""
        _write_migration(
            migrations_dir,
            "001_a.sql",
            "CREATE TABLE a (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "002_b.sql",
            "CREATE TABLE b (id INTEGER PRIMARY KEY);",
        )

        migrator.apply_pending()
        assert migrator.get_current_version() == 2
