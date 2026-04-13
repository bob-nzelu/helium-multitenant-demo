-- Migration 003: Add access_control table
-- Controls which services can access which resources (modules, endpoints, config).
-- Used by Platform Services and Registry Discovery to filter responses by caller.

CREATE TABLE IF NOT EXISTS access_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name   TEXT NOT NULL,                    -- e.g. "relay", "core", "sdk"
    resource_type  TEXT NOT NULL,                    -- e.g. "transforma_module", "endpoint", "config"
    resource_key   TEXT NOT NULL,                    -- e.g. "qr_generator", "/api/blobs/write", "*" for wildcard
    access_level   TEXT NOT NULL DEFAULT 'read',     -- "read", "write", "execute", "none"
    description    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(service_name, resource_type, resource_key)
);

CREATE INDEX IF NOT EXISTS idx_access_control_service
    ON access_control(service_name);
CREATE INDEX IF NOT EXISTS idx_access_control_resource
    ON access_control(resource_type, resource_key);

-- Seed: Transforma module access rules
-- Relay gets QR module (contains IRN + QR + CSID) + service keys only
INSERT OR IGNORE INTO access_control
    (service_name, resource_type, resource_key, access_level, description, created_at, updated_at)
VALUES
    ('relay', 'transforma_module', 'qr_generator', 'read',
     'Relay gets QR module (IRN+QR+CSID evaluation)', datetime('now'), datetime('now')),
    ('relay', 'transforma_module', 'service_keys', 'read',
     'Relay gets FIRS service keys for QR encryption', datetime('now'), datetime('now')),
    ('core', 'transforma_module', '*', 'read',
     'Core gets all Transforma modules', datetime('now'), datetime('now')),
    ('sdk', 'transforma_module', 'none', 'none',
     'SDK has no direct Transforma access', datetime('now'), datetime('now'));
