-- ============================================================================
-- 007_devices_and_app_registrations.sql
--
-- Device registration, session device_id, and app registration tables.
-- Matches the runtime code in src/database/pg_auth.py:
--   - register_device()          -> auth.devices
--   - create_session(device_id)  -> auth.sessions.device_id column
--   - create_app_registration()  -> auth.app_registrations
--
-- Without this migration, fresh HeartBeat deployments fail on first
-- device/app registration call with "relation does not exist".
-- ============================================================================

-- Devices: one row per machine. device_id = SHA256(machine_guid:mac)[:16]
CREATE TABLE IF NOT EXISTS auth.devices (
    device_id        TEXT PRIMARY KEY,
    user_id          TEXT,
    machine_guid     TEXT NOT NULL,
    mac_address      TEXT,
    computer_name    TEXT,
    os_type          TEXT NOT NULL,
    os_version       TEXT,
    last_app_type    TEXT,
    last_app_version TEXT,
    last_seen_at     TIMESTAMPTZ,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_revoked       BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at       TIMESTAMPTZ,
    revoked_by       TEXT
);

-- Sessions: bind a session to its device (for eviction + JWT device_id claim)
ALTER TABLE auth.sessions ADD COLUMN IF NOT EXISTS device_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_device ON auth.sessions(device_id);

-- App registrations: one row per (device_id, source_type) pair.
-- source_id format: "src-{source_type[:5]}-{device_id[:6]}-{sequence}"
-- NOTE: `source_type` is scheduled to be renamed to `app_type` in a future
-- migration to avoid collision with Core's `invoices.source` field
-- (BULK_UPLOAD, MANUAL, API, POLLER, EMAIL). See technical debt log.
CREATE TABLE IF NOT EXISTS auth.app_registrations (
    id              SERIAL PRIMARY KEY,
    source_id       TEXT UNIQUE NOT NULL,
    source_type     TEXT NOT NULL,
    source_name     TEXT,
    device_id       TEXT,
    user_id         TEXT,
    tenant_id       TEXT NOT NULL,
    app_version     TEXT,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ,
    UNIQUE(device_id, source_type)
);

CREATE INDEX IF NOT EXISTS idx_app_registrations_user
    ON auth.app_registrations(user_id);
CREATE INDEX IF NOT EXISTS idx_app_registrations_tenant
    ON auth.app_registrations(tenant_id);
