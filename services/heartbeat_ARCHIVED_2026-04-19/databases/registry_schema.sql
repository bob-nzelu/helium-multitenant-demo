-- ============================================================================
-- SERVICE REGISTRY DATABASE SCHEMA
-- Database: registry.db
-- Owner: HeartBeat Service (sole gatekeeper)
-- Version: 1.0
-- Date: 2026-02-17
--
-- Purpose: Dynamic service discovery + API key management.
-- HeartBeat is the ONLY service with direct DB access.
-- All other services call HeartBeat's HTTP API.
--
-- Install flow:
--   1. Installer runs HeartBeat first
--   2. Installer pre-seeds API keys via HeartBeat API
--   3. Each service polls HeartBeat, registers endpoints, gets catalog
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- TABLE 1: service_instances (Running Service Instances)
-- ============================================================================
-- Each running service instance registers itself here.
-- Multiple instances per service_name allowed (relay-bulk-1, relay-nas-1).
-- Core/Edge typically 1 instance; Relay/Float-SDK may have multiple.
-- ============================================================================
CREATE TABLE IF NOT EXISTS service_instances (
    service_instance_id TEXT PRIMARY KEY,    -- "relay-bulk-1", "core-primary", "heartbeat-primary"
    service_name TEXT NOT NULL,              -- "relay", "core", "heartbeat", "edge", "float-sdk"
    display_name TEXT NOT NULL,              -- "Relay Bulk Upload Service"

    -- Network location
    base_url TEXT NOT NULL,                  -- "http://127.0.0.1:8082"
    health_url TEXT,                         -- "http://127.0.0.1:8082/health"
    websocket_url TEXT,                      -- "ws://127.0.0.1:8080/sync" (Core only)

    -- Version & tier
    version TEXT NOT NULL DEFAULT '2.0.0',
    tier TEXT NOT NULL DEFAULT 'test',       -- "test", "standard", "pro", "enterprise"

    -- State
    is_active BOOLEAN DEFAULT 1,
    last_health_check_at TEXT,               -- ISO-8601 timestamp
    last_health_status TEXT,                 -- "healthy", "degraded", "down"

    -- Timestamps
    registered_at TEXT NOT NULL,             -- First registration
    updated_at TEXT NOT NULL,                -- Last re-registration or health update

    -- Constraints
    CONSTRAINT tier_values CHECK (tier IN ('test', 'standard', 'pro', 'enterprise')),
    CONSTRAINT health_values CHECK (
        last_health_status IS NULL OR
        last_health_status IN ('healthy', 'degraded', 'down')
    )
);

-- Indexes for service_instances
CREATE INDEX IF NOT EXISTS idx_service_instances_name
    ON service_instances(service_name);

CREATE INDEX IF NOT EXISTS idx_service_instances_active
    ON service_instances(is_active, service_name)
    WHERE is_active = 1;


-- ============================================================================
-- TABLE 2: service_endpoint_catalog (Endpoints Each Instance Exposes)
-- ============================================================================
-- When a service registers, it sends its list of API endpoints.
-- Other services query this to discover available operations.
-- ============================================================================
CREATE TABLE IF NOT EXISTS service_endpoint_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    service_instance_id TEXT NOT NULL,       -- FK -> service_instances
    method TEXT NOT NULL,                    -- "POST", "GET", "PUT", "DELETE"
    path TEXT NOT NULL,                      -- "/api/v1/enqueue"
    description TEXT,                        -- "Enqueue file for processing"
    requires_auth BOOLEAN DEFAULT 1,

    created_at TEXT NOT NULL,

    FOREIGN KEY (service_instance_id) REFERENCES service_instances(service_instance_id)
        ON DELETE CASCADE,

    UNIQUE(service_instance_id, method, path)
);

-- Indexes for service_endpoint_catalog
CREATE INDEX IF NOT EXISTS idx_endpoint_catalog_instance
    ON service_endpoint_catalog(service_instance_id);

CREATE INDEX IF NOT EXISTS idx_endpoint_catalog_method_path
    ON service_endpoint_catalog(method, path);


-- ============================================================================
-- TABLE 3: api_credentials (API Keys for Inter-Service Auth)
-- ============================================================================
-- Pre-seeded by Installer at install time.
-- Services use their key+secret to authenticate with HeartBeat.
-- HeartBeat validates keys on every registry API call.
--
-- Key format: {2-letter-svc}_{env}_{random_hex}
--   e.g., rl_test_abc123def456 (Relay, test environment)
--
-- Secret: bcrypt-hashed (12 rounds). Plaintext returned ONLY at creation.
-- ============================================================================
CREATE TABLE IF NOT EXISTS api_credentials (
    credential_id TEXT PRIMARY KEY,          -- UUID
    api_key TEXT NOT NULL UNIQUE,             -- "rl_test_abc123..."
    api_secret_hash TEXT NOT NULL,            -- bcrypt hash

    -- Ownership
    service_name TEXT NOT NULL,               -- "relay", "core", etc.
    issued_to TEXT NOT NULL,                  -- "relay-bulk-1", "core-primary"

    -- Permissions
    permissions TEXT,                         -- JSON: ["blob.write", "registry.read"]

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'active',    -- "active", "expiring", "revoked", "inactive"
    expires_at TEXT,                          -- ISO-8601 (null = never expires)
    last_used_at TEXT,                        -- Updated on each successful validation
    last_rotated_at TEXT,                     -- When key was last rotated

    -- Timestamps
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    -- Constraints
    CONSTRAINT status_values CHECK (status IN ('active', 'expiring', 'revoked', 'inactive'))
);

-- Indexes for api_credentials
CREATE INDEX IF NOT EXISTS idx_api_credentials_key
    ON api_credentials(api_key);

CREATE INDEX IF NOT EXISTS idx_api_credentials_service
    ON api_credentials(service_name);

CREATE INDEX IF NOT EXISTS idx_api_credentials_status
    ON api_credentials(status)
    WHERE status = 'active';


-- ============================================================================
-- TABLE 4: key_rotation_log (Immutable Audit Trail)
-- ============================================================================
-- Every key lifecycle event is recorded here.
-- Never deleted — compliance audit trail.
-- ============================================================================
CREATE TABLE IF NOT EXISTS key_rotation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    credential_id TEXT NOT NULL,
    action TEXT NOT NULL,                    -- "created", "rotated", "revoked", "expired"
    performed_by TEXT NOT NULL,              -- "installer", "admin", "heartbeat-auto"
    old_key_prefix TEXT,                     -- First 8 chars of old key (for identification)
    reason TEXT,                             -- "routine rotation", "compromised", etc.

    created_at TEXT NOT NULL,

    FOREIGN KEY (credential_id) REFERENCES api_credentials(credential_id),

    CONSTRAINT action_values CHECK (action IN ('created', 'rotated', 'revoked', 'expired'))
);

-- Indexes for key_rotation_log
CREATE INDEX IF NOT EXISTS idx_key_rotation_credential
    ON key_rotation_log(credential_id);

CREATE INDEX IF NOT EXISTS idx_key_rotation_time
    ON key_rotation_log(created_at DESC);


-- ============================================================================
-- TABLE 5: service_config (Key-Value Config Per Service)
-- ============================================================================
-- Misc configuration that services can query from HeartBeat.
-- is_encrypted=1 means config_value is AES256-encrypted (future Key Vault).
-- ============================================================================
CREATE TABLE IF NOT EXISTS service_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    service_name TEXT NOT NULL,
    config_key TEXT NOT NULL,
    config_value TEXT NOT NULL,
    is_encrypted BOOLEAN DEFAULT 0,          -- 0=plaintext, 1=AES256 encrypted

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(service_name, config_key)
);

-- Indexes for service_config
CREATE INDEX IF NOT EXISTS idx_service_config_service
    ON service_config(service_name);

COMMIT;

-- ============================================================================
-- REGISTRY SCHEMA COMPLETE
-- 5 tables: service_instances, service_endpoint_catalog, api_credentials,
--           key_rotation_log, service_config
-- ============================================================================
