-- ============================================================================
-- Migration 003: Add security_events table (P2-B Wazuh Integration)
-- Database: blob.db
-- Date: 2026-02-18
--
-- Stores security-relevant events in OCSF-aligned format for Wazuh ingestion.
-- Wazuh reads the JSONL file, but HeartBeat also persists events in SQLite
-- for dashboard queries and audit correlation.
-- ============================================================================

CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- OCSF core fields
    event_class TEXT NOT NULL,             -- "authentication", "credential_lifecycle", "file_activity", "security_finding"
    event_type TEXT NOT NULL,              -- "auth_failure", "brute_force", "key_rotated", "upload_anomaly"
    severity TEXT NOT NULL DEFAULT 'info', -- "info", "low", "medium", "high", "critical"

    -- Actor
    actor_service TEXT,                    -- "relay", "core", "float-sdk"
    actor_ip TEXT,                         -- Source IP
    actor_credential_id TEXT,             -- API credential used (if any)

    -- Target
    target_resource TEXT,                  -- "/api/blobs/write", "blob:uuid", "credential:id"
    target_service TEXT,                   -- "heartbeat"

    -- Details
    message TEXT NOT NULL,
    details_json TEXT,                     -- JSON blob with event-specific data
    correlation_id TEXT,                   -- Link to audit_events or request ID

    -- Metadata
    created_at TEXT NOT NULL,

    -- Immutability (same pattern as audit_events)
    checksum TEXT                          -- SHA-256 for tamper detection
);

CREATE INDEX IF NOT EXISTS idx_security_events_class ON security_events(event_class);
CREATE INDEX IF NOT EXISTS idx_security_events_severity ON security_events(severity);
CREATE INDEX IF NOT EXISTS idx_security_events_created ON security_events(created_at);
CREATE INDEX IF NOT EXISTS idx_security_events_actor ON security_events(actor_service);

-- Immutability triggers (same as audit_events)
CREATE TRIGGER IF NOT EXISTS security_events_no_update
BEFORE UPDATE ON security_events
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: security_events rows cannot be modified');
END;

CREATE TRIGGER IF NOT EXISTS security_events_no_delete
BEFORE DELETE ON security_events
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: security_events rows cannot be deleted');
END;
