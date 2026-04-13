-- ============================================================================
-- CONFIG DATABASE SCHEMA (Q5 — Extensibility)
-- Database: config.db
-- Owner: HeartBeat Service (sole gatekeeper)
-- Version: 1.0
-- Date: 2026-02-18
--
-- Purpose: Configuration management, tier-based limits, feature flags,
--          and database catalog for all Helium tenant databases.
--
-- HeartBeat's 3rd database (alongside blob.db and registry.db).
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- TABLE 1: config_entries (Key-Value Config Store)
-- ============================================================================
-- General-purpose config store. Replaces hardcoded values with live,
-- API-updatable configuration. CRUD via /api/config endpoints.
--
-- Scope: service_name + config_key unique pair.
-- Special service_name "_shared" = tenant-wide keys.
-- ============================================================================
CREATE TABLE IF NOT EXISTS config_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Scope
    service_name TEXT NOT NULL,               -- "heartbeat", "relay", "core", "_shared"
    config_key TEXT NOT NULL,                 -- "max_retries", "daily_limit", "tenant_id"
    config_value TEXT NOT NULL,               -- All values stored as text, caller interprets
    value_type TEXT NOT NULL DEFAULT 'string', -- "string", "int", "bool", "json"

    -- Metadata
    description TEXT,                         -- Human-readable description
    is_encrypted BOOLEAN DEFAULT 0,           -- 0=plaintext, 1=AES256 encrypted (future)
    is_readonly BOOLEAN DEFAULT 0,            -- 1=cannot be changed via API (set by installer)

    -- Audit
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,                          -- "installer", "admin", "api"

    UNIQUE(service_name, config_key)
);

CREATE INDEX IF NOT EXISTS idx_config_entries_service
    ON config_entries(service_name);

CREATE INDEX IF NOT EXISTS idx_config_entries_key
    ON config_entries(config_key);


-- ============================================================================
-- TABLE 2: tier_limits (Per-Tier Resource Limits)
-- ============================================================================
-- Defines what each subscription tier is allowed to do.
-- Seeded at install time. Upgraded via Prodeus update packages.
-- ============================================================================
CREATE TABLE IF NOT EXISTS tier_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    tier TEXT NOT NULL,                       -- "test", "standard", "pro", "enterprise"
    limit_key TEXT NOT NULL,                  -- "daily_upload_limit", "max_file_size_mb", etc.
    limit_value TEXT NOT NULL,               -- Value as text (caller interprets type)
    value_type TEXT NOT NULL DEFAULT 'int',  -- "int", "bool", "string"
    description TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(tier, limit_key),
    CONSTRAINT tier_values CHECK (tier IN ('test', 'standard', 'pro', 'enterprise'))
);

CREATE INDEX IF NOT EXISTS idx_tier_limits_tier
    ON tier_limits(tier);


-- ============================================================================
-- TABLE 3: feature_flags (Feature Toggle System)
-- ============================================================================
-- Controls which features are available per tier/service.
-- ============================================================================
CREATE TABLE IF NOT EXISTS feature_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    flag_name TEXT NOT NULL UNIQUE,          -- "sse_events", "wazuh_logging", "satellite_mode"
    is_enabled BOOLEAN NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT 'global',    -- "global", tier name, or service name
    description TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_flags_name
    ON feature_flags(flag_name);

CREATE INDEX IF NOT EXISTS idx_feature_flags_scope
    ON feature_flags(scope);


-- ============================================================================
-- TABLE 4: database_catalog (Tenant Database Registry)
-- ============================================================================
-- Central registry of every database in the Helium platform.
-- Services register their databases at startup via API.
-- HeartBeat uses this for reconciliation, backup planning, and monitoring.
-- ============================================================================
CREATE TABLE IF NOT EXISTS database_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Logical identity
    db_logical_name TEXT NOT NULL,            -- "sync", "invoices", "his_reference", "blob"
    db_category TEXT NOT NULL,                -- "operational", "reference", "audit", "config"
    tenant_id TEXT NOT NULL,                  -- "pikwik-001", "global"
    owner_service TEXT NOT NULL,              -- "float-sdk", "core", "his", "heartbeat", "relay"

    -- Physical location
    db_physical_name TEXT NOT NULL,           -- "sync_pikwik-001_0e008e8xy0.db"
    db_path TEXT NOT NULL,                    -- Full path
    db_engine TEXT NOT NULL DEFAULT 'sqlite', -- "sqlite" | "postgresql"

    -- Access control
    credential_id TEXT,                       -- Reference to api_credentials in registry.db
    connection_string TEXT,                   -- For PostgreSQL (encrypted)
    is_encrypted BOOLEAN DEFAULT 0,           -- SQLCipher encryption flag (future)

    -- State
    status TEXT NOT NULL DEFAULT 'active',    -- "active" | "migrating" | "archived" | "error"
    schema_version TEXT,                      -- Current migration version
    size_bytes INTEGER,                       -- Last known size

    -- Metadata
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(db_logical_name, tenant_id),
    CONSTRAINT engine_values CHECK (db_engine IN ('sqlite', 'postgresql')),
    CONSTRAINT status_values CHECK (status IN ('active', 'migrating', 'archived', 'error')),
    CONSTRAINT category_values CHECK (db_category IN ('operational', 'reference', 'audit', 'config'))
);

CREATE INDEX IF NOT EXISTS idx_database_catalog_tenant
    ON database_catalog(tenant_id, owner_service);

CREATE INDEX IF NOT EXISTS idx_database_catalog_service
    ON database_catalog(owner_service, status);

CREATE INDEX IF NOT EXISTS idx_database_catalog_status
    ON database_catalog(status);

COMMIT;

-- ============================================================================
-- CONFIG SCHEMA COMPLETE
-- 4 tables: config_entries, tier_limits, feature_flags, database_catalog
-- ============================================================================
