-- ============================================================================
-- MIGRATION 002: Audit Immutability (Q4 — Demo Question)
-- Database: blob.db
-- Date: 2026-02-18
--
-- Adds:
--   1. checksum_chain column to audit_events (SHA-256 chain for tamper detection)
--   2. BEFORE UPDATE trigger on audit_events (prevents modification)
--   3. BEFORE DELETE trigger on audit_events (prevents deletion)
--   4. Same immutability triggers on key_rotation_log (blob.db doesn't have this
--      table, but registry.db does — this migration only touches blob.db tables)
--   5. Same immutability triggers on blob_cleanup_history
-- ============================================================================

-- Add checksum_chain column to audit_events
-- NULL for legacy rows (pre-migration), populated for new rows
ALTER TABLE audit_events ADD COLUMN checksum_chain TEXT;

-- ── Immutability Triggers: audit_events ──────────────────────────────────

-- Prevent UPDATE on audit_events
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: audit_events rows cannot be modified');
END;

-- Prevent DELETE on audit_events
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE: audit_events rows cannot be deleted');
END;

-- ── Immutability Triggers: blob_cleanup_history ─────────────────────────
-- NOTE: blob_cleanup_history was dropped in canonical schema v1.4.0.
-- These triggers are retained for backward compatibility with pre-migration
-- databases that still have the table. On fresh canonical installs, the
-- table doesn't exist so these triggers are no-ops.
-- Migration 005 drops the table (and its triggers) as part of the canonical
-- migration, so these only apply to databases between v002 and v005.
