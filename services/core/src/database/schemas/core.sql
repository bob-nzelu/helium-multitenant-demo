-- Core Schema (PostgreSQL) — WS0-defined operational tables
-- Source: MENTAL_MODEL.md §4.4
SET search_path TO core;

-- ── Updated-at trigger function (shared) ────────────────────────────────

CREATE OR REPLACE FUNCTION core.fn_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── Table 1: core_queue ─────────────────────────────────────────────────
-- Processing queue — one entry per ingested file/batch.
-- State machine: PENDING → PROCESSING → PROCESSED | PREVIEW_READY | FAILED

CREATE TABLE IF NOT EXISTS core_queue (
    queue_id            TEXT PRIMARY KEY NOT NULL,
    blob_uuid           TEXT NOT NULL UNIQUE,
    data_uuid           TEXT,
    original_filename   TEXT,
    company_id          TEXT NOT NULL,
    uploaded_by         TEXT,
    batch_id            TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'PREVIEW_READY', 'FINALIZED', 'CANCELLED', 'EXPIRED', 'FAILED')),
    priority            INTEGER NOT NULL DEFAULT 3
                        CHECK (priority BETWEEN 1 AND 5),
    processing_started_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    error_message       TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON core_queue (status);
CREATE INDEX IF NOT EXISTS idx_queue_priority ON core_queue (priority);
CREATE INDEX IF NOT EXISTS idx_queue_data_uuid ON core_queue (data_uuid);
CREATE INDEX IF NOT EXISTS idx_queue_company ON core_queue (company_id);

DROP TRIGGER IF EXISTS trg_core_queue_updated_at ON core_queue;
CREATE TRIGGER trg_core_queue_updated_at
BEFORE UPDATE ON core_queue
FOR EACH ROW EXECUTE FUNCTION core.fn_updated_at();


-- ── Table 2: processed_files ────────────────────────────────────────────
-- Deduplication — SHA256 hash of every processed file.

CREATE TABLE IF NOT EXISTS processed_files (
    file_hash           TEXT PRIMARY KEY NOT NULL,
    original_filename   TEXT,
    queue_id            TEXT,
    data_uuid           TEXT,
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── Table 3: transformation_scripts ─────────────────────────────────────
-- Per-tenant pluggable ETL scripts (AST-validated Python).

CREATE TABLE IF NOT EXISTS transformation_scripts (
    script_id           BIGSERIAL PRIMARY KEY,
    company_id          TEXT NOT NULL,
    script_name         TEXT NOT NULL,
    script_type         TEXT NOT NULL
                        CHECK (script_type IN ('extract', 'validate', 'format', 'enrich')),
    script_code         TEXT NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_transformation_scripts_updated_at ON transformation_scripts;
CREATE TRIGGER trg_transformation_scripts_updated_at
BEFORE UPDATE ON transformation_scripts
FOR EACH ROW EXECUTE FUNCTION core.fn_updated_at();


-- ── Table 4: config ─────────────────────────────────────────────────────
-- Key-value runtime configuration.

CREATE TABLE IF NOT EXISTS config (
    key                 TEXT PRIMARY KEY NOT NULL,
    value               TEXT,
    description         TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_config_updated_at ON config;
CREATE TRIGGER trg_config_updated_at
BEFORE UPDATE ON config
FOR EACH ROW EXECUTE FUNCTION core.fn_updated_at();


-- ── Table 5: reports (WS7) ──────────────────────────────────────────────
-- On-demand and scheduled report metadata. Blob content in HeartBeat.

CREATE TABLE IF NOT EXISTS reports (
    report_id       TEXT PRIMARY KEY NOT NULL,
    report_type     TEXT NOT NULL
                    CHECK (report_type IN (
                        'compliance', 'transmission', 'customer',
                        'audit_trail', 'monthly_summary'
                    )),
    format          TEXT NOT NULL CHECK (format IN ('pdf', 'excel')),
    status          TEXT NOT NULL DEFAULT 'generating'
                    CHECK (status IN ('generating', 'ready', 'failed', 'expired')),
    title           TEXT,
    blob_uuid       TEXT,
    filters         JSONB,
    generated_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    size_bytes      INTEGER,
    error_message   TEXT,
    generated_by    TEXT,
    company_id      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_company ON reports (company_id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports (status);
CREATE INDEX IF NOT EXISTS idx_reports_type ON reports (report_type);
CREATE INDEX IF NOT EXISTS idx_reports_expires ON reports (expires_at)
    WHERE expires_at IS NOT NULL;

DROP TRIGGER IF EXISTS trg_reports_updated_at ON reports;
CREATE TRIGGER trg_reports_updated_at
BEFORE UPDATE ON reports
FOR EACH ROW EXECUTE FUNCTION core.fn_updated_at();
