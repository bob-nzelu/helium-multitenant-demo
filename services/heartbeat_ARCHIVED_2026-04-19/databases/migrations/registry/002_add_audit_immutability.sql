-- ============================================================================
-- MIGRATION 002: Audit Immutability for registry.db (Q4 — Demo Question)
-- Database: registry.db
-- Date: 2026-02-18
--
-- Adds immutability triggers on key_rotation_log (the audit trail for
-- credential lifecycle events). This table is append-only for compliance.
-- ============================================================================

-- ── Immutability Triggers: key_rotation_log ─────────────────────────────

-- Prevent UPDATE on key_rotation_log
CREATE TRIGGER IF NOT EXISTS key_rotation_log_no_update
BEFORE UPDATE ON key_rotation_log
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: key_rotation_log rows cannot be modified');
END;

-- Prevent DELETE on key_rotation_log
CREATE TRIGGER IF NOT EXISTS key_rotation_log_no_delete
BEFORE DELETE ON key_rotation_log
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: key_rotation_log rows cannot be deleted');
END;
