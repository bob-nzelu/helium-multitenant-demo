-- ============================================================================
-- Migration 003: Add satellite_registrations table
-- Database: registry.db
-- Purpose: Track registered Satellite instances for Primary/Satellite mode (Q6)
-- ============================================================================

CREATE TABLE IF NOT EXISTS satellite_registrations (
    satellite_id TEXT PRIMARY KEY,           -- "satellite-lagos-1"
    display_name TEXT NOT NULL,              -- "Lagos Branch Satellite"
    base_url TEXT NOT NULL,                  -- "http://10.0.2.5:9000"

    -- State
    status TEXT NOT NULL DEFAULT 'active',   -- "active", "revoked", "unreachable"
    last_heartbeat_at TEXT,                  -- ISO-8601: last ping received
    last_heartbeat_status TEXT,              -- "ok", "degraded"

    -- Metadata
    region TEXT,                             -- "lagos", "abuja", etc.
    version TEXT,                            -- "2.0.0"
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    CONSTRAINT status_values CHECK (status IN ('active', 'revoked', 'unreachable'))
);

CREATE INDEX IF NOT EXISTS idx_satellite_registrations_status
    ON satellite_registrations(status)
    WHERE status = 'active';
