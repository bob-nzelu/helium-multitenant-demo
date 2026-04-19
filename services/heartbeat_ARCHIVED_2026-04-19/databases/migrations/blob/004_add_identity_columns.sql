-- ============================================================================
-- MIGRATION 004: Add identity/trace columns to blob_entries and blob_batches
-- Database: blob.db
-- Date: 2026-03-04
--
-- Adds 8 identity/trace columns to harmonize with SDK's schema.py v5.1.
-- These columns enable full user/machine traceability on every blob record.
--
-- Fields (canonical — from Float/App/SDK/src/ws1_database/schema.py):
--   user_trace_id   — SDK-generated trace ID
--   x_trace_id      — Relay-generated trace ID
--   helium_user_id  — From JWT sub claim (authenticated user)
--   float_id        — Machine-tied SDK installation ID
--   session_id      — 8-hour session ID (JWT jti)
--   machine_guid    — Windows machine GUID
--   mac_address     — NIC MAC address
--   computer_name   — Machine hostname
--
-- All columns are nullable for backward compatibility with existing records
-- and machine-to-machine (HMAC-only, no human user) uploads.
-- ============================================================================

-- blob_entries: 8 identity columns
ALTER TABLE blob_entries ADD COLUMN user_trace_id TEXT;
ALTER TABLE blob_entries ADD COLUMN x_trace_id TEXT;
ALTER TABLE blob_entries ADD COLUMN helium_user_id TEXT;
ALTER TABLE blob_entries ADD COLUMN float_id TEXT;
ALTER TABLE blob_entries ADD COLUMN session_id TEXT;
ALTER TABLE blob_entries ADD COLUMN machine_guid TEXT;
ALTER TABLE blob_entries ADD COLUMN mac_address TEXT;
ALTER TABLE blob_entries ADD COLUMN computer_name TEXT;

-- blob_batches: 8 identity columns
ALTER TABLE blob_batches ADD COLUMN user_trace_id TEXT;
ALTER TABLE blob_batches ADD COLUMN x_trace_id TEXT;
ALTER TABLE blob_batches ADD COLUMN helium_user_id TEXT;
ALTER TABLE blob_batches ADD COLUMN float_id TEXT;
ALTER TABLE blob_batches ADD COLUMN session_id TEXT;
ALTER TABLE blob_batches ADD COLUMN machine_guid TEXT;
ALTER TABLE blob_batches ADD COLUMN mac_address TEXT;
ALTER TABLE blob_batches ADD COLUMN computer_name TEXT;

-- Indexes for blob_entries identity columns (partial — only non-NULL)
CREATE INDEX IF NOT EXISTS idx_blob_entries_helium_user_id
    ON blob_entries(helium_user_id)
    WHERE helium_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_blob_entries_x_trace_id
    ON blob_entries(x_trace_id)
    WHERE x_trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_blob_entries_float_id
    ON blob_entries(float_id)
    WHERE float_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_blob_entries_machine_guid
    ON blob_entries(machine_guid)
    WHERE machine_guid IS NOT NULL;

-- Indexes for blob_batches identity columns (partial — only non-NULL)
CREATE INDEX IF NOT EXISTS idx_blob_batches_helium_user_id
    ON blob_batches(helium_user_id)
    WHERE helium_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_blob_batches_x_trace_id
    ON blob_batches(x_trace_id)
    WHERE x_trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_blob_batches_float_id
    ON blob_batches(float_id)
    WHERE float_id IS NOT NULL;
