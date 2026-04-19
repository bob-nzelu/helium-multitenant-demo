-- ============================================================================
-- Helium Canonical Blob Schema v1.1.0
-- ============================================================================
-- Date:    2026-03-01
-- Status:  Approved
-- Scope:   Canonical blob/file schema for HeartBeat, Core, Float SDK sync
--
-- This file is the SINGLE SOURCE OF TRUTH for blob table definitions.
-- All service implementations MUST derive from this file.
--
-- Architecture:
--   Batch-centric model. blob_batches is the primary entity.
--   Core processes batches as atomic units.
--   Firebase-pattern optimistic updates: SDK writes locally, HeartBeat
--   overwrites via SSE when confirmed (pending_sync flag).
--   Filesystem storage (NOT MinIO) for single-tenant deployments.
--   MinIO available on-demand for Enterprise SaaS tier only.
--
-- Security Spec Alignment (HELIUM_SECURITY_SPEC.docx, 2026-03-01):
--   3-level trace:  user_trace_id (SDK) → x_trace_id (Relay) → invoice_trace_id (Core)
--   Machine ID:     Composite fingerprint — machine_guid + mac_address + computer_name
--   Identity:       helium_user_id (HeartBeat-assigned), float_id, session_id
--   Auth:           JWT-based (HeartBeat sole issuer), SSE session liveness (hb_ack)
--
-- Tables:
--   1. blob_batches        — Primary: one row per upload action (36 fields)
--   2. file_entries         — Child: one row per file (48 fields)
--   3. blob_batch_entries   — Junction: batch ↔ file (5 fields)
--   4. blob_outputs         — Per-file processing outputs (14 fields)
--   5. uploads              — SDK local-first session staging (14 fields)
--   6. upload_files         — SDK local junction: upload ↔ file (6 fields)
--   7. vw_blob_metrics      — Computed metrics view
--   8. blob_downloads       — Download audit trail (10 fields)
--   9. blob_schema_version  — Schema version tracking (3 fields)
--
-- Total: 139 fields across 7 tables + 1 view + 1 version table
--
-- Change log:
--   v1.0.0 (2026-02-28) — Initial canonical schema.
--     Batch-centric model, dual identity (display_id + uuid),
--     Firebase optimistic pattern, processing statistics.
--   v1.1.0 (2026-03-01) — Security spec alignment.
--     3-field machine composite (machine_guid, mac_address, computer_name).
--     3-level trace model (user_trace_id, x_trace_id).
--     Identity fields (helium_user_id, float_id, session_id).
--     MinIO deprecated for single-tenant; filesystem storage canonical.
-- ============================================================================


-- ============================================================================
-- TABLE 1: blob_batches (PRIMARY — 36 fields)
-- ============================================================================
-- Primary entity in the batch-centric model. One row per upload action.
-- Core processes batches as atomic units — even single-file uploads create
-- a batch. ZIPs are extracted; file_count reflects extracted count.
--
-- Dual identity:
--   batch_display_id — PK, source-prefixed, SDK-generated at staging.
--   batch_uuid       — HeartBeat-generated, NULL until SSE confirms.
--
-- Status flow:
--   draft → uploading → queued → processing → preview_pending → finalized
--              ↓                     ↓
--            error ←──────────── error
-- ============================================================================

CREATE TABLE IF NOT EXISTS blob_batches (
    -- A. Primary Identification
    batch_display_id            TEXT PRIMARY KEY NOT NULL,
    batch_uuid                  TEXT UNIQUE,        -- HeartBeat-generated, NULL until confirmed
    source_document_id          TEXT,               -- Relay-generated tracking ID

    -- B. Source & Ownership
    source                      TEXT NOT NULL,       -- upload_manager, sse_sync, manual_import
    connection_type             TEXT,               -- manual, nas, erp, api, email
    connection_id               TEXT,               -- Specific connection instance
    queue_mode                  TEXT NOT NULL DEFAULT 'bulk'
        CHECK(queue_mode IN ('bulk', 'api', 'polling', 'watcher', 'dbc', 'email')),

    -- C. Batch Content
    file_count                  INTEGER NOT NULL,
    original_filename_pattern   TEXT,               -- e.g. INV-2026-*.pdf or jan_invoices.zip
    total_size_bytes            INTEGER,

    -- D. Processing State
    status                      TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN (
            'draft', 'uploading', 'queued', 'processing',
            'preview_pending', 'finalized', 'error'
        )),
    pending_sync                INTEGER NOT NULL DEFAULT 1,  -- 1=optimistic, 0=confirmed
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
    user_trace_id               TEXT,               -- SDK-generated UUID v7 from USER-TRACE block (client origin)
    x_trace_id                  TEXT,               -- Relay-generated UUID v7 (server authority, propagates downstream)
    helium_user_id              TEXT,               -- HeartBeat-assigned user ID (immutable, from JWT claim)
    float_id                    TEXT,               -- HeartBeat-assigned Float instance ID (from JWT, tied to machine at registration)
    session_id                  TEXT,               -- Session UUID active at upload time (resets at hard re-auth)
    machine_guid                TEXT,               -- Windows MachineGuid / Linux machine-id (primary machine anchor)
    mac_address                 TEXT,               -- Primary NIC MAC address (corroborating signal)
    computer_name               TEXT,               -- OS hostname (human-readable label only)
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);


-- ============================================================================
-- TABLE 2: file_entries (CHILD — 48 fields)
-- ============================================================================
-- One row per uploaded/synced file. Child of blob_batches.
-- Dual-purpose: SDK optimistic writes + HeartBeat confirmed state.
--
-- Dual identity:
--   file_display_id — PK, source-prefixed, SDK-generated.
--   blob_uuid       — HeartBeat-generated, NULL until SSE confirms.
--
-- Status flow:
--   staged → uploading → uploaded → processing → preview_pending → finalized
--               ↓                       ↓
--             error ←─────────────── error
--               ↓ (retry)
--             staged
-- ============================================================================

CREATE TABLE IF NOT EXISTS file_entries (
    -- A. Primary Identification
    file_display_id     TEXT PRIMARY KEY NOT NULL,
    blob_uuid           TEXT UNIQUE,            -- HeartBeat-generated, NULL until confirmed
    blob_path           TEXT,                   -- HeartBeat filesystem storage path (/files_blob/{uuid}-filename)
    original_filename   TEXT NOT NULL,
    batch_display_id    TEXT NOT NULL,           -- FK → blob_batches

    -- B. Source & Ownership
    source              TEXT,                   -- upload_manager, sse_sync, manual_import
    source_type         TEXT,                   -- upload, sync, import
    -- NOTE: scope (LOCAL/EXTERNAL) is SDK-local only — see schema.py.
    -- HeartBeat treats all blobs identically regardless of upload origin.
    connection_type     TEXT,                   -- manual, nas, erp, api, email
    connection_id       TEXT,

    -- C. Local-only (SDK staging)
    display_name        TEXT,                   -- Human-readable SWDB label
    local_path          TEXT,                   -- Absolute path to local cached copy

    -- D. File Metadata
    content_type        TEXT,                   -- MIME type
    file_size_bytes     INTEGER NOT NULL DEFAULT 0,
    file_hash           TEXT,                   -- SHA-256
    download_count      INTEGER NOT NULL DEFAULT 0,  -- Incremented on HeartBeat/mock fetch (not local cache hits)

    -- E. Processing State
    status              TEXT NOT NULL DEFAULT 'staged'
        CHECK(status IN (
            'staged', 'uploading', 'uploaded',
            'processing', 'preview_pending',
            'finalized', 'error'
        )),
    pending_sync        INTEGER NOT NULL DEFAULT 1,  -- 1=optimistic, 0=confirmed
    processing_stage    TEXT,                   -- ocr, extraction, validation, dedup, submission
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    preflight_status    TEXT                    -- passed, failed, skipped
        CHECK(preflight_status IS NULL OR preflight_status IN ('passed', 'failed', 'skipped')),
    preflight_details   TEXT,                   -- JSON: {file_type: ok, dedup: ok, virus: ok, ...}
    queue_mode          TEXT                    -- Denormalized from uploads.queue_mode
        CHECK(queue_mode IS NULL OR queue_mode IN (
            'bulk', 'api', 'polling', 'watcher', 'dbc', 'email'
        )),

    -- F. Processing Statistics (Core-populated, NULL at optimistic)
    extracted_invoice_count INTEGER,
    rejected_invoice_count  INTEGER,
    submitted_invoice_count INTEGER,
    duplicate_count         INTEGER,            -- IRN duplicates found

    -- G. Timestamps (dual format)
    uploaded_at_unix    INTEGER,
    uploaded_at_iso     TEXT,
    processed_at_unix   INTEGER,
    processed_at_iso    TEXT,
    finalized_at_unix   INTEGER,
    finalized_at_iso    TEXT,

    -- H. Retention (invoice-linked files only)
    retention_until_unix INTEGER,
    retention_until_iso  TEXT,
    deleted_at_unix     INTEGER,
    deleted_at_iso      TEXT,

    -- I. Metadata Reference
    metadata_path       TEXT,                   -- Sidecar JSON path
    has_processed_outputs BOOLEAN DEFAULT 0,

    -- J. Traceability & Identity (HELIUM_SECURITY_SPEC aligned)
    user_trace_id       TEXT,                   -- SDK-generated UUID v7 from USER-TRACE block (client origin)
    x_trace_id          TEXT,                   -- Relay-generated UUID v7 (server authority)
    helium_user_id      TEXT,                   -- HeartBeat-assigned user ID (immutable, from JWT)
    float_id            TEXT,                   -- HeartBeat-assigned Float instance ID (from JWT)
    session_id          TEXT,                   -- Session UUID active at upload time
    machine_guid        TEXT,                   -- Windows MachineGuid / Linux machine-id (primary anchor)
    mac_address         TEXT,                   -- Primary NIC MAC (corroborating signal)
    computer_name       TEXT,                   -- OS hostname (label only)
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (batch_display_id) REFERENCES blob_batches(batch_display_id)
) WITHOUT ROWID;


-- ============================================================================
-- TABLE 3: blob_batch_entries (JUNCTION — 5 fields)
-- ============================================================================
-- Maps files to batches. Each file belongs to one batch.
-- ============================================================================

CREATE TABLE IF NOT EXISTS blob_batch_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_display_id    TEXT NOT NULL,
    file_display_id     TEXT NOT NULL,
    sequence_number     INTEGER,                -- Position within batch (0-indexed)
    created_at          TEXT NOT NULL,

    FOREIGN KEY (batch_display_id) REFERENCES blob_batches(batch_display_id) ON DELETE CASCADE,
    FOREIGN KEY (file_display_id)  REFERENCES file_entries(file_display_id),
    UNIQUE(batch_display_id, file_display_id)
);


-- ============================================================================
-- TABLE 4: blob_outputs (14 fields)
-- ============================================================================
-- Processed outputs from Core. One file → multiple outputs.
-- Linked via blob_uuid (HeartBeat canonical), NOT file_display_id.
-- Outputs only exist after HeartBeat/Core processing.
-- ============================================================================

CREATE TABLE IF NOT EXISTS blob_outputs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    blob_uuid               TEXT NOT NULL,       -- FK → file_entries.blob_uuid
    output_type             TEXT NOT NULL,
    object_path             TEXT NOT NULL,
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER,
    file_hash               TEXT,                -- SHA-256 of output
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
-- TABLE 5: uploads (SDK LOCAL-ONLY — 14 fields)
-- ============================================================================
-- UploadManager session metadata. NOT synced from HeartBeat.
-- Per-file state lives in file_entries, NOT here.
-- upload_status is a DENORMALIZED CACHE of aggregate file statuses.
-- Reconciled on app startup and every 5 minutes.
-- ============================================================================

CREATE TABLE IF NOT EXISTS uploads (
    upload_id           TEXT PRIMARY KEY NOT NULL,
    batch_display_id    TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    queue_mode          TEXT NOT NULL DEFAULT 'bulk'
        CHECK(queue_mode IN ('bulk', 'api', 'polling', 'watcher', 'dbc', 'email')),
    upload_status       TEXT NOT NULL DEFAULT 'draft'
        CHECK(upload_status IN (
            'draft', 'uploading', 'queued', 'processing',
            'preview_pending', 'finalized', 'error'
        )),
    file_count          INTEGER NOT NULL DEFAULT 0,
    total_size_bytes    INTEGER NOT NULL DEFAULT 0,
    file_location       TEXT NOT NULL DEFAULT 'Not Applicable',
    relay_file_uuid     TEXT,
    relay_queue_id      TEXT,
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (batch_display_id) REFERENCES blob_batches(batch_display_id)
);


-- ============================================================================
-- TABLE 6: upload_files (SDK LOCAL-ONLY JUNCTION — 6 fields)
-- ============================================================================
-- Links file_entries to uploads. Per-file metadata lives in file_entries.
-- ============================================================================

CREATE TABLE IF NOT EXISTS upload_files (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id           TEXT NOT NULL,
    file_display_id     TEXT NOT NULL,
    original_filename   TEXT NOT NULL,
    sequence_number     INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (upload_id) REFERENCES uploads(upload_id) ON DELETE CASCADE,
    FOREIGN KEY (file_display_id) REFERENCES file_entries(file_display_id)
);


-- ============================================================================
-- INDEXES
-- ============================================================================

-- blob_batches
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

-- file_entries
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

-- blob_batch_entries
CREATE INDEX IF NOT EXISTS idx_blob_batch_entries_batch
    ON blob_batch_entries(batch_display_id);

-- blob_outputs
CREATE INDEX IF NOT EXISTS idx_blob_outputs_blob_uuid
    ON blob_outputs(blob_uuid);

-- uploads
CREATE INDEX IF NOT EXISTS idx_uploads_status
    ON uploads(upload_status);
CREATE INDEX IF NOT EXISTS idx_uploads_created_at
    ON uploads(created_at);
CREATE INDEX IF NOT EXISTS idx_uploads_batch_display_id
    ON uploads(batch_display_id);

-- upload_files
CREATE INDEX IF NOT EXISTS idx_upload_files_upload_id
    ON upload_files(upload_id);


-- ============================================================================
-- VIEW: vw_blob_metrics
-- ============================================================================
-- Computed metrics view. Replicates vw_invoice_metrics pattern.
-- Single query for all blob health metrics.
-- ============================================================================

CREATE VIEW IF NOT EXISTS vw_blob_metrics AS
SELECT
    -- File counts by status
    COUNT(*)                                                        AS total_files,
    COUNT(CASE WHEN fe.status = 'staged' THEN 1 END)               AS staged_count,
    COUNT(CASE WHEN fe.status = 'uploading' THEN 1 END)            AS uploading_count,
    COUNT(CASE WHEN fe.status = 'uploaded' THEN 1 END)             AS uploaded_count,
    COUNT(CASE WHEN fe.status = 'processing' THEN 1 END)           AS processing_count,
    COUNT(CASE WHEN fe.status = 'preview_pending' THEN 1 END)      AS preview_pending_count,
    COUNT(CASE WHEN fe.status = 'finalized' THEN 1 END)            AS finalized_count,
    COUNT(CASE WHEN fe.status = 'error' THEN 1 END)                AS error_count,

    -- Sync status
    COUNT(CASE WHEN fe.pending_sync = 1 THEN 1 END)                AS pending_sync_count,
    COUNT(CASE WHEN fe.pending_sync = 0 THEN 1 END)                AS confirmed_count,

    -- Storage
    COALESCE(SUM(fe.file_size_bytes), 0)                            AS total_storage_bytes,
    COALESCE(SUM(CASE WHEN fe.status = 'finalized'
        THEN fe.file_size_bytes ELSE 0 END), 0)                    AS finalized_storage_bytes,

    -- Processing statistics
    COALESCE(SUM(fe.extracted_invoice_count), 0)                    AS total_invoices_extracted,
    COALESCE(SUM(fe.submitted_invoice_count), 0)                    AS total_invoices_submitted,
    COALESCE(SUM(fe.rejected_invoice_count), 0)                     AS total_invoices_rejected,
    COALESCE(SUM(fe.duplicate_count), 0)                            AS total_duplicates_found,

    -- Batch counts
    (SELECT COUNT(*) FROM blob_batches)                             AS total_batches,
    (SELECT COUNT(*) FROM blob_batches WHERE status = 'finalized')  AS finalized_batches,
    (SELECT COUNT(*) FROM blob_batches WHERE status = 'error')      AS error_batches,

    -- Output counts
    (SELECT COUNT(*) FROM blob_outputs)                             AS total_outputs,
    (SELECT COALESCE(SUM(size_bytes), 0) FROM blob_outputs)         AS total_output_storage_bytes,

    -- Retention
    COUNT(CASE WHEN fe.retention_until_unix IS NOT NULL THEN 1 END) AS retention_tracked_count,
    COUNT(CASE WHEN fe.deleted_at_unix IS NOT NULL THEN 1 END)      AS soft_deleted_count,

    -- Local storage (Float App disk usage)
    COUNT(CASE WHEN fe.local_path IS NOT NULL THEN 1 END)           AS local_files_count,
    COALESCE(SUM(CASE WHEN fe.local_path IS NOT NULL
        THEN fe.file_size_bytes ELSE 0 END), 0)                    AS local_storage_bytes,
    COUNT(CASE WHEN fe.local_path IS NOT NULL
        AND fe.pending_sync = 1 THEN 1 END)                        AS local_pending_count,
    COUNT(CASE WHEN fe.local_path IS NOT NULL
        AND fe.pending_sync = 0 THEN 1 END)                        AS local_confirmed_count,

    -- Identity metrics
    COUNT(DISTINCT fe.float_id)                                     AS unique_float_instances,
    COUNT(DISTINCT fe.helium_user_id)                               AS unique_users,
    COUNT(DISTINCT fe.machine_guid)                                 AS unique_machines,

    -- Upload sessions
    (SELECT COUNT(*) FROM uploads)                                  AS total_upload_sessions,
    (SELECT COALESCE(SUM(total_size_bytes), 0) FROM uploads)        AS total_upload_session_bytes,
    (SELECT COUNT(*) FROM uploads WHERE upload_status = 'error')    AS failed_upload_sessions

FROM file_entries fe
WHERE fe.deleted_at_unix IS NULL;


-- ============================================================================
-- CATEGORY VIEWS (v1.2.0 — permission-ready field grouping)
--
-- Views organized by DATA CATEGORY, not by role. The permission system
-- (Owner/Admin/Operator/Support) will layer access control dynamically
-- by granting SELECT on specific category views per role.
--
-- Categories:
--   operational  — user-facing business data (status, filenames, counts, ISO timestamps)
--   identity     — security/audit trace fields (user IDs, machine fingerprint, traces)
--   metrics      — performance analytics (processing time, access counts)
--   (system-internal fields appear in NO view: blob_uuid, blob_path, local_path,
--    object_path, metadata_path, pending_sync, file_hash, raw unix timestamps)
-- ============================================================================


-- ── blob_batches: operational ──────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_blob_batches_operational AS
SELECT
    batch_display_id,
    -- Source & ownership
    source,
    connection_type,
    queue_mode,
    -- Batch content
    file_count,
    original_filename_pattern,
    total_size_bytes,
    -- Processing state
    status,
    upload_status,
    -- Processing statistics (Core-populated)
    total_invoice_count,
    total_rejected_count,
    total_submitted_count,
    total_duplicate_count,
    -- ISO timestamps (no raw unix)
    uploaded_at_iso,
    finalized_at_iso,
    retention_until_iso,
    deleted_at_iso,
    -- Audit
    created_at
FROM blob_batches
WHERE deleted_at_unix IS NULL;


-- ── blob_batches: identity ─────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_blob_batches_identity AS
SELECT
    batch_display_id,
    -- User identity
    helium_user_id,
    float_id,
    user_trace_id,
    x_trace_id,
    session_id,
    -- Machine fingerprint
    machine_guid,
    mac_address,
    computer_name,
    -- Connection
    connection_id,
    -- Audit
    created_at
FROM blob_batches;


-- ── blob_batches: metrics ──────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_blob_batches_metrics AS
SELECT
    batch_display_id,
    -- Performance
    processing_time_seconds,
    -- Context
    file_count,
    total_size_bytes,
    status,
    upload_status,
    -- Timestamps
    created_at,
    updated_at
FROM blob_batches;


-- ── file_entries: operational ──────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_file_entries_operational AS
SELECT
    file_display_id,
    batch_display_id,
    original_filename,
    display_name,
    -- Source
    source,
    source_type,
    -- scope is SDK-local only (not in HeartBeat canonical schema)
    connection_type,
    -- File metadata
    content_type,
    file_size_bytes,
    download_count,
    -- Processing state
    status,
    processing_stage,
    error_message,
    preflight_status,
    preflight_details,
    queue_mode,
    -- Processing statistics (Core-populated)
    extracted_invoice_count,
    rejected_invoice_count,
    submitted_invoice_count,
    duplicate_count,
    -- ISO timestamps (no raw unix)
    uploaded_at_iso,
    processed_at_iso,
    finalized_at_iso,
    retention_until_iso,
    deleted_at_iso,
    -- Output indicator
    has_processed_outputs,
    -- Audit
    created_at
FROM file_entries
WHERE deleted_at_unix IS NULL;


-- ── file_entries: identity ─────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_file_entries_identity AS
SELECT
    file_display_id,
    batch_display_id,
    -- User identity
    helium_user_id,
    float_id,
    user_trace_id,
    x_trace_id,
    session_id,
    -- Machine fingerprint
    machine_guid,
    mac_address,
    computer_name,
    -- Connection
    connection_id,
    -- Audit
    created_at
FROM file_entries;


-- ── file_entries: metrics ──────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_file_entries_metrics AS
SELECT
    file_display_id,
    batch_display_id,
    -- Performance
    retry_count,
    -- Context
    processing_stage,
    status,
    file_size_bytes,
    -- Timestamps
    created_at,
    updated_at
FROM file_entries;


-- ── blob_outputs: operational ──────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_blob_outputs_operational AS
SELECT
    bo.id,
    bo.output_type,
    bo.content_type,
    bo.size_bytes,
    bo.created_at_iso,
    bo.created_at,
    -- Linked file context (via JOIN — no blob_uuid exposed)
    fe.file_display_id,
    fe.original_filename,
    fe.batch_display_id
FROM blob_outputs bo
JOIN file_entries fe ON fe.blob_uuid = bo.blob_uuid;


-- ── blob_outputs: metrics ──────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS vw_blob_outputs_metrics AS
SELECT
    bo.id,
    bo.output_type,
    -- Access analytics
    bo.accessed_count,
    bo.last_accessed_unix,
    bo.created_by_core_version,
    -- Linked file context
    fe.file_display_id,
    fe.helium_user_id
FROM blob_outputs bo
JOIN file_entries fe ON fe.blob_uuid = bo.blob_uuid;


-- ============================================================================
-- TABLE 8: blob_downloads (AUDIT — 10 fields)
-- ============================================================================
-- Download audit trail. One row per fetch from HeartBeat (API or mock).
-- Local cache hits (file already on disk) do NOT create rows here.
-- download_count on file_entries is the denormalized aggregate.
-- ============================================================================

CREATE TABLE IF NOT EXISTS blob_downloads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    blob_uuid           TEXT NOT NULL,              -- FK → file_entries.blob_uuid
    file_display_id     TEXT NOT NULL,              -- FK → file_entries.file_display_id
    downloaded_by       TEXT NOT NULL,              -- helium_user_id who triggered the download
    downloaded_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    download_source     TEXT NOT NULL               -- Where the file was fetched from
        CHECK(download_source IN ('heartbeat_api', 'heartbeat_mock', 'local_cache')),
    file_size_bytes     INTEGER,                    -- Size of downloaded file
    download_duration_ms INTEGER,                   -- How long the download took
    session_id          TEXT,                       -- Session that triggered the download
    float_id            TEXT                        -- Float instance that downloaded
);

CREATE INDEX IF NOT EXISTS idx_blob_downloads_blob_uuid
    ON blob_downloads(blob_uuid);
CREATE INDEX IF NOT EXISTS idx_blob_downloads_user
    ON blob_downloads(downloaded_by);


-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS blob_schema_version (
    version         TEXT PRIMARY KEY NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description     TEXT
);

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.1.0', 'Security spec alignment — 3-field machine composite, 3-level trace model, identity fields (helium_user_id, float_id, session_id). 128 fields across 6 tables. MinIO deprecated for single-tenant.');

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.2.0', 'Category views — 8 views (operational/identity/metrics) for blob_batches, file_entries, blob_outputs. Permission-ready field grouping.');

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.3.0', 'Download tracking + scope — blob_downloads audit table (10 fields), file_entries.download_count, file_entries.scope (LOCAL/EXTERNAL). 139 fields across 7 tables.');

INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.4.0', 'Scope field moved to SDK-only (not in HeartBeat canonical schema). 137 fields across 7 tables (canonical). SDK schema.py retains scope for Queue tab display.');
