"""
HeartBeat Database Connection Module

Manages SQLite connection for blob.db (blob storage tracking).
Thread-safe connection management with context managers.

Database: blob.db (canonical blob schema v1.4.0)
Tables: file_entries, blob_batches, blob_batch_entries, blob_outputs,
        blob_downloads, blob_deduplication, daily_usage,
        audit_events, metrics_events, blob_schema_version, etc.

Identity model (canonical v1.1.0+):
  file_display_id (SDK-generated PK) + blob_uuid (HeartBeat-generated)
  batch_display_id (SDK-generated PK) + batch_uuid (HeartBeat-generated)
"""

import json
import sqlite3
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from threading import Lock


logger = logging.getLogger(__name__)


class BlobDatabase:
    """
    SQLite database connection manager for blob storage.

    Thread-safe singleton pattern for connection management.
    Provides helper methods for blob, dedup, daily usage, audit, and metrics operations.
    """

    _instance = None
    _lock = Lock()

    def __init__(self, db_path: str):
        """
        Initialize database connection.

        Args:
            db_path: Absolute path to blob.db
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

        # Initialize database if needed
        self._initialize_database()
        # Run migrations for existing databases
        self._run_migrations()

    def _initialize_database(self):
        """
        Initialize database with schema if it doesn't exist.
        Runs schema.sql and seed.sql if database is empty.
        """
        db_exists = os.path.exists(self.db_path)

        if not db_exists:
            logger.info(f"Creating new blob database at {self.db_path}")

            # Create database directory if needed
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            # Create database and run schema
            conn = sqlite3.connect(self.db_path)

            # Load schema
            schema_path = os.path.join(
                os.path.dirname(self.db_path),
                "schema.sql"
            )

            if os.path.exists(schema_path):
                with open(schema_path, 'r') as f:
                    schema_sql = f.read()
                    conn.executescript(schema_sql)
                    logger.info("Database schema created successfully")

            # Load seed data
            seed_path = os.path.join(
                os.path.dirname(self.db_path),
                "seed.sql"
            )

            if os.path.exists(seed_path):
                with open(seed_path, 'r') as f:
                    seed_sql = f.read()
                    conn.executescript(seed_sql)
                    logger.info("Seed data loaded successfully")

            conn.commit()
            conn.close()
        else:
            logger.info(f"Using existing blob database at {self.db_path}")

    def _run_migrations(self):
        """
        Run pending migrations on existing databases.
        Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).
        """
        migrations_dir = os.path.dirname(self.db_path)
        migration_files = sorted(
            f for f in os.listdir(migrations_dir)
            if f.startswith("migration_") and f.endswith(".sql")
        )

        if not migration_files:
            return

        conn = sqlite3.connect(self.db_path)
        try:
            for migration_file in migration_files:
                migration_path = os.path.join(migrations_dir, migration_file)
                with open(migration_path, 'r') as f:
                    migration_sql = f.read()
                    conn.executescript(migration_sql)
                    logger.info(f"Migration applied: {migration_file}")
            conn.commit()
        except Exception as e:
            logger.warning(f"Migration error (may be already applied): {e}")
        finally:
            conn.close()

    @contextmanager
    def get_connection(self):
        """
        Get database connection with context manager.

        Usage:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM file_entries")

        Yields:
            sqlite3.Connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries

        try:
            yield conn
        finally:
            conn.close()

    def execute_query(
        self,
        query: str,
        params: Optional[tuple] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute SELECT query and return results as list of dictionaries.

        Args:
            query: SQL SELECT query
            params: Optional query parameters

        Returns:
            List of row dictionaries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            rows = cursor.fetchall()

            # Convert Row objects to dictionaries
            return [dict(row) for row in rows]

    def execute_insert(
        self,
        query: str,
        params: tuple
    ) -> int:
        """
        Execute INSERT query and return last row ID.

        Args:
            query: SQL INSERT query
            params: Query parameters

        Returns:
            Last inserted row ID

        Raises:
            sqlite3.IntegrityError: On UNIQUE constraint violation
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    def execute_update(
        self,
        query: str,
        params: tuple
    ) -> int:
        """
        Execute UPDATE query and return number of affected rows.

        Args:
            query: SQL UPDATE query
            params: Query parameters

        Returns:
            Number of rows affected
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount

    # ── Blob Operations ─────────────────────────────────────────────────

    def get_blob(self, blob_uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get file entry by blob_uuid (HeartBeat canonical ID).

        Args:
            blob_uuid: HeartBeat-generated blob UUID

        Returns:
            File entry dictionary or None if not found
        """
        query = "SELECT * FROM file_entries WHERE blob_uuid = ?"
        results = self.execute_query(query, (blob_uuid,))
        return results[0] if results else None

    def get_file_by_display_id(self, file_display_id: str) -> Optional[Dict[str, Any]]:
        """
        Get file entry by file_display_id (SDK-generated PK).

        Args:
            file_display_id: SDK-generated display ID

        Returns:
            File entry dictionary or None if not found
        """
        query = "SELECT * FROM file_entries WHERE file_display_id = ?"
        results = self.execute_query(query, (file_display_id,))
        return results[0] if results else None

    def blob_exists(self, blob_uuid: str) -> bool:
        """
        Check if blob exists in database.

        Args:
            blob_uuid: Blob UUID

        Returns:
            True if exists, False otherwise
        """
        query = "SELECT 1 FROM file_entries WHERE blob_uuid = ? LIMIT 1"
        results = self.execute_query(query, (blob_uuid,))
        return len(results) > 0

    def register_blob(
        self,
        blob_uuid: str,
        blob_path: str,
        file_size_bytes: int,
        file_hash: str,
        content_type: str,
        source: str,
        uploaded_at_unix: int,
        uploaded_at_iso: str,
        retention_until_unix: int,
        retention_until_iso: str,
        identity: Optional[Dict[str, Any]] = None,
        file_display_id: Optional[str] = None,
        batch_display_id: Optional[str] = None,
        original_filename: Optional[str] = None,
        connection_type: Optional[str] = None,
        queue_mode: Optional[str] = None,
    ) -> int:
        """
        Register new file entry with canonical dual identity.

        Args:
            blob_uuid: HeartBeat-generated blob identifier
            blob_path: Storage object path
            file_size_bytes: File size in bytes
            file_hash: SHA256 hash
            content_type: MIME type
            source: Source relay instance ID
            uploaded_at_unix: Upload timestamp (Unix)
            uploaded_at_iso: Upload timestamp (ISO-8601)
            retention_until_unix: Retention expiry (Unix)
            retention_until_iso: Retention expiry (ISO-8601)
            identity: SDK identity/trace fields (user_trace_id, x_trace_id,
                      helium_user_id, float_id, session_id, machine_guid,
                      mac_address, computer_name). All optional.
            file_display_id: SDK-generated display ID (PK). If None, synthetic
                            'HB-{blob_uuid}' is generated.
            batch_display_id: SDK-generated batch display ID. If None, a
                             synthetic 'HBB-{blob_uuid}' is created.
            original_filename: Original filename. If None, extracted from blob_path.
            connection_type: Connection type (manual, nas, erp, api, email)
            queue_mode: Queue mode (bulk, api, polling, watcher, dbc, email)

        Returns:
            Inserted row ID

        Raises:
            sqlite3.IntegrityError: If file_display_id or blob_uuid already exists
        """
        # Extract original filename from blob_path if not provided
        if original_filename is None:
            original_filename = blob_path.split("/")[-1]

        # Generate synthetic display IDs for server-originated registrations
        if file_display_id is None:
            file_display_id = f"HB-{blob_uuid}"

        if batch_display_id is None:
            batch_display_id = f"HBB-{blob_uuid}"

        # Determine source_type from source ID
        if "bulk" in source.lower():
            source_type = "bulk"
        elif "nas" in source.lower():
            source_type = "nas"
        elif "erp" in source.lower():
            source_type = "erp"
        elif "email" in source.lower():
            source_type = "email"
        else:
            source_type = "unknown"

        # Extract identity fields (all nullable)
        id_fields = identity or {}

        now_iso = datetime.now(timezone.utc).isoformat()

        # Ensure batch exists (auto-create single-file batch if needed)
        self._ensure_batch(
            batch_display_id=batch_display_id,
            batch_uuid=blob_uuid,  # Single-file: batch_uuid = blob_uuid
            source=source,
            file_count=1,
            total_size_bytes=file_size_bytes,
            uploaded_at_unix=uploaded_at_unix,
            uploaded_at_iso=uploaded_at_iso,
            retention_until_unix=retention_until_unix,
            retention_until_iso=retention_until_iso,
            identity=id_fields,
            connection_type=connection_type,
            queue_mode=queue_mode or "bulk",
        )

        query = """
            INSERT INTO file_entries (
                file_display_id, blob_uuid, blob_path, original_filename,
                batch_display_id,
                source, source_type, connection_type, connection_id,
                display_name,
                content_type, file_size_bytes, file_hash,
                status, pending_sync, queue_mode,
                uploaded_at_unix, uploaded_at_iso,
                retention_until_unix, retention_until_iso,
                user_trace_id, x_trace_id, helium_user_id,
                float_id, session_id,
                machine_guid, mac_address, computer_name,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """

        params = (
            file_display_id,
            blob_uuid,
            blob_path,
            original_filename,
            batch_display_id,
            source,
            source_type,
            connection_type,
            id_fields.get("connection_id"),
            original_filename,  # display_name defaults to filename
            content_type,
            file_size_bytes,
            file_hash,
            "uploaded",  # Initial status (server-confirmed)
            0,           # pending_sync = 0 (confirmed — written by HeartBeat)
            queue_mode,
            uploaded_at_unix,
            uploaded_at_iso,
            retention_until_unix,
            retention_until_iso,
            id_fields.get("user_trace_id"),
            id_fields.get("x_trace_id"),
            id_fields.get("helium_user_id"),
            id_fields.get("float_id"),
            id_fields.get("session_id"),
            id_fields.get("machine_guid"),
            id_fields.get("mac_address"),
            id_fields.get("computer_name"),
            now_iso,
            now_iso,
        )

        row_id = self.execute_insert(query, params)

        # Create junction entry
        self._create_batch_entry(batch_display_id, file_display_id)

        return row_id

    def _ensure_batch(
        self,
        batch_display_id: str,
        batch_uuid: Optional[str],
        source: str,
        file_count: int,
        total_size_bytes: int,
        uploaded_at_unix: int,
        uploaded_at_iso: str,
        retention_until_unix: int,
        retention_until_iso: str,
        identity: Dict[str, Any],
        connection_type: Optional[str] = None,
        queue_mode: str = "bulk",
    ) -> None:
        """Auto-create batch if it doesn't exist (idempotent)."""
        now_iso = datetime.now(timezone.utc).isoformat()

        query = """
            INSERT OR IGNORE INTO blob_batches (
                batch_display_id, batch_uuid, source_document_id,
                source, connection_type, queue_mode,
                file_count, total_size_bytes,
                status, pending_sync, upload_status,
                uploaded_at_unix, uploaded_at_iso,
                retention_until_unix, retention_until_iso,
                user_trace_id, x_trace_id, helium_user_id,
                float_id, session_id,
                machine_guid, mac_address, computer_name,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
        """
        params = (
            batch_display_id,
            batch_uuid,
            identity.get("source_document_id"),
            source,
            connection_type,
            queue_mode,
            file_count,
            total_size_bytes,
            "uploaded",  # Batch status: uploaded (server-confirmed)
            0,           # pending_sync = 0
            "uploaded",
            uploaded_at_unix,
            uploaded_at_iso,
            retention_until_unix,
            retention_until_iso,
            identity.get("user_trace_id"),
            identity.get("x_trace_id"),
            identity.get("helium_user_id"),
            identity.get("float_id"),
            identity.get("session_id"),
            identity.get("machine_guid"),
            identity.get("mac_address"),
            identity.get("computer_name"),
            now_iso,
            now_iso,
        )

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()

    def _create_batch_entry(
        self,
        batch_display_id: str,
        file_display_id: str,
    ) -> None:
        """Create junction row linking file to batch (idempotent)."""
        now_iso = datetime.now(timezone.utc).isoformat()

        query = """
            INSERT OR IGNORE INTO blob_batch_entries (
                batch_display_id, file_display_id, created_at
            ) VALUES (?, ?, ?)
        """

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (batch_display_id, file_display_id, now_iso))
            conn.commit()

    def get_blob_by_path(self, blob_path: str) -> Optional[Dict[str, Any]]:
        """
        Get file entry by path.

        Args:
            blob_path: Blob storage path

        Returns:
            File entry dictionary or None if not found
        """
        query = "SELECT * FROM file_entries WHERE blob_path = ?"
        results = self.execute_query(query, (blob_path,))
        return results[0] if results else None

    def update_blob_status(
        self,
        blob_uuid: str,
        status: str,
        processing_stage: Optional[str] = None,
        error_message: Optional[str] = None,
        processing_stats: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Update file processing status.

        Args:
            blob_uuid: Blob UUID
            status: New status (staged, uploading, uploaded, processing,
                    preview_pending, finalized, error)
            processing_stage: Optional stage (ocr, extraction, validation, dedup, submission)
            error_message: Optional error message (for error status)
            processing_stats: Optional dict with extracted_invoice_count,
                            rejected_invoice_count, submitted_invoice_count, duplicate_count

        Returns:
            Number of rows affected (0 if blob not found)
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())

        # Build dynamic SET clause
        set_parts = ["status = ?", "processing_stage = ?", "updated_at = ?"]
        params: list = [status, processing_stage, now_iso]

        if error_message is not None:
            set_parts.append("error_message = ?")
            params.append(error_message)

        if status == "finalized":
            set_parts.extend(["finalized_at_unix = ?", "finalized_at_iso = ?"])
            params.extend([now_unix, now_iso])
        elif status == "processing":
            set_parts.extend(["processed_at_unix = ?", "processed_at_iso = ?"])
            params.extend([now_unix, now_iso])

        # Processing statistics (Core-populated)
        if processing_stats:
            for key in ("extracted_invoice_count", "rejected_invoice_count",
                        "submitted_invoice_count", "duplicate_count"):
                if key in processing_stats:
                    set_parts.append(f"{key} = ?")
                    params.append(processing_stats[key])

        params.append(blob_uuid)  # WHERE clause

        query = f"""
            UPDATE file_entries
            SET {', '.join(set_parts)}
            WHERE blob_uuid = ?
        """

        return self.execute_update(query, tuple(params))

    def record_download(
        self,
        blob_uuid: str,
        file_display_id: str,
        downloaded_by: str,
        download_source: str = "heartbeat_api",
        file_size_bytes: Optional[int] = None,
        download_duration_ms: Optional[int] = None,
        session_id: Optional[str] = None,
        float_id: Optional[str] = None,
    ) -> int:
        """
        Record a download in the blob_downloads audit table and
        increment download_count on file_entries.

        Args:
            blob_uuid: Blob UUID
            file_display_id: SDK display ID
            downloaded_by: helium_user_id who triggered the download
            download_source: heartbeat_api, heartbeat_mock, or local_cache
            file_size_bytes: Size of downloaded file
            download_duration_ms: How long the download took
            session_id: Session that triggered the download
            float_id: Float instance that downloaded

        Returns:
            Inserted row ID in blob_downloads
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        query = """
            INSERT INTO blob_downloads (
                blob_uuid, file_display_id, downloaded_by,
                downloaded_at, download_source,
                file_size_bytes, download_duration_ms,
                session_id, float_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            blob_uuid, file_display_id, downloaded_by,
            now_iso, download_source,
            file_size_bytes, download_duration_ms,
            session_id, float_id,
        )

        row_id = self.execute_insert(query, params)

        # Increment download_count on file_entries
        self.execute_update(
            "UPDATE file_entries SET download_count = download_count + 1 WHERE blob_uuid = ?",
            (blob_uuid,),
        )

        return row_id

    # ── Deduplication Operations ────────────────────────────────────────

    def check_dedup(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """
        Check if a file hash exists in deduplication table.

        Args:
            file_hash: SHA256 hex digest

        Returns:
            Dedup record dict or None if not a duplicate
        """
        query = """
            SELECT file_hash, source_system, original_blob_uuid,
                   original_filename, first_seen_iso,
                   duplicates_rejected_count
            FROM blob_deduplication
            WHERE file_hash = ?
            LIMIT 1
        """
        results = self.execute_query(query, (file_hash,))
        return results[0] if results else None

    def record_dedup(
        self,
        file_hash: str,
        source_system: str,
        original_blob_uuid: str,
        original_filename: Optional[str] = None,
    ) -> int:
        """
        Record a file hash in deduplication table.

        Args:
            file_hash: SHA256 hex digest
            source_system: Relay instance ID
            original_blob_uuid: UUID of the blob
            original_filename: Original filename

        Returns:
            Inserted row ID

        Raises:
            sqlite3.IntegrityError: If hash+source already exists
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())

        query = """
            INSERT INTO blob_deduplication (
                file_hash, source_system, original_blob_uuid,
                original_filename,
                first_seen_at_unix, first_seen_iso,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            file_hash, source_system, original_blob_uuid,
            original_filename,
            now_unix, now_iso,
            now_iso, now_iso,
        )
        return self.execute_insert(query, params)

    def increment_dedup_count(self, file_hash: str) -> int:
        """
        Increment the duplicate rejection count for a hash.

        Args:
            file_hash: SHA256 hex digest

        Returns:
            Number of rows affected
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())

        query = """
            UPDATE blob_deduplication
            SET duplicates_rejected_count = duplicates_rejected_count + 1,
                last_duplicate_at_unix = ?,
                last_duplicate_at_iso = ?,
                updated_at = ?
            WHERE file_hash = ?
        """
        return self.execute_update(query, (now_unix, now_iso, now_iso, file_hash))

    # ── Daily Usage Operations ──────────────────────────────────────────

    def get_daily_usage(
        self,
        company_id: str,
        usage_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get daily usage record for a company.

        Args:
            company_id: Company identifier
            usage_date: Date string (YYYY-MM-DD). Defaults to today.

        Returns:
            Usage record dict or None if no usage today
        """
        if usage_date is None:
            usage_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        query = """
            SELECT company_id, usage_date, file_count,
                   total_size_bytes, daily_limit
            FROM daily_usage
            WHERE company_id = ? AND usage_date = ?
        """
        results = self.execute_query(query, (company_id, usage_date))
        return results[0] if results else None

    def increment_daily_usage(
        self,
        company_id: str,
        file_count: int = 1,
        size_bytes: int = 0,
        daily_limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Increment daily usage for a company (upsert).

        Args:
            company_id: Company identifier
            file_count: Number of files to add
            size_bytes: Total size of files to add
            daily_limit: Daily file limit for this company

        Returns:
            Updated usage record dict
        """
        usage_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Upsert: INSERT OR UPDATE
            cursor.execute("""
                INSERT INTO daily_usage (
                    company_id, usage_date, file_count, total_size_bytes,
                    daily_limit, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, usage_date)
                DO UPDATE SET
                    file_count = file_count + ?,
                    total_size_bytes = total_size_bytes + ?,
                    updated_at = ?
            """, (
                company_id, usage_date, file_count, size_bytes,
                daily_limit, now_iso, now_iso,
                file_count, size_bytes, now_iso,
            ))
            conn.commit()

            # Return updated record
            cursor.execute("""
                SELECT company_id, usage_date, file_count,
                       total_size_bytes, daily_limit
                FROM daily_usage
                WHERE company_id = ? AND usage_date = ?
            """, (company_id, usage_date))

            row = cursor.fetchone()
            return dict(row) if row else {
                "company_id": company_id,
                "usage_date": usage_date,
                "file_count": file_count,
                "total_size_bytes": size_bytes,
                "daily_limit": daily_limit,
            }

    # ── Audit Operations ────────────────────────────────────────────────

    def log_audit_event(
        self,
        service: str,
        event_type: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> int:
        """
        Insert an immutable audit event.

        Args:
            service: Service name ("relay-api", "core", "heartbeat")
            event_type: Event type ("file.ingested", "batch.completed", etc.)
            user_id: Optional user identifier
            details: Optional event details dict (stored as JSON)
            trace_id: Optional trace ID for correlation
            ip_address: Optional source IP

        Returns:
            Inserted row ID
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())

        details_json = json.dumps(details) if details else None

        query = """
            INSERT INTO audit_events (
                service, event_type, user_id, details,
                trace_id, ip_address,
                created_at, created_at_unix
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            service, event_type, user_id, details_json,
            trace_id, ip_address,
            now_iso, now_unix,
        )
        return self.execute_insert(query, params)

    def query_audit_events(
        self,
        service: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Query audit events with optional filters.

        Args:
            service: Filter by service name
            event_type: Filter by event type
            limit: Maximum results (default 100)

        Returns:
            List of audit event dicts
        """
        conditions = []
        params = []

        if service:
            conditions.append("service = ?")
            params.append(service)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        query = f"""
            SELECT id, service, event_type, user_id, details,
                   trace_id, ip_address, created_at, created_at_unix
            FROM audit_events
            {where_clause}
            ORDER BY created_at_unix DESC
            LIMIT ?
        """
        results = self.execute_query(query, tuple(params))

        # Parse JSON details back to dict
        for row in results:
            if row.get("details") and isinstance(row["details"], str):
                try:
                    row["details"] = json.loads(row["details"])
                except json.JSONDecodeError:
                    pass

        return results

    # ── Metrics Operations ──────────────────────────────────────────────

    def log_metric(
        self,
        metric_type: str,
        values: Dict[str, Any],
        reported_by: Optional[str] = None,
    ) -> int:
        """
        Insert a metrics event.

        Args:
            metric_type: Metric category ("ingestion", "error", "submission", "performance")
            values: Metric values dict (stored as JSON)
            reported_by: Service that reported this metric

        Returns:
            Inserted row ID
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        now_unix = int(time.time())

        values_json = json.dumps(values)

        query = """
            INSERT INTO metrics_events (
                metric_type, metric_values, reported_by,
                created_at, created_at_unix
            ) VALUES (?, ?, ?, ?, ?)
        """
        params = (metric_type, values_json, reported_by, now_iso, now_unix)
        return self.execute_insert(query, params)


# Singleton instance
_db_instance: Optional[BlobDatabase] = None
_db_lock = Lock()


def get_blob_database(db_path: Optional[str] = None) -> BlobDatabase:
    """
    Get singleton BlobDatabase instance.

    Args:
        db_path: Database path (required on first call)

    Returns:
        BlobDatabase instance
    """
    global _db_instance

    with _db_lock:
        if _db_instance is None:
            if db_path is None:
                # Default path
                db_path = os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "databases",
                    "blob.db"
                )

            _db_instance = BlobDatabase(db_path)

        return _db_instance


def reset_blob_database() -> None:
    """Reset singleton instance (for testing)."""
    global _db_instance
    with _db_lock:
        _db_instance = None
