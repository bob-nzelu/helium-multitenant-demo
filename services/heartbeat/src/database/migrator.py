"""
HeartBeat SQL Migration Framework (P2-C)

Proper migration runner replacing the primitive _run_migrations() approach.
Supports forward migrations with checksum verification, drift detection,
and idempotent application.

Each migration is a versioned SQL file:
    databases/migrations/{db_name}/NNN_description.sql

State tracked in `schema_migrations` table within each database.

Usage:
    migrator = DatabaseMigrator(db_path, migrations_dir)
    results = migrator.apply_pending()
    status = migrator.get_status()
"""

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..errors import DatabaseError


logger = logging.getLogger(__name__)


# ── Data Classes ──────────────────────────────────────────────────────────

@dataclass
class MigrationFile:
    """Represents a migration SQL file on disk."""
    version: int
    filename: str
    filepath: str
    description: str
    checksum: str  # SHA-256 of file contents

    @classmethod
    def from_path(cls, filepath: str) -> "MigrationFile":
        """Parse a migration file from its path."""
        filename = os.path.basename(filepath)

        # Expected format: NNN_description.sql
        parts = filename.split("_", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not filename.endswith(".sql"):
            raise ValueError(
                f"Invalid migration filename: {filename}. "
                f"Expected format: NNN_description.sql (e.g., 001_add_schema_migrations.sql)"
            )

        version = int(parts[0])
        description = parts[1].replace(".sql", "").replace("_", " ")

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

        return cls(
            version=version,
            filename=filename,
            filepath=filepath,
            description=description,
            checksum=checksum,
        )


@dataclass
class MigrationRecord:
    """A migration record from the schema_migrations table."""
    version: int
    filename: str
    description: str
    checksum: str
    applied_at: str
    execution_time_ms: int


@dataclass
class MigrationResult:
    """Result of applying a single migration."""
    version: int
    filename: str
    status: str  # "applied", "skipped", "error"
    execution_time_ms: int = 0
    error: Optional[str] = None


@dataclass
class MigrationStatus:
    """Overall migration status for a database."""
    db_name: str
    current_version: int
    latest_available: int
    pending_count: int
    applied: List[MigrationRecord]
    pending: List[MigrationFile]
    drift_detected: bool = False
    drift_details: Optional[List[str]] = None


# ── Migrator ──────────────────────────────────────────────────────────────

class DatabaseMigrator:
    """
    SQL migration runner for a single SQLite database.

    Applies versioned .sql files from a migrations directory,
    tracks state in a schema_migrations table, and detects
    drift (checksum mismatches against previously applied migrations).
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
        db_name: str = "unknown",
    ):
        """
        Initialize the migrator.

        Args:
            db_path: Path to the SQLite database file.
            migrations_dir: Directory containing NNN_*.sql migration files.
            db_name: Logical name for logging ("blob", "registry", "config").
        """
        self.db_path = db_path
        self.migrations_dir = migrations_dir
        self.db_name = db_name

    # ── Public API ────────────────────────────────────────────────────

    def ensure_table(self) -> None:
        """Create the schema_migrations table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(self.SCHEMA_MIGRATIONS_DDL)
            conn.commit()
            logger.debug(f"[{self.db_name}] schema_migrations table ensured")
        finally:
            conn.close()

    def discover_migrations(self) -> List[MigrationFile]:
        """
        Scan the migrations directory for SQL files.

        Returns:
            Sorted list of MigrationFile objects (ascending by version).

        Raises:
            DatabaseError: If migrations directory doesn't exist.
        """
        if not os.path.isdir(self.migrations_dir):
            logger.debug(
                f"[{self.db_name}] No migrations directory at {self.migrations_dir}"
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
                logger.warning(f"[{self.db_name}] Skipping invalid migration: {e}")

        # Sort by version (should already be sorted by filename, but be explicit)
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
        """
        Get all applied migration records from the database.

        Returns:
            List of MigrationRecord objects ordered by version.
        """
        self.ensure_table()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
        """
        Get the highest applied migration version.

        Returns:
            The highest version number, or 0 if no migrations applied.
        """
        applied = self.get_applied()
        if not applied:
            return 0
        return applied[-1].version

    def get_pending(self) -> List[MigrationFile]:
        """
        Get migrations that haven't been applied yet.

        Returns:
            List of MigrationFile objects pending application.
        """
        available = self.discover_migrations()
        applied_versions = {r.version for r in self.get_applied()}
        return [m for m in available if m.version not in applied_versions]

    def detect_drift(self) -> Tuple[bool, List[str]]:
        """
        Check if any applied migrations have been modified on disk.

        Compares checksums of applied migrations against current files.

        Returns:
            Tuple of (has_drift, list_of_drift_descriptions).
        """
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

    def apply_pending(self, stop_on_error: bool = True) -> List[MigrationResult]:
        """
        Apply all pending migrations in version order.

        Each migration runs in its own transaction. If a migration fails
        and stop_on_error is True, subsequent migrations are skipped.

        Args:
            stop_on_error: Stop applying if a migration fails (default True).

        Returns:
            List of MigrationResult objects for each migration attempted.
        """
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
            f"{applied_count} applied, {error_count} errors, {skip_count} skipped"
        )

        return results

    def get_status(self) -> MigrationStatus:
        """
        Get comprehensive migration status.

        Returns:
            MigrationStatus with applied, pending, and drift info.
        """
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

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """
        Full integrity check: drift detection + version gap detection.

        Returns:
            Tuple of (is_healthy, list_of_issues).
        """
        issues = []

        # Check drift
        has_drift, drift_details = self.detect_drift()
        if has_drift:
            issues.extend(drift_details)

        # Check for version gaps in applied migrations
        applied = self.get_applied()
        if len(applied) > 1:
            for i in range(1, len(applied)):
                expected = applied[i - 1].version + 1
                actual = applied[i].version
                # We allow non-contiguous versions (e.g., 001, 003 if 002 was removed)
                # but log it as informational
                if actual != expected:
                    issues.append(
                        f"Version gap: v{applied[i-1].version} → v{actual} "
                        f"(expected v{expected})"
                    )

        return (len(issues) == 0, issues)

    # ── Private ───────────────────────────────────────────────────────

    def _apply_single(self, migration: MigrationFile) -> MigrationResult:
        """
        Apply a single migration file.

        Reads the SQL, executes it, and records it in schema_migrations.
        """
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

        conn = sqlite3.connect(self.db_path)
        try:
            # Execute migration SQL
            # We use executescript() which auto-commits and handles
            # multiple statements. This is how the existing codebase works.
            conn.executescript(sql)

            end_ms = int(time.time() * 1000)
            elapsed = end_ms - start_ms

            # Record in schema_migrations (separate transaction)
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

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT INTO schema_migrations
                   (version, filename, description, checksum, applied_at, execution_time_ms)
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
