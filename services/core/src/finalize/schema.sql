-- ============================================================================
-- WS5 Finalize: Supporting Tables
-- ============================================================================
-- Date:    2026-03-25
-- Status:  Required for Tier A integrity safeguards
-- See:     WS5_DB_INTEGRITY.md
-- ============================================================================


-- ── A1: Idempotency Guard ───────────────────────────────────────────────
-- Prevents duplicate finalize executions on retry.
-- Key = SHA-256(batch_id + company_id + version_number)
-- TTL: 24 hours. Expired keys cleaned up lazily or by scheduled job.

CREATE TABLE IF NOT EXISTS finalize_idempotency (
    idempotency_key  TEXT PRIMARY KEY NOT NULL,
    batch_id         TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    result_json      TEXT,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_finalize_idemp_expires
    ON finalize_idempotency(expires_at);

CREATE INDEX IF NOT EXISTS idx_finalize_idemp_batch
    ON finalize_idempotency(batch_id);


-- ── A3: Finalize Audit Log ──────────────────────────────────────────────
-- Records every pipeline step for traceability.
-- One row per step per finalize attempt.

CREATE TABLE IF NOT EXISTS finalize_audit_log (
    id               SERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    idempotency_key  TEXT,
    action           TEXT NOT NULL,
    status           TEXT NOT NULL,
    detail           TEXT,
    invoice_count    INTEGER,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_finalize_audit_batch
    ON finalize_audit_log(batch_id);

CREATE INDEX IF NOT EXISTS idx_finalize_audit_created
    ON finalize_audit_log(created_at);

CREATE INDEX IF NOT EXISTS idx_finalize_audit_action_status
    ON finalize_audit_log(action, status);
