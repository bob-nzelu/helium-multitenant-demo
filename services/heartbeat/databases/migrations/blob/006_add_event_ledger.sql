-- ============================================================================
-- Migration 006: Add Event Ledger (SSE Spec Section 4)
-- Date: 2026-04-01
-- Description: Persistent event store for SSE replay and catchup.
--   Stores every SSE event with monotonic sequence for Last-Event-ID
--   reconnect, paginated catchup, and reconciliation watermarks.
--   Retention: 48 hours, pruned every 6 hours.
-- ============================================================================

CREATE TABLE IF NOT EXISTS event_ledger (
    sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    data_json   TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    data_uuid   TEXT,
    company_id  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON event_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_ledger_data_uuid ON event_ledger(data_uuid) WHERE data_uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ledger_company ON event_ledger(company_id);

-- Record migration
INSERT OR IGNORE INTO blob_schema_version (version, description)
VALUES ('1.5.0', 'Add event_ledger table for SSE replay, catchup, and reconciliation (SSE Spec v1.1).');
