-- ============================================================================
-- MIGRATION 001: Bootstrap schema_migrations table for registry.db
-- Database: registry.db
-- Date: 2026-02-18
--
-- Foundational migration confirming migration framework is active.
-- Ensures all 5 Phase 1 tables exist (idempotent).
-- ============================================================================

-- Ensure all Phase 1 registry tables exist (idempotent)

CREATE TABLE IF NOT EXISTS service_instances (
    service_instance_id TEXT PRIMARY KEY,
    service_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    health_url TEXT,
    websocket_url TEXT,
    version TEXT NOT NULL DEFAULT '2.0.0',
    tier TEXT NOT NULL DEFAULT 'test',
    is_active BOOLEAN DEFAULT 1,
    last_health_check_at TEXT,
    last_health_status TEXT,
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT tier_values CHECK (tier IN ('test', 'standard', 'pro', 'enterprise')),
    CONSTRAINT health_values CHECK (
        last_health_status IS NULL OR
        last_health_status IN ('healthy', 'degraded', 'down')
    )
);

CREATE INDEX IF NOT EXISTS idx_service_instances_name
    ON service_instances(service_name);
CREATE INDEX IF NOT EXISTS idx_service_instances_active
    ON service_instances(is_active, service_name)
    WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS service_endpoint_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_instance_id TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    description TEXT,
    requires_auth BOOLEAN DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (service_instance_id) REFERENCES service_instances(service_instance_id)
        ON DELETE CASCADE,
    UNIQUE(service_instance_id, method, path)
);

CREATE INDEX IF NOT EXISTS idx_endpoint_catalog_instance
    ON service_endpoint_catalog(service_instance_id);
CREATE INDEX IF NOT EXISTS idx_endpoint_catalog_method_path
    ON service_endpoint_catalog(method, path);

CREATE TABLE IF NOT EXISTS api_credentials (
    credential_id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL UNIQUE,
    api_secret_hash TEXT NOT NULL,
    service_name TEXT NOT NULL,
    issued_to TEXT NOT NULL,
    permissions TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    last_used_at TEXT,
    last_rotated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT status_values CHECK (status IN ('active', 'expiring', 'revoked', 'inactive'))
);

CREATE INDEX IF NOT EXISTS idx_api_credentials_key
    ON api_credentials(api_key);
CREATE INDEX IF NOT EXISTS idx_api_credentials_service
    ON api_credentials(service_name);
CREATE INDEX IF NOT EXISTS idx_api_credentials_status
    ON api_credentials(status)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS key_rotation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_id TEXT NOT NULL,
    action TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    old_key_prefix TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (credential_id) REFERENCES api_credentials(credential_id),
    CONSTRAINT action_values CHECK (action IN ('created', 'rotated', 'revoked', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_key_rotation_credential
    ON key_rotation_log(credential_id);
CREATE INDEX IF NOT EXISTS idx_key_rotation_time
    ON key_rotation_log(created_at DESC);

CREATE TABLE IF NOT EXISTS service_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    config_key TEXT NOT NULL,
    config_value TEXT NOT NULL,
    is_encrypted BOOLEAN DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(service_name, config_key)
);

CREATE INDEX IF NOT EXISTS idx_service_config_service
    ON service_config(service_name);
