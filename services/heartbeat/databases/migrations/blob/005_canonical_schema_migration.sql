-- ============================================================================
-- MIGRATION 005: Canonical Blob Schema v1.4.0 alignment
-- Database: blob.db
-- Date: 2026-03-29
--
-- PURPOSE:
--   Migrate HeartBeat blob.db from Phase 1 schema (2026-01-31) to the
--   canonical blob schema v1.4.0 (2026-03-01+).
--
-- CHANGES:
--   1. Rename blob_entries → file_entries with canonical fields (48 cols)
--   2. Rebuild blob_batches with canonical fields (36 cols, display_id PK)
--   3. Rebuild blob_batch_entries with display_id FKs
--   4. Rebuild blob_outputs with FK to file_entries.blob_uuid
--   5. Add blob_downloads table (replaces blob_access_log)
--   6. Add blob_schema_version table
--   7. Add category views (operational/identity/metrics)
--   8. Drop replaced tables (blob_access_log, blob_cleanup_history)
--
-- IDENTITY MODEL:
--   file_display_id (SDK-generated, PK) + blob_uuid (HeartBeat-generated)
--   batch_display_id (SDK-generated, PK) + batch_uuid (HeartBeat-generated)
--   Legacy records get synthetic display_ids: HB-{blob_uuid}, HBB-{batch_uuid}
--
-- NOTE: SQLite does not support ALTER TABLE RENAME or ADD CONSTRAINT.
--   We use the standard SQLite migration pattern:
--   CREATE new → INSERT INTO ... SELECT → DROP old
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 1: Create canonical file_entries table
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS file_entries (
    -- A. Primary Identification
    file_display_id     TEXT PRIMARY KEY NOT NULL,
    blob_uuid           TEXT UNIQUE,
    blob_path           TEXT,
    original_filename   TEXT NOT NULL,
    batch_display_id    TEXT NOT NULL,

    -- B. Source & Ownership
    source              TEXT,
    source_type         TEXT,
    connection_type     TEXT,
    connection_id       TEXT,

    -- C. Local-only (SDK staging — HeartBeat stores for SSE sync)
    display_name        TEXT,
    local_path          TEXT,

    -- D. File Metadata
    content_type        TEXT,
    file_size_bytes     INTEGER NOT NULL DEFAULT 0,
    file_hash           TEXT,
    download_count      INTEGER NOT NULL DEFAULT 0,

    -- E. Processing State
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

    -- F. Processing Statistics (Core-populated, NULL at optimistic)
    extracted_invoice_count INTEGER,
    rejected_invoice_count  INTEGER,
    submitted_invoice_count INTEGER,
    duplicate_count         INTEGER,

    -- G. Timestamps (dual format)
    uploaded_at_unix    INTEGER,
    uploaded_at_iso     TEXT,
    processed_at_unix   INTEGER,
    processed_at_iso    TEXT,
    finalized_at_unix   INTEGER,
    finalized_at_iso    TEXT,

    -- H. Retention
    retention_until_unix INTEGER,
    retention_until_iso  TEXT,
    deleted_at_unix     INTEGER,
    deleted_at_iso      TEXT,

    -- I. Metadata Reference
    metadata_path       TEXT,
    has_processed_outputs BOOLEAN DEFAULT 0,

    -- J. Traceability & Identity (HELIUM_SECURITY_SPEC aligned)
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


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 2: Create canonical blob_batches table (new structure)
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS blob_batches_new (
    -- A. Primary Identification
    batch_display_id            TEXT PRIMARY KEY NOT NULL,
    batch_uuid                  TEXT UNIQUE,
    source_document_id          TEXT,

    -- B. Source & Ownership
    source                      TEXT NOT NULL,
    connection_type             TEXT,
    connection_id               TEXT,
    queue_mode                  TEXT NOT NULL DEFAULT 'bulk'
        CHECK(queue_mode IN ('bulk', 'api', 'polling', 'watcher', 'dbc', 'email')),

    -- C. Batch Content
    file_count                  INTEGER NOT NULL,
    original_filename_pattern   TEXT,
    total_size_bytes            INTEGER,

    -- D. Processing State
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

    -- E. Processing Statistics (Core-populated, NULL at optimistic)
    total_invoice_count         INTEGER,
    total_rejected_count        INTEGER,
    total_submitted_count       INTEGER,
    total_duplicate_count       INTEGER,

    -- F. Timestamps (dual format)
    uploaded_at_unix            INTEGER NOT NULL,
    uploaded_at_iso             TEXT NOT NULL,
    finalized_at_unix           INTEGER,
    finalized_at_iso            TEXT,

    -- G. Retention
    retention_until_unix        INTEGER,
    retention_until_iso         TEXT,
    deleted_at_unix             INTEGER,
    deleted_at_iso              TEXT,

    -- H. Traceability & Identity (HELIUM_SECURITY_SPEC aligned)
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


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 3: Migrate data from old tables → new tables
-- ────────────────────────────────────────────────────────────────────────────

-- 3a. Migrate blob_batches → blob_batches_new
--     Legacy records get synthetic batch_display_id = 'HBB-' || batch_uuid
INSERT OR IGNORE INTO blob_batches_new (
    batch_display_id, batch_uuid, source_document_id,
    source, connection_type, connection_id, queue_mode,
    file_count, original_filename_pattern, total_size_bytes,
    status, pending_sync, upload_status, processing_time_seconds,
    total_invoice_count, total_rejected_count, total_submitted_count, total_duplicate_count,
    uploaded_at_unix, uploaded_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    deleted_at_unix, deleted_at_iso,
    user_trace_id, x_trace_id, helium_user_id,
    float_id, session_id, machine_guid, mac_address, computer_name,
    created_at, updated_at
)
SELECT
    'HBB-' || batch_uuid,          -- synthetic batch_display_id
    batch_uuid,
    NULL,                           -- source_document_id (not in old schema)
    source,
    NULL,                           -- connection_type
    NULL,                           -- connection_id
    'bulk',                         -- default queue_mode
    file_count,
    original_filename_pattern,
    total_size_bytes,
    status,
    0,                              -- pending_sync = confirmed (legacy data is server-confirmed)
    status,                         -- upload_status mirrors status for legacy
    processing_time_seconds,
    NULL, NULL, NULL, NULL,         -- processing stats not tracked in legacy
    uploaded_at_unix, uploaded_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    deleted_at_unix, deleted_at_iso,
    user_trace_id, x_trace_id, helium_user_id,
    float_id, session_id, machine_guid, mac_address, computer_name,
    created_at, updated_at
FROM blob_batches;


-- 3b. Create a default batch for orphan files (files without a batch)
--     so file_entries FK to batch_display_id is always satisfied
INSERT OR IGNORE INTO blob_batches_new (
    batch_display_id, batch_uuid, source,
    file_count, status, pending_sync, upload_status,
    uploaded_at_unix, uploaded_at_iso,
    created_at, updated_at
) VALUES (
    'HBB-LEGACY-ORPHANS', NULL, 'legacy-migration',
    0, 'finalized', 0, 'finalized',
    CAST(strftime('%s', 'now') AS INTEGER),
    datetime('now'),
    datetime('now'), datetime('now')
);


-- 3c. Migrate blob_entries → file_entries
--     Legacy records get synthetic file_display_id = 'HB-' || blob_uuid
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, connection_type, connection_id,
    display_name, local_path,
    content_type, file_size_bytes, file_hash, download_count,
    status, pending_sync, processing_stage, error_message, retry_count,
    preflight_status, preflight_details, queue_mode,
    extracted_invoice_count, rejected_invoice_count, submitted_invoice_count, duplicate_count,
    uploaded_at_unix, uploaded_at_iso,
    processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    deleted_at_unix, deleted_at_iso,
    metadata_path, has_processed_outputs,
    user_trace_id, x_trace_id, helium_user_id,
    float_id, session_id, machine_guid, mac_address, computer_name,
    created_at, updated_at
)
SELECT
    'HB-' || blob_uuid,            -- synthetic file_display_id
    blob_uuid,
    blob_path,
    original_filename,
    COALESCE('HBB-' || batch_uuid, 'HBB-LEGACY-ORPHANS'),  -- map to new batch PK
    source,
    source_type,
    NULL,                           -- connection_type
    NULL,                           -- connection_id
    original_filename,              -- display_name defaults to filename
    NULL,                           -- local_path (not applicable server-side)
    content_type,
    file_size_bytes,
    file_hash,
    0,                              -- download_count
    status,
    0,                              -- pending_sync = confirmed (legacy)
    processing_stage,
    NULL,                           -- error_message
    0,                              -- retry_count
    NULL, NULL,                     -- preflight_status, preflight_details
    NULL,                           -- queue_mode
    NULL, NULL, NULL, NULL,         -- processing stats
    uploaded_at_unix, uploaded_at_iso,
    processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    deleted_at_unix, deleted_at_iso,
    metadata_path, has_processed_outputs,
    user_trace_id, x_trace_id, helium_user_id,
    float_id, session_id, machine_guid, mac_address, computer_name,
    created_at, updated_at
FROM blob_entries;


-- 3d. Update orphan batch file count
UPDATE blob_batches_new
SET file_count = (
    SELECT COUNT(*) FROM file_entries
    WHERE batch_display_id = 'HBB-LEGACY-ORPHANS'
)
WHERE batch_display_id = 'HBB-LEGACY-ORPHANS';


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 4: Rebuild blob_batch_entries with display_id FKs
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS blob_batch_entries_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_display_id    TEXT NOT NULL,
    file_display_id     TEXT NOT NULL,
    sequence_number     INTEGER,
    created_at          TEXT NOT NULL,

    FOREIGN KEY (batch_display_id) REFERENCES blob_batches_new(batch_display_id) ON DELETE CASCADE,
    FOREIGN KEY (file_display_id)  REFERENCES file_entries(file_display_id),
    UNIQUE(batch_display_id, file_display_id)
);

-- Migrate existing junction data
INSERT OR IGNORE INTO blob_batch_entries_new (
    batch_display_id, file_display_id, sequence_number, created_at
)
SELECT
    'HBB-' || bbe.batch_uuid,
    'HB-' || bbe.blob_uuid,
    bbe.sequence_number,
    bbe.created_at
FROM blob_batch_entries bbe;


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 5: Rebuild blob_outputs with FK to file_entries
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS blob_outputs_new (
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

-- Migrate existing outputs
INSERT OR IGNORE INTO blob_outputs_new (
    blob_uuid, output_type, object_path, content_type,
    size_bytes, file_hash,
    created_at_unix, created_at_iso, created_by_core_version,
    accessed_count, last_accessed_unix,
    created_at, updated_at
)
SELECT
    blob_uuid, output_type, object_path, content_type,
    size_bytes, file_hash,
    created_at_unix, created_at_iso, created_by_core_version,
    accessed_count, last_accessed_unix,
    created_at, updated_at
FROM blob_outputs;


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 6: Add blob_downloads table (replaces blob_access_log)
-- ────────────────────────────────────────────────────────────────────────────

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


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 7: Add blob_schema_version table
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS blob_schema_version (
    version         TEXT PRIMARY KEY NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description     TEXT
);

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.4.0', 'Canonical schema migration from Phase 1. Renamed blob_entries → file_entries, adopted batch_display_id/file_display_id PKs, added processing stats, blob_downloads, category views.');


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 8: Drop old tables and rename new ones
-- ────────────────────────────────────────────────────────────────────────────

-- Drop old junction and outputs (we have _new versions)
DROP TABLE IF EXISTS blob_batch_entries;
DROP TABLE IF EXISTS blob_outputs;

-- Drop old primary tables
DROP TABLE IF EXISTS blob_entries;
DROP TABLE IF EXISTS blob_batches;

-- Drop replaced tables
DROP TABLE IF EXISTS blob_access_log;
DROP TABLE IF EXISTS blob_cleanup_history;

-- Rename new tables to canonical names
ALTER TABLE blob_batches_new RENAME TO blob_batches;
ALTER TABLE blob_batch_entries_new RENAME TO blob_batch_entries;
ALTER TABLE blob_outputs_new RENAME TO blob_outputs;


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 9: Create indexes (canonical)
-- ────────────────────────────────────────────────────────────────────────────

-- blob_batches indexes
CREATE INDEX IF NOT EXISTS idx_blob_batches_status
    ON blob_batches(status);
CREATE INDEX IF NOT EXISTS idx_blob_batches_batch_uuid
    ON blob_batches(batch_uuid);
CREATE INDEX IF NOT EXISTS idx_blob_batches_pending_sync
    ON blob_batches(pending_sync);
CREATE INDEX IF NOT EXISTS idx_blob_batches_helium_user_id
    ON blob_batches(helium_user_id);
CREATE INDEX IF NOT EXISTS idx_blob_batches_float_id
    ON blob_batches(float_id);
CREATE INDEX IF NOT EXISTS idx_blob_batches_x_trace_id
    ON blob_batches(x_trace_id);

-- file_entries indexes
CREATE INDEX IF NOT EXISTS idx_file_entries_status
    ON file_entries(status);
CREATE INDEX IF NOT EXISTS idx_file_entries_batch_display_id
    ON file_entries(batch_display_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_created_at
    ON file_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_file_entries_source
    ON file_entries(source);
CREATE INDEX IF NOT EXISTS idx_file_entries_pending_sync
    ON file_entries(pending_sync);
CREATE INDEX IF NOT EXISTS idx_file_entries_helium_user_id
    ON file_entries(helium_user_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_machine_guid
    ON file_entries(machine_guid);
CREATE INDEX IF NOT EXISTS idx_file_entries_float_id
    ON file_entries(float_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_x_trace_id
    ON file_entries(x_trace_id);
CREATE INDEX IF NOT EXISTS idx_file_entries_blob_uuid
    ON file_entries(blob_uuid);
CREATE INDEX IF NOT EXISTS idx_file_entries_file_hash
    ON file_entries(file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_file_entries_retention
    ON file_entries(retention_until_unix) WHERE deleted_at_unix IS NULL;

-- blob_batch_entries indexes
CREATE INDEX IF NOT EXISTS idx_blob_batch_entries_batch
    ON blob_batch_entries(batch_display_id);

-- blob_outputs indexes
CREATE INDEX IF NOT EXISTS idx_blob_outputs_blob_uuid
    ON blob_outputs(blob_uuid);

-- blob_downloads indexes
CREATE INDEX IF NOT EXISTS idx_blob_downloads_blob_uuid
    ON blob_downloads(blob_uuid);
CREATE INDEX IF NOT EXISTS idx_blob_downloads_user
    ON blob_downloads(downloaded_by);


-- ────────────────────────────────────────────────────────────────────────────
-- STEP 10: Create category views (canonical v1.2.0+)
-- ────────────────────────────────────────────────────────────────────────────

-- Blob batches: operational
CREATE VIEW IF NOT EXISTS vw_blob_batches_operational AS
SELECT
    batch_display_id,
    source, connection_type, queue_mode,
    file_count, original_filename_pattern, total_size_bytes,
    status, upload_status,
    total_invoice_count, total_rejected_count,
    total_submitted_count, total_duplicate_count,
    uploaded_at_iso, finalized_at_iso,
    retention_until_iso, deleted_at_iso,
    created_at
FROM blob_batches
WHERE deleted_at_unix IS NULL;

-- Blob batches: identity
CREATE VIEW IF NOT EXISTS vw_blob_batches_identity AS
SELECT
    batch_display_id,
    helium_user_id, float_id,
    user_trace_id, x_trace_id, session_id,
    machine_guid, mac_address, computer_name,
    connection_id, created_at
FROM blob_batches;

-- Blob batches: metrics
CREATE VIEW IF NOT EXISTS vw_blob_batches_metrics AS
SELECT
    batch_display_id,
    processing_time_seconds,
    file_count, total_size_bytes,
    status, upload_status,
    created_at, updated_at
FROM blob_batches;

-- File entries: operational
CREATE VIEW IF NOT EXISTS vw_file_entries_operational AS
SELECT
    file_display_id, batch_display_id,
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
FROM file_entries
WHERE deleted_at_unix IS NULL;

-- File entries: identity
CREATE VIEW IF NOT EXISTS vw_file_entries_identity AS
SELECT
    file_display_id, batch_display_id,
    helium_user_id, float_id,
    user_trace_id, x_trace_id, session_id,
    machine_guid, mac_address, computer_name,
    connection_id, created_at
FROM file_entries;

-- File entries: metrics
CREATE VIEW IF NOT EXISTS vw_file_entries_metrics AS
SELECT
    file_display_id, batch_display_id,
    retry_count, processing_stage, status,
    file_size_bytes,
    created_at, updated_at
FROM file_entries;

-- Blob outputs: operational
CREATE VIEW IF NOT EXISTS vw_blob_outputs_operational AS
SELECT
    bo.id, bo.output_type, bo.content_type,
    bo.size_bytes, bo.created_at_iso, bo.created_at,
    fe.file_display_id, fe.original_filename, fe.batch_display_id
FROM blob_outputs bo
JOIN file_entries fe ON fe.blob_uuid = bo.blob_uuid;

-- Blob outputs: metrics
CREATE VIEW IF NOT EXISTS vw_blob_outputs_metrics AS
SELECT
    bo.id, bo.output_type,
    bo.accessed_count, bo.last_accessed_unix,
    bo.created_by_core_version,
    fe.file_display_id, fe.helium_user_id
FROM blob_outputs bo
JOIN file_entries fe ON fe.blob_uuid = bo.blob_uuid;

-- Aggregate metrics view
CREATE VIEW IF NOT EXISTS vw_blob_metrics AS
SELECT
    COUNT(*)                                                        AS total_files,
    COUNT(CASE WHEN fe.status = 'staged' THEN 1 END)               AS staged_count,
    COUNT(CASE WHEN fe.status = 'uploading' THEN 1 END)            AS uploading_count,
    COUNT(CASE WHEN fe.status = 'uploaded' THEN 1 END)             AS uploaded_count,
    COUNT(CASE WHEN fe.status = 'processing' THEN 1 END)           AS processing_count,
    COUNT(CASE WHEN fe.status = 'preview_pending' THEN 1 END)      AS preview_pending_count,
    COUNT(CASE WHEN fe.status = 'finalized' THEN 1 END)            AS finalized_count,
    COUNT(CASE WHEN fe.status = 'error' THEN 1 END)                AS error_count,
    COUNT(CASE WHEN fe.pending_sync = 1 THEN 1 END)                AS pending_sync_count,
    COUNT(CASE WHEN fe.pending_sync = 0 THEN 1 END)                AS confirmed_count,
    COALESCE(SUM(fe.file_size_bytes), 0)                            AS total_storage_bytes,
    COALESCE(SUM(CASE WHEN fe.status = 'finalized'
        THEN fe.file_size_bytes ELSE 0 END), 0)                    AS finalized_storage_bytes,
    COALESCE(SUM(fe.extracted_invoice_count), 0)                    AS total_invoices_extracted,
    COALESCE(SUM(fe.submitted_invoice_count), 0)                    AS total_invoices_submitted,
    COALESCE(SUM(fe.rejected_invoice_count), 0)                     AS total_invoices_rejected,
    COALESCE(SUM(fe.duplicate_count), 0)                            AS total_duplicates_found,
    (SELECT COUNT(*) FROM blob_batches)                             AS total_batches,
    (SELECT COUNT(*) FROM blob_batches WHERE status = 'finalized')  AS finalized_batches,
    (SELECT COUNT(*) FROM blob_batches WHERE status = 'error')      AS error_batches,
    (SELECT COUNT(*) FROM blob_outputs)                             AS total_outputs,
    (SELECT COALESCE(SUM(size_bytes), 0) FROM blob_outputs)         AS total_output_storage_bytes,
    COUNT(CASE WHEN fe.retention_until_unix IS NOT NULL THEN 1 END) AS retention_tracked_count,
    COUNT(CASE WHEN fe.deleted_at_unix IS NOT NULL THEN 1 END)      AS soft_deleted_count,
    COUNT(DISTINCT fe.float_id)                                     AS unique_float_instances,
    COUNT(DISTINCT fe.helium_user_id)                               AS unique_users,
    COUNT(DISTINCT fe.machine_guid)                                 AS unique_machines
FROM file_entries fe
WHERE fe.deleted_at_unix IS NULL;


-- ============================================================================
-- MIGRATION COMPLETE
-- HeartBeat blob.db is now aligned with canonical blob schema v1.4.0
-- ============================================================================
