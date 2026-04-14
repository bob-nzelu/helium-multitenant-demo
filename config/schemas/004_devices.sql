-- ============================================================================
-- 004_devices.sql — Device registration, session device_id, app registrations
-- ============================================================================

-- Devices table
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

-- Add device_id to sessions
ALTER TABLE auth.sessions ADD COLUMN IF NOT EXISTS device_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_device ON auth.sessions(device_id);

-- App registrations table
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
