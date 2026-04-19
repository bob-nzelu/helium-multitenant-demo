-- ============================================================================
-- MIGRATION 001: Bootstrap schema_migrations table for blob.db
-- Database: blob.db
-- Date: 2026-02-18
--
-- This is the foundational migration. The schema_migrations table itself
-- is created by the migrator before any migrations run (ensure_table()),
-- so this migration just serves as a recorded checkpoint confirming
-- that the migration framework is active for this database.
--
-- Also adds any missing tables from the Phase 1 schema that may not
-- exist in very old databases (safe: all IF NOT EXISTS).
-- ============================================================================

-- Ensure all Phase 1 tables exist (idempotent)
-- This handles the case where an old blob.db was created before
-- tables 10-12 were added to the main schema.sql

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

CREATE INDEX IF NOT EXISTS idx_daily_usage_company_date
    ON daily_usage(company_id, usage_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_usage_date
    ON daily_usage(usage_date DESC);

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

CREATE INDEX IF NOT EXISTS idx_audit_events_service
    ON audit_events(service, event_type);
CREATE INDEX IF NOT EXISTS idx_audit_events_type
    ON audit_events(event_type, created_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_user
    ON audit_events(user_id, created_at_unix DESC)
    WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_events_trace
    ON audit_events(trace_id)
    WHERE trace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_events_time
    ON audit_events(created_at_unix DESC);

CREATE TABLE IF NOT EXISTS metrics_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL,
    metric_values TEXT NOT NULL,
    reported_by TEXT,
    created_at TEXT NOT NULL,
    created_at_unix INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_events_type
    ON metrics_events(metric_type, created_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_events_time
    ON metrics_events(created_at_unix DESC);
