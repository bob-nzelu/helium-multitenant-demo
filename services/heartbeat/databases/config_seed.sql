-- ============================================================================
-- CONFIG DATABASE SEED DATA
-- Database: config.db
-- Loaded on first initialization (dev/test environments)
-- ============================================================================

-- ── Shared Config Entries ──────────────────────────────────────────────

INSERT OR IGNORE INTO config_entries
    (service_name, config_key, config_value, value_type, description, is_readonly, updated_by, created_at, updated_at)
VALUES
    ('_shared', 'tenant_id', 'dev-tenant-001', 'string', 'Current tenant identifier', 1, 'installer', datetime('now'), datetime('now')),
    ('_shared', 'tenant_name', 'Dev Tenant', 'string', 'Human-readable tenant name', 0, 'installer', datetime('now'), datetime('now')),
    ('_shared', 'tier', 'test', 'string', 'Subscription tier (test/standard/pro/enterprise)', 1, 'installer', datetime('now'), datetime('now')),
    ('_shared', 'helium_version', '2.0.0', 'string', 'Installed Helium platform version', 1, 'installer', datetime('now'), datetime('now'));

-- HeartBeat-specific config
INSERT OR IGNORE INTO config_entries
    (service_name, config_key, config_value, value_type, description, is_readonly, updated_by, created_at, updated_at)
VALUES
    ('heartbeat', 'retention_years', '7', 'int', 'FIRS audit retention period in years', 0, 'installer', datetime('now'), datetime('now')),
    ('heartbeat', 'auto_migrate', 'true', 'bool', 'Run pending DB migrations on startup', 0, 'installer', datetime('now'), datetime('now'));


-- ── Tier Limits ────────────────────────────────────────────────────────

-- Test tier (development / free)
INSERT OR IGNORE INTO tier_limits (tier, limit_key, limit_value, value_type, description, created_at, updated_at)
VALUES
    ('test', 'daily_upload_limit', '100', 'int', 'Max files per company per day', datetime('now'), datetime('now')),
    ('test', 'max_file_size_mb', '10', 'int', 'Max single file size in MB', datetime('now'), datetime('now')),
    ('test', 'max_services', '5', 'int', 'Max registered service instances', datetime('now'), datetime('now')),
    ('test', 'max_databases', '10', 'int', 'Max databases in catalog', datetime('now'), datetime('now')),
    ('test', 'retention_days', '90', 'int', 'Blob retention period in days', datetime('now'), datetime('now')),
    ('test', 'sse_enabled', 'false', 'bool', 'SSE event streaming allowed', datetime('now'), datetime('now'));

-- Standard tier
INSERT OR IGNORE INTO tier_limits (tier, limit_key, limit_value, value_type, description, created_at, updated_at)
VALUES
    ('standard', 'daily_upload_limit', '1000', 'int', 'Max files per company per day', datetime('now'), datetime('now')),
    ('standard', 'max_file_size_mb', '50', 'int', 'Max single file size in MB', datetime('now'), datetime('now')),
    ('standard', 'max_services', '10', 'int', 'Max registered service instances', datetime('now'), datetime('now')),
    ('standard', 'max_databases', '25', 'int', 'Max databases in catalog', datetime('now'), datetime('now')),
    ('standard', 'retention_days', '365', 'int', 'Blob retention period in days', datetime('now'), datetime('now')),
    ('standard', 'sse_enabled', 'true', 'bool', 'SSE event streaming allowed', datetime('now'), datetime('now'));

-- Pro tier
INSERT OR IGNORE INTO tier_limits (tier, limit_key, limit_value, value_type, description, created_at, updated_at)
VALUES
    ('pro', 'daily_upload_limit', '5000', 'int', 'Max files per company per day', datetime('now'), datetime('now')),
    ('pro', 'max_file_size_mb', '100', 'int', 'Max single file size in MB', datetime('now'), datetime('now')),
    ('pro', 'max_services', '25', 'int', 'Max registered service instances', datetime('now'), datetime('now')),
    ('pro', 'max_databases', '50', 'int', 'Max databases in catalog', datetime('now'), datetime('now')),
    ('pro', 'retention_days', '730', 'int', 'Blob retention period in days (2yr)', datetime('now'), datetime('now')),
    ('pro', 'sse_enabled', 'true', 'bool', 'SSE event streaming allowed', datetime('now'), datetime('now'));

-- Enterprise tier
INSERT OR IGNORE INTO tier_limits (tier, limit_key, limit_value, value_type, description, created_at, updated_at)
VALUES
    ('enterprise', 'daily_upload_limit', '50000', 'int', 'Max files per company per day', datetime('now'), datetime('now')),
    ('enterprise', 'max_file_size_mb', '500', 'int', 'Max single file size in MB', datetime('now'), datetime('now')),
    ('enterprise', 'max_services', '100', 'int', 'Max registered service instances', datetime('now'), datetime('now')),
    ('enterprise', 'max_databases', '200', 'int', 'Max databases in catalog', datetime('now'), datetime('now')),
    ('enterprise', 'retention_days', '2555', 'int', 'Blob retention period in days (7yr FIRS)', datetime('now'), datetime('now')),
    ('enterprise', 'sse_enabled', 'true', 'bool', 'SSE event streaming allowed', datetime('now'), datetime('now'));


-- ── Feature Flags ──────────────────────────────────────────────────────

INSERT OR IGNORE INTO feature_flags (flag_name, is_enabled, scope, description, created_at, updated_at)
VALUES
    ('sse_events', 0, 'global', 'Server-Sent Events streaming (P2-D)', datetime('now'), datetime('now')),
    ('wazuh_logging', 0, 'global', 'Wazuh security event emission (P2-B)', datetime('now'), datetime('now')),
    ('satellite_mode', 0, 'global', 'Primary/Satellite coordination (Q6)', datetime('now'), datetime('now')),
    ('reconciliation', 0, 'global', 'Reconciliation job scheduling (P2-E)', datetime('now'), datetime('now')),
    ('audit_checksums', 1, 'global', 'Audit event checksum chain (Q4)', datetime('now'), datetime('now')),
    ('prometheus_metrics', 1, 'global', 'Prometheus /metrics endpoint (P2-A)', datetime('now'), datetime('now'));


-- ── Database Catalog (HeartBeat's own databases) ───────────────────────

INSERT OR IGNORE INTO database_catalog
    (db_logical_name, db_category, tenant_id, owner_service, db_physical_name, db_path, db_engine, status, schema_version, description, created_at, updated_at)
VALUES
    ('blob', 'operational', 'global', 'heartbeat', 'blob.db', 'databases/blob.db', 'sqlite', 'active', '2', 'Blob storage, dedup, audit, limits, metrics', datetime('now'), datetime('now')),
    ('registry', 'operational', 'global', 'heartbeat', 'registry.db', 'databases/registry.db', 'sqlite', 'active', '2', 'Service discovery, API credentials, endpoint catalog', datetime('now'), datetime('now')),
    ('config', 'config', 'global', 'heartbeat', 'config.db', 'databases/config.db', 'sqlite', 'active', '1', 'Configuration, tier limits, feature flags, DB catalog', datetime('now'), datetime('now'));
