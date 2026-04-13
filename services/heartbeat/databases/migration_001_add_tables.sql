-- ============================================================================
-- MIGRATION 001: Add daily_usage, audit_events, metrics_events tables
-- For existing databases created before Phase 3
-- Safe to run multiple times (IF NOT EXISTS)
-- ============================================================================

-- TABLE 10: daily_usage
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

-- TABLE 11: audit_events
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

-- TABLE 12: metrics_events
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
