"""
SQLCipher-Aware Migration Runner for auth.db

Mirrors the logic of DatabaseMigrator but uses sqlcipher3 for encrypted
database connections. Reuses MigrationFile, MigrationRecord, MigrationResult,
and MigrationStatus data classes from the existing migrator module.

Usage:
    migrator = AuthDatabaseMigrator(db_path, migrations_dir, encryption_key)
    results = migrator.apply_pending()
    status = migrator.get_status()
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .migrator import (
    MigrationFile,
    MigrationRecord,
    MigrationResult,
    MigrationStatus,
)
from ..errors import DatabaseError

try:
    import sqlcipher3 as sqlite_module
    _HAS_SQLCIPHER = True
except ImportError:
    import sqlite3 as sqlite_module
    _HAS_SQLCIPHER = False


logger = logging.getLogger(__name__)


class AuthDatabaseMigrator:
    """
    SQLCipher-aware migration runner for auth.db.

    Follows the same pattern as DatabaseMigrator (versioned SQL files,
    checksum tracking, drift detection) but routes all connections
    through SQLCipher with PRAGMA key for encryption at rest.

    Falls back to plain sqlite3 if sqlcipher3 is not installed
    (logs a warning — encryption will not be active).
    """

    SCHEMA_MIGRATIONS_DDL = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            description TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            execution_time_ms INTEGER NOT NULL DEFAULT 0
        );
    """

    def __init__(
        self,
        db_path: str,
        migrations_dir: str,
        encryption_key: str = "",
        db_name: str = "auth",
    ):
        self.db_path = db_path
        self.migrations_dir = migrations_dir
        self.encryption_key = encryption_key
        self.db_name = db_name

        if encryption_key and not _HAS_SQLCIPHER:
            logger.warning(
                f"[{self.db_name}] sqlcipher3 not installed — "
                f"auth.db will NOT be encrypted. Install sqlcipher3-binary "
                f"for production use."
            )

    # ── Connection ────────────────────────────────────────────────

    def _connect(self):
        """
        Get a database connection with optional SQLCipher encryption.

        If encryption_key is set and sqlcipher3 is available, the
        connection is encrypted. Otherwise falls back to plain sqlite3.
        """
        conn = sqlite_module.connect(self.db_path)
        if self.encryption_key and _HAS_SQLCIPHER:
            conn.execute(f'PRAGMA key="{self.encryption_key}"')
        return conn

    # ── Public API (mirrors DatabaseMigrator) ─────────────────────

    def ensure_table(self) -> None:
        """Create the schema_migrations table if it doesn't exist."""
        conn = self._connect()
        try:
            conn.execute(self.SCHEMA_MIGRATIONS_DDL)
            conn.commit()
            logger.debug(f"[{self.db_name}] schema_migrations table ensured")
        finally:
            conn.close()

    def discover_migrations(self) -> List[MigrationFile]:
        """Scan the migrations directory for SQL files."""
        if not os.path.isdir(self.migrations_dir):
            logger.debug(
                f"[{self.db_name}] No migrations directory at "
                f"{self.migrations_dir}"
            )
            return []

        files = []
        for entry in sorted(os.listdir(self.migrations_dir)):
            if not entry.endswith(".sql"):
                continue
            filepath = os.path.join(self.migrations_dir, entry)
            if not os.path.isfile(filepath):
                continue
            try:
                mf = MigrationFile.from_path(filepath)
                files.append(mf)
            except ValueError as e:
                logger.warning(
                    f"[{self.db_name}] Skipping invalid migration: {e}"
                )

        files.sort(key=lambda m: m.version)

        # Check for duplicate versions
        seen = {}
        for mf in files:
            if mf.version in seen:
                raise DatabaseError(
                    message=(
                        f"Duplicate migration version {mf.version}: "
                        f"{seen[mf.version]} and {mf.filename}"
                    ),
                    details=[{
                        "db_name": self.db_name,
                        "version": str(mf.version),
                        "files": [seen[mf.version], mf.filename],
                    }],
                )
            seen[mf.version] = mf.filename

        return files

    def get_applied(self) -> List[MigrationRecord]:
        """Get all applied migration records from the database."""
        self.ensure_table()

        conn = self._connect()
        conn.row_factory = sqlite_module.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM schema_migrations ORDER BY version ASC"
            )
            rows = cursor.fetchall()
            return [
                MigrationRecord(
                    version=row["version"],
                    filename=row["filename"],
                    description=row["description"],
                    checksum=row["checksum"],
                    applied_at=row["applied_at"],
                    execution_time_ms=row["execution_time_ms"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def get_current_version(self) -> int:
        """Get the highest applied migration version."""
        applied = self.get_applied()
        if not applied:
            return 0
        return applied[-1].version

    def get_pending(self) -> List[MigrationFile]:
        """Get migrations that haven't been applied yet."""
        available = self.discover_migrations()
        applied_versions = {r.version for r in self.get_applied()}
        return [m for m in available if m.version not in applied_versions]

    def detect_drift(self) -> Tuple[bool, List[str]]:
        """Check if any applied migrations have been modified on disk."""
        applied = self.get_applied()
        available = {m.version: m for m in self.discover_migrations()}
        drift_details = []

        for record in applied:
            if record.version not in available:
                drift_details.append(
                    f"v{record.version} ({record.filename}): "
                    f"applied but file missing from disk"
                )
            elif available[record.version].checksum != record.checksum:
                drift_details.append(
                    f"v{record.version} ({record.filename}): "
                    f"checksum mismatch (applied={record.checksum[:12]}... "
                    f"vs disk={available[record.version].checksum[:12]}...)"
                )

        return (len(drift_details) > 0, drift_details)

    def apply_pending(
        self, stop_on_error: bool = True
    ) -> List[MigrationResult]:
        """Apply all pending migrations in version order."""
        self.ensure_table()

        pending = self.get_pending()
        if not pending:
            logger.info(f"[{self.db_name}] No pending migrations")
            return []

        logger.info(
            f"[{self.db_name}] Applying {len(pending)} pending migration(s)..."
        )

        results = []
        errored = False

        for migration in pending:
            if errored and stop_on_error:
                results.append(MigrationResult(
                    version=migration.version,
                    filename=migration.filename,
                    status="skipped",
                    error="Skipped due to previous error",
                ))
                continue

            result = self._apply_single(migration)
            results.append(result)

            if result.status == "error":
                errored = True

        applied_count = sum(1 for r in results if r.status == "applied")
        error_count = sum(1 for r in results if r.status == "error")
        skip_count = sum(1 for r in results if r.status == "skipped")

        logger.info(
            f"[{self.db_name}] Migration complete: "
            f"{applied_count} applied, {error_count} errors, "
            f"{skip_count} skipped"
        )

        return results

    def get_status(self) -> MigrationStatus:
        """Get comprehensive migration status."""
        applied = self.get_applied()
        pending = self.get_pending()
        has_drift, drift_details = self.detect_drift()

        current = applied[-1].version if applied else 0
        available = self.discover_migrations()
        latest = available[-1].version if available else 0

        return MigrationStatus(
            db_name=self.db_name,
            current_version=current,
            latest_available=latest,
            pending_count=len(pending),
            applied=applied,
            pending=pending,
            drift_detected=has_drift,
            drift_details=drift_details if has_drift else None,
        )

    # ── Private ───────────────────────────────────────────────────

    def _apply_single(self, migration: MigrationFile) -> MigrationResult:
        """Apply a single migration file."""
        logger.info(
            f"[{self.db_name}] Applying migration v{migration.version}: "
            f"{migration.filename}"
        )

        with open(migration.filepath, "r", encoding="utf-8") as f:
            sql = f.read()

        if not sql.strip():
            logger.warning(
                f"[{self.db_name}] Migration v{migration.version} is empty, "
                f"recording as applied"
            )
            self._record_migration(migration, execution_time_ms=0)
            return MigrationResult(
                version=migration.version,
                filename=migration.filename,
                status="applied",
                execution_time_ms=0,
            )

        start_ms = int(time.time() * 1000)

        conn = self._connect()
        try:
            conn.executescript(sql)

            end_ms = int(time.time() * 1000)
            elapsed = end_ms - start_ms

            self._record_migration(migration, execution_time_ms=elapsed)

            logger.info(
                f"[{self.db_name}] Migration v{migration.version} applied "
                f"in {elapsed}ms"
            )

            return MigrationResult(
                version=migration.version,
                filename=migration.filename,
                status="applied",
                execution_time_ms=elapsed,
            )

        except Exception as e:
            logger.error(
                f"[{self.db_name}] Migration v{migration.version} FAILED: {e}"
            )
            return MigrationResult(
                version=migration.version,
                filename=migration.filename,
                status="error",
                error=str(e),
            )
        finally:
            conn.close()

    def _record_migration(
        self, migration: MigrationFile, execution_time_ms: int
    ) -> None:
        """Record a successful migration in schema_migrations."""
        now = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO schema_migrations
                   (version, filename, description, checksum,
                    applied_at, execution_time_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    migration.version,
                    migration.filename,
                    migration.description,
                    migration.checksum,
                    now,
                    execution_time_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()
