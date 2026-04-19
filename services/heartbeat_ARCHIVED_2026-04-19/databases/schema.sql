-- ============================================================================
-- BLOB STORAGE DATABASE SCHEMA — Canonical v1.4.0
-- Database: helium_blob.db
-- Owner: HeartBeat Service
-- Version: 1.4.0
-- Date: 2026-03-29
-- Status: CANONICAL (aligned with Documentation/Schema/blob/)
--
-- This file is the single source of truth for NEW blob.db instances.
-- Existing databases are migrated via migrations/blob/005_canonical_schema_migration.sql.
--
-- Architecture:
--   Batch-centric model. blob_batches is the primary entity.
--   Dual identity: file_display_id (SDK PK) + blob_uuid (HeartBeat canonical).
--   Firebase-pattern optimistic updates: SDK writes locally, HeartBeat
--   overwrites via SSE when confirmed (pending_sync flag).
--   Filesystem storage for single-tenant deployments.
--
-- Tables (7 + 1 view + 1 version):
--   1. file_entries         — Child: one row per file (48+ fields)
--   2. blob_batches         — Primary: one row per upload action (36 fields)
--   3. blob_batch_entries   — Junction: batch ↔ file
--   4. blob_outputs         — Per-file processing outputs
--   5. blob_downloads       — Download audit trail
--   6. blob_deduplication   — Duplicate prevention
--   7. relay_services       — Relay instance reference
--   + daily_usage, audit_events, metrics_events, notifications
--   + blob_schema_version, vw_blob_metrics, category views
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- TABLE 1: relay_services (Reference Data)
-- ============================================================================
CREATE TABLE IF NOT EXISTS relay_services (
    instance_id TEXT PRIMARY KEY,
    relay_type TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL
);

-- ============================================================================
-- TABLE 2: blob_batches (PRIMARY — canonical v1.4.0)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_batches (
    batch_display_id            TEXT PRIMARY KEY NOT NULL,
    batch_uuid                  TEXT UNIQUE,
    source_document_id          TEXT,
    source                      TEXT NOT NULL,
    connection_type             TEXT,
    connection_id               TEXT,
    queue_mode                  TEXT NOT NULL DEFAULT 'bulk'
        CHECK(queue_mode IN ('bulk', 'api', 'polling', 'watcher', 'dbc', 'email')),
    file_count                  INTEGER NOT NULL,
    original_filename_pattern   TEXT,
    total_size_bytes            INTEGER,
    status                      TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN (
            'draft', 'uploading', 'queued', 'processing',
            'preview_pending', 'finalized', 'error'
        )),
    pending_sync                INTEGER NOT NULL DEFAULT 1,
    upload_status               TEXT NOT NULL DEFAULT 'draft'
        CHECK(upload_status IN (
            'draft', 'uploading', 'queued', 'processing',
            'preview_pending', 'finalized', 'error'
        )),
    processing_time_seconds     REAL,
    total_invoice_count         INTEGER,
    total_rejected_count        INTEGER,
    total_submitted_count       INTEGER,
    total_duplicate_count       INTEGER,
    uploaded_at_unix            INTEGER NOT NULL,
    uploaded_at_iso             TEXT NOT NULL,
    finalized_at_unix           INTEGER,
    finalized_at_iso            TEXT,
    retention_until_unix        INTEGER,
    retention_until_iso         TEXT,
    deleted_at_unix             INTEGER,
    deleted_at_iso              TEXT,
    user_trace_id               TEXT,
    x_trace_id                  TEXT,
    helium_user_id              TEXT,
    float_id                    TEXT,
    session_id                  TEXT,
    machine_guid                TEXT,
    mac_address                 TEXT,
    computer_name               TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

-- ============================================================================
-- TABLE 3: file_entries (CHILD — canonical v1.4.0)
-- ============================================================================
CREATE TABLE IF NOT EXISTS file_entries (
    file_display_id     TEXT PRIMARY KEY NOT NULL,
    blob_uuid           TEXT UNIQUE,
    blob_path           TEXT,
    original_filename   TEXT NOT NULL,
    batch_display_id    TEXT NOT NULL,
    source              TEXT,
    source_type         TEXT,
    connection_type     TEXT,
    connection_id       TEXT,
    display_name        TEXT,
    local_path          TEXT,
    content_type        TEXT,
    file_size_bytes     INTEGER NOT NULL DEFAULT 0,
    file_hash           TEXT,
    download_count      INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'staged'
        CHECK(status IN (
            'staged', 'uploading', 'uploaded',
            'processing', 'preview_pending',
            'finalized', 'error'
        )),
    pending_sync        INTEGER NOT NULL DEFAULT 1,
    processing_stage    TEXT,
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    preflight_status    TEXT
        CHECK(preflight_status IS NULL OR preflight_status IN ('passed', 'failed', 'skipped')),
    preflight_details   TEXT,
    queue_mode          TEXT
        CHECK(queue_mode IS NULL OR queue_mode IN (
            'bulk', 'api', 'polling', 'watcher', 'dbc', 'email'
        )),
    extracted_invoice_count INTEGER,
    rejected_invoice_count  INTEGER,
    submitted_invoice_count INTEGER,
    duplicate_count         INTEGER,
    uploaded_at_unix    INTEGER,
    uploaded_at_iso     TEXT,
    processed_at_unix   INTEGER,
    processed_at_iso    TEXT,
    finalized_at_unix   INTEGER,
    finalized_at_iso    TEXT,
    retention_until_unix INTEGER,
    retention_until_iso  TEXT,
    deleted_at_unix     INTEGER,
    deleted_at_iso      TEXT,
    metadata_path       TEXT,
    has_processed_outputs BOOLEAN DEFAULT 0,
    user_trace_id       TEXT,
    x_trace_id          TEXT,
    helium_user_id      TEXT,
    float_id            TEXT,
    session_id          TEXT,
    machine_guid        TEXT,
    mac_address         TEXT,
    computer_name       TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_display_id) REFERENCES blob_batches(batch_display_id)
) WITHOUT ROWID;

-- ============================================================================
-- TABLE 4: blob_batch_entries (JUNCTION)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_batch_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_display_id    TEXT NOT NULL,
    file_display_id     TEXT NOT NULL,
    sequence_number     INTEGER,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (batch_display_id) REFERENCES blob_batches(batch_display_id) ON DELETE CASCADE,
    FOREIGN KEY (file_display_id)  REFERENCES file_entries(file_display_id),
    UNIQUE(batch_display_id, file_display_id)
);

-- ============================================================================
-- TABLE 5: blob_outputs (Processing Outputs)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_outputs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    blob_uuid               TEXT NOT NULL,
    output_type             TEXT NOT NULL,
    object_path             TEXT NOT NULL,
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER,
    file_hash               TEXT,
    created_at_unix         INTEGER NOT NULL,
    created_at_iso          TEXT NOT NULL,
    created_by_core_version TEXT,
    accessed_count          INTEGER DEFAULT 0,
    last_accessed_unix      INTEGER,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (blob_uuid) REFERENCES file_entries(blob_uuid) ON DELETE CASCADE,
    UNIQUE(blob_uuid, output_type)
);

-- ============================================================================
-- TABLE 6: blob_downloads (Download Audit Trail — canonical v1.3.0+)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_downloads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    blob_uuid           TEXT NOT NULL,
    file_display_id     TEXT NOT NULL,
    downloaded_by       TEXT NOT NULL,
    downloaded_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    download_source     TEXT NOT NULL
        CHECK(download_source IN ('heartbeat_api', 'heartbeat_mock', 'local_cache')),
    file_size_bytes     INTEGER,
    download_duration_ms INTEGER,
    session_id          TEXT,
    float_id            TEXT
);

-- ============================================================================
-- TABLE 7: blob_deduplication (Duplicate Prevention)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_deduplication (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL,
    source_system TEXT NOT NULL,
    original_blob_uuid TEXT NOT NULL,
    original_filename TEXT,
    first_seen_at_unix INTEGER NOT NULL,
    first_seen_iso TEXT NOT NULL,
    duplicates_rejected_count INTEGER DEFAULT 0,
    last_duplicate_at_unix INTEGER,
    last_duplicate_at_iso TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(file_hash, source_system)
);

-- ============================================================================
-- TABLE 8: notifications (Reconciliation Alerts)
-- ============================================================================
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    blob_uuid TEXT,
    blob_path TEXT,
    message TEXT NOT NULL,
    details TEXT,
    is_resolved BOOLEAN DEFAULT 0,
    resolved_at_unix INTEGER,
    resolved_at_iso TEXT,
    created_at_unix INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by_service TEXT
);

-- ============================================================================
-- TABLE 9: daily_usage (Per-Company Daily Upload Tracking)
-- ============================================================================
CREATE TABLE IF NOT EXISTS daily_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    file_count INTEGER DEFAULT 0,
    total_size_bytes INTEGER DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 1000,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (company_id, usage_date)
);

-- ============================================================================
-- TABLE 10: audit_events (Immutable Audit Trail)
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    event_type TEXT NOT NULL,
    user_id TEXT,
    details TEXT,
    trace_id TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL,
    created_at_unix INTEGER NOT NULL
);

-- ============================================================================
-- TABLE 11: metrics_events (Operational Metrics)
-- ============================================================================
CREATE TABLE IF NOT EXISTS metrics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL,
    metric_values TEXT NOT NULL,
    reported_by TEXT,
    created_at TEXT NOT NULL,
    created_at_unix INTEGER NOT NULL
);

-- ============================================================================
-- TABLE 12: event_ledger (SSE Replay — SSE Spec Section 4)
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_ledger (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    data_json   TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    data_uuid   TEXT,
    company_id  TEXT    NOT NULL
);

-- ============================================================================
-- TABLE 13: blob_schema_version (Version Tracking)
-- ============================================================================
CREATE TABLE IF NOT EXISTS blob_schema_version (
    version         TEXT PRIMARY KEY NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description     TEXT
);

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.5.0', 'Add event_ledger for SSE replay, catchup, and reconciliation (SSE Spec v1.1).');

-- ============================================================================
-- INDEXES
-- ============================================================================

-- blob_batches
CREATE INDEX IF NOT EXISTS idx_blob_batches_status ON blob_batches(status);
CREATE INDEX IF NOT EXISTS idx_blob_batches_batch_uuid ON blob_batches(batch_uuid);
CREATE INDEX IF NOT EXISTS idx_blob_batches_pending_sync ON blob_batches(pending_sync);
CREATE INDEX IF NOT EXISTS idx_blob_batches_helium_user_id ON blob_batches(helium_user_id);
CREATE INDEX IF NOT EXISTS idx_blob_batches_float_id ON blob_batches(float_id);
CREATE INDEX IF NOT EXISTS idx_blob_batches_x_trace_id ON blob_batches(x_trace_id);

-- file_entries
CREATE INDEX IF NOT EXISTS idx_file_entries_status ON file_entries(status);
CREATE INDEX IF NOT EXISTS idx_file_entries_batch_display_id ON file_entries(batch_display_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_created_at ON file_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_file_entries_source ON file_entries(source);
CREATE INDEX IF NOT EXISTS idx_file_entries_pending_sync ON file_entries(pending_sync);
CREATE INDEX IF NOT EXISTS idx_file_entries_helium_user_id ON file_entries(helium_user_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_machine_guid ON file_entries(machine_guid);
CREATE INDEX IF NOT EXISTS idx_file_entries_float_id ON file_entries(float_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_x_trace_id ON file_entries(x_trace_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_blob_uuid ON file_entries(blob_uuid);
CREATE INDEX IF NOT EXISTS idx_file_entries_file_hash ON file_entries(file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_entries_retention ON file_entries(retention_until_unix) WHERE deleted_at_unix IS NULL;

-- blob_batch_entries
CREATE INDEX IF NOT EXISTS idx_blob_batch_entries_batch ON blob_batch_entries(batch_display_id);

-- blob_outputs
CREATE INDEX IF NOT EXISTS idx_blob_outputs_blob_uuid ON blob_outputs(blob_uuid);

-- blob_downloads
CREATE INDEX IF NOT EXISTS idx_blob_downloads_blob_uuid ON blob_downloads(blob_uuid);
CREATE INDEX IF NOT EXISTS idx_blob_downloads_user ON blob_downloads(downloaded_by);

-- blob_deduplication
CREATE INDEX IF NOT EXISTS idx_blob_dedup_hash ON blob_deduplication(file_hash);
CREATE INDEX IF NOT EXISTS idx_blob_dedup_source ON blob_deduplication(source_system);

-- notifications
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(notification_type);
CREATE INDEX IF NOT EXISTS idx_notifications_resolved ON notifications(is_resolved, created_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_blob ON notifications(blob_uuid);

-- daily_usage
CREATE INDEX IF NOT EXISTS idx_daily_usage_company_date ON daily_usage(company_id, usage_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_usage_date ON daily_usage(usage_date DESC);

-- audit_events
CREATE INDEX IF NOT EXISTS idx_audit_events_service ON audit_events(service, event_type);
CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events(event_type, created_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_user ON audit_events(user_id, created_at_unix DESC) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_events_trace ON audit_events(trace_id) WHERE trace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events(created_at_unix DESC);

-- metrics_events
CREATE INDEX IF NOT EXISTS idx_metrics_events_type ON metrics_events(metric_type, created_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_events_time ON metrics_events(created_at_unix DESC);

-- event_ledger
CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON event_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_ledger_data_uuid ON event_ledger(data_uuid) WHERE data_uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ledger_company ON event_ledger(company_id);

-- ============================================================================
-- VIEWS (Canonical category views)
-- ============================================================================

CREATE VIEW IF NOT EXISTS vw_blob_batches_operational AS
SELECT batch_display_id, source, connection_type, queue_mode,
    file_count, original_filename_pattern, total_size_bytes,
    status, upload_status,
    total_invoice_count, total_rejected_count,
    total_submitted_count, total_duplicate_count,
    uploaded_at_iso, finalized_at_iso,
    retention_until_iso, deleted_at_iso, created_at
FROM blob_batches WHERE deleted_at_unix IS NULL;

CREATE VIEW IF NOT EXISTS vw_blob_batches_identity AS
SELECT batch_display_id, helium_user_id, float_id,
    user_trace_id, x_trace_id, session_id,
    machine_guid, mac_address, computer_name,
    connection_id, created_at
FROM blob_batches;

CREATE VIEW IF NOT EXISTS vw_file_entries_operational AS
SELECT file_display_id, batch_display_id,
    original_filename, display_name,
    source, source_type, connection_type,
    content_type, file_size_bytes, download_count,
    status, processing_stage, error_message,
    preflight_status, preflight_details, queue_mode,
    extracted_invoice_count, rejected_invoice_count,
    submitted_invoice_count, duplicate_count,
    uploaded_at_iso, processed_at_iso,
    finalized_at_iso, retention_until_iso, deleted_at_iso,
    has_processed_outputs, created_at
FROM file_entries WHERE deleted_at_unix IS NULL;

CREATE VIEW IF NOT EXISTS vw_file_entries_identity AS
SELECT file_display_id, batch_display_id,
    helium_user_id, float_id,
    user_trace_id, x_trace_id, session_id,
    machine_guid, mac_address, computer_name,
    connection_id, created_at
FROM file_entries;

CREATE VIEW IF NOT EXISTS vw_blob_metrics AS
SELECT
    COUNT(*) AS total_files,
    COUNT(CASE WHEN fe.status = 'staged' THEN 1 END) AS staged_count,
    COUNT(CASE WHEN fe.status = 'uploading' THEN 1 END) AS uploading_count,
    COUNT(CASE WHEN fe.status = 'uploaded' THEN 1 END) AS uploaded_count,
    COUNT(CASE WHEN fe.status = 'processing' THEN 1 END) AS processing_count,
    COUNT(CASE WHEN fe.status = 'preview_pending' THEN 1 END) AS preview_pending_count,
    COUNT(CASE WHEN fe.status = 'finalized' THEN 1 END) AS finalized_count,
    COUNT(CASE WHEN fe.status = 'error' THEN 1 END) AS error_count,
    COUNT(CASE WHEN fe.pending_sync = 1 THEN 1 END) AS pending_sync_count,
    COUNT(CASE WHEN fe.pending_sync = 0 THEN 1 END) AS confirmed_count,
    COALESCE(SUM(fe.file_size_bytes), 0) AS total_storage_bytes,
    COALESCE(SUM(fe.extracted_invoice_count), 0) AS total_invoices_extracted,
    COALESCE(SUM(fe.submitted_invoice_count), 0) AS total_invoices_submitted,
    COALESCE(SUM(fe.rejected_invoice_count), 0) AS total_invoices_rejected,
    (SELECT COUNT(*) FROM blob_batches) AS total_batches,
    (SELECT COUNT(*) FROM blob_outputs) AS total_outputs,
    COUNT(DISTINCT fe.float_id) AS unique_float_instances,
    COUNT(DISTINCT fe.helium_user_id) AS unique_users,
    COUNT(DISTINCT fe.machine_guid) AS unique_machines
FROM file_entries fe WHERE fe.deleted_at_unix IS NULL;

COMMIT;

-- ============================================================================
-- SCHEMA CREATION COMPLETE — Canonical v1.4.0
-- 12 tables + 5 views + blob_schema_version
-- ============================================================================
