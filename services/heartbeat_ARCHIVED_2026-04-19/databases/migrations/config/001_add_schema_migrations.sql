-- ============================================================================
-- Migration 001: Bootstrap config.db schema_migrations tracking
-- Database: config.db
-- Date: 2026-02-18
--
-- Ensures the 4 config.db tables exist (idempotent via IF NOT EXISTS).
-- This is the bootstrap migration — schema.sql creates them on first init,
-- this migration ensures they exist for databases created before the
-- migration framework was added.
-- ============================================================================

-- schema_migrations is handled by the migrator itself (ensure_table).
-- This migration just verifies all 4 config tables exist.

CREATE TABLE IF NOT EXISTS config_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    config_key TEXT NOT NULL,
    config_value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'string',
    description TEXT,
    is_encrypted BOOLEAN DEFAULT 0,
    is_readonly BOOLEAN DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    UNIQUE(service_name, config_key)
);

CREATE INDEX IF NOT EXISTS idx_config_entries_service ON config_entries(service_name);
CREATE INDEX IF NOT EXISTS idx_config_entries_key ON config_entries(config_key);

CREATE TABLE IF NOT EXISTS tier_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tier TEXT NOT NULL,
    limit_key TEXT NOT NULL,
    limit_value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'int',
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tier, limit_key),
    CONSTRAINT tier_values CHECK (tier IN ('test', 'standard', 'pro', 'enterprise'))
);

CREATE INDEX IF NOT EXISTS idx_tier_limits_tier ON tier_limits(tier);

CREATE TABLE IF NOT EXISTS feature_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_name TEXT NOT NULL UNIQUE,
    is_enabled BOOLEAN NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT 'global',
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feature_flags_name ON feature_flags(flag_name);
CREATE INDEX IF NOT EXISTS idx_feature_flags_scope ON feature_flags(scope);

CREATE TABLE IF NOT EXISTS database_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    db_logical_name TEXT NOT NULL,
    db_category TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    owner_service TEXT NOT NULL,
    db_physical_name TEXT NOT NULL,
    db_path TEXT NOT NULL,
    db_engine TEXT NOT NULL DEFAULT 'sqlite',
    credential_id TEXT,
    connection_string TEXT,
    is_encrypted BOOLEAN DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    schema_version TEXT,
    size_bytes INTEGER,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(db_logical_name, tenant_id),
    CONSTRAINT engine_values CHECK (db_engine IN ('sqlite', 'postgresql')),
    CONSTRAINT status_values CHECK (status IN ('active', 'migrating', 'archived', 'error')),
    CONSTRAINT category_values CHECK (db_category IN ('operational', 'reference', 'audit', 'config'))
);

CREATE INDEX IF NOT EXISTS idx_database_catalog_tenant ON database_catalog(tenant_id, owner_service);
CREATE INDEX IF NOT EXISTS idx_database_catalog_service ON database_catalog(owner_service, status);
CREATE INDEX IF NOT EXISTS idx_database_catalog_status ON database_catalog(status);
