-- ============================================================
-- Event Ledger (SSE_SPEC Section 4)
--
-- Persistent store for all SSE events. Source of truth for
-- replay (catchup) and reconciliation (watermark).
--
-- Per Section 4.1: sequence is auto-increment, monotonic, gap-free.
-- Per Section 4.2: system events (__heartbeat__, connected) are NOT stored.
-- Per Section 4.3: rows retained for 48 hours minimum.
-- ============================================================

SET search_path TO core;

CREATE TABLE IF NOT EXISTS event_ledger (
    sequence    BIGSERIAL   PRIMARY KEY,
    event_type  TEXT        NOT NULL,
    data_json   TEXT        NOT NULL,
    timestamp   TEXT        NOT NULL,
    data_uuid   TEXT,
    company_id  TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_timestamp
    ON event_ledger (timestamp);

CREATE INDEX IF NOT EXISTS idx_ledger_data_uuid
    ON event_ledger (data_uuid) WHERE data_uuid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ledger_company
    ON event_ledger (company_id);

CREATE INDEX IF NOT EXISTS idx_ledger_company_sequence
    ON event_ledger (company_id, sequence);
