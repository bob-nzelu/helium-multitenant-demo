-- ============================================================================
-- SERVICE REGISTRY SEED DATA
-- Database: registry.db
-- Purpose: Pre-seed HeartBeat's own registration + API keys for all services
-- Version: 1.0
-- Date: 2026-02-17
--
-- SECURITY NOTE: Plaintext secrets are documented in comments for DEV ONLY.
-- Production uses Installer-generated secrets (never stored as plaintext).
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- 1. HeartBeat Self-Registration (first service, always present)
-- ============================================================================
INSERT OR IGNORE INTO service_instances (
    service_instance_id, service_name, display_name,
    base_url, health_url, websocket_url,
    version, tier, is_active,
    last_health_status,
    registered_at, updated_at
) VALUES (
    'heartbeat-primary', 'heartbeat', 'HeartBeat Primary',
    'http://127.0.0.1:9000', 'http://127.0.0.1:9000/health', NULL,
    '2.0.0', 'test', 1,
    'healthy',
    datetime('now'), datetime('now')
);

-- HeartBeat's endpoint catalog (all endpoints it exposes)
INSERT OR IGNORE INTO service_endpoint_catalog (service_instance_id, method, path, description, requires_auth, created_at) VALUES
    -- Blob operations
    ('heartbeat-primary', 'POST', '/api/blobs/write', 'Write file to blob storage', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/blobs/register', 'Register blob metadata', 1, datetime('now')),
    -- Dedup
    ('heartbeat-primary', 'GET', '/api/dedup/check', 'Check file hash for duplicates', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/dedup/record', 'Record processed file hash', 1, datetime('now')),
    -- Limits
    ('heartbeat-primary', 'GET', '/api/limits/daily', 'Check daily upload limit', 1, datetime('now')),
    -- Audit
    ('heartbeat-primary', 'POST', '/api/audit/log', 'Log audit event', 1, datetime('now')),
    -- Metrics
    ('heartbeat-primary', 'POST', '/api/metrics/report', 'Report metrics', 1, datetime('now')),
    -- Blob status (Float SDK + Core)
    ('heartbeat-primary', 'GET', '/api/v1/heartbeat/blob/{uuid}/status', 'Get blob processing status', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/v1/heartbeat/blob/{uuid}/status', 'Update blob processing status', 1, datetime('now')),
    -- Registry (service discovery)
    ('heartbeat-primary', 'POST', '/api/registry/register', 'Register service instance + endpoints', 1, datetime('now')),
    ('heartbeat-primary', 'GET', '/api/registry/discover', 'Discover all services', 1, datetime('now')),
    ('heartbeat-primary', 'GET', '/api/registry/discover/{service_name}', 'Discover specific service', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/registry/health/{instance_id}', 'Report instance health', 1, datetime('now')),
    ('heartbeat-primary', 'GET', '/api/registry/config/{service_name}', 'Get service config', 1, datetime('now')),
    -- Credential management (admin only)
    ('heartbeat-primary', 'POST', '/api/registry/credentials/generate', 'Generate new API key', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/registry/credentials/{credential_id}/rotate', 'Rotate API key', 1, datetime('now')),
    ('heartbeat-primary', 'POST', '/api/registry/credentials/{credential_id}/revoke', 'Revoke API key', 1, datetime('now')),
    ('heartbeat-primary', 'GET', '/api/registry/credentials/{service_name}', 'List credentials for service', 1, datetime('now')),
    -- Health / Root
    ('heartbeat-primary', 'GET', '/health', 'Service health check', 0, datetime('now')),
    ('heartbeat-primary', 'GET', '/', 'Service info', 0, datetime('now'));


-- ============================================================================
-- 2. Pre-Seeded API Credentials (created by Installer)
-- ============================================================================
-- DEV SECRETS (plaintext, for local testing only):
--   hb_test_heartbeat001  => secret-heartbeat-primary-001
--   rl_test_relay001      => secret-for-test-key-001     (matches Relay test fixtures)
--   rl_test_relay002      => secret-relay-nas-001
--   cr_test_core001       => secret-core-primary-001
--   ed_test_edge001       => secret-edge-primary-001
--   fl_test_float001      => secret-float-sdk-dev-001
-- ============================================================================

-- HeartBeat admin key (all permissions)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000001',
    'hb_test_heartbeat001',
    '$2b$12$zJ457yM02AG6c9YtpsDgSuJPlV9az5D8HackQw24Sq.Pano4ZQd/y',
    'heartbeat', 'heartbeat-primary',
    '["*"]',
    'active',
    datetime('now'), datetime('now')
);

-- Relay bulk key (blob + dedup + limits + audit + metrics)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000002',
    'rl_test_relay001',
    '$2b$12$Vh92BhZHR8CnIk./dj8i2OA3UDcgkd27KFtZzAUOa7rJsCUOZFJh.',
    'relay', 'relay-bulk-1',
    '["blob.write", "blob.read", "dedup.check", "dedup.record", "limits.check", "audit.log", "metrics.report", "registry.register", "registry.discover"]',
    'active',
    datetime('now'), datetime('now')
);

-- Relay NAS key (same permissions, different instance)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000003',
    'rl_test_relay002',
    '$2b$12$GMisOEnVNs3GVmN4jzAzqe.Pjw7W/Hi/hBEzsVINoVrfjqXj.oyHS',
    'relay', 'relay-nas-1',
    '["blob.write", "blob.read", "dedup.check", "dedup.record", "limits.check", "audit.log", "metrics.report", "registry.register", "registry.discover"]',
    'active',
    datetime('now'), datetime('now')
);

-- Core key (blob read + status update + audit + metrics)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000004',
    'cr_test_core001',
    '$2b$12$xH/3iBhgVNF0YmirclTtcuEZcNRC.cqdpAnhEBnDyLkjX9/J4AiH.',
    'core', 'core-primary',
    '["blob.read", "blob.status.update", "audit.log", "metrics.report", "registry.register", "registry.discover"]',
    'active',
    datetime('now'), datetime('now')
);

-- Edge key (audit + metrics + registry read)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000005',
    'ed_test_edge001',
    '$2b$12$EblKkxndWuqG9ghs6LszYuWVLS6tt2zpqo/ubFgn/4f4EAgAARYb2',
    'edge', 'edge-primary',
    '["audit.log", "metrics.report", "registry.register", "registry.discover"]',
    'active',
    datetime('now'), datetime('now')
);

-- Float SDK key (blob read + status read + registry read)
INSERT OR IGNORE INTO api_credentials (
    credential_id, api_key, api_secret_hash,
    service_name, issued_to, permissions, status,
    created_at, updated_at
) VALUES (
    'cred-00000000-0000-0000-0000-000000000006',
    'fl_test_float001',
    '$2b$12$fYgEZlAe1T0b59aHyKWEeOcpcIszc/Uc/fcSAX7Hu7ehF.XFdEOnK',
    'float-sdk', 'float-sdk-dev',
    '["blob.read", "blob.status.read", "registry.register", "registry.discover"]',
    'active',
    datetime('now'), datetime('now')
);


-- ============================================================================
-- 3. Key Rotation Log (initial creation events)
-- ============================================================================
INSERT OR IGNORE INTO key_rotation_log (credential_id, action, performed_by, reason, created_at) VALUES
    ('cred-00000000-0000-0000-0000-000000000001', 'created', 'installer', 'Initial install seed', datetime('now')),
    ('cred-00000000-0000-0000-0000-000000000002', 'created', 'installer', 'Initial install seed', datetime('now')),
    ('cred-00000000-0000-0000-0000-000000000003', 'created', 'installer', 'Initial install seed', datetime('now')),
    ('cred-00000000-0000-0000-0000-000000000004', 'created', 'installer', 'Initial install seed', datetime('now')),
    ('cred-00000000-0000-0000-0000-000000000005', 'created', 'installer', 'Initial install seed', datetime('now')),
    ('cred-00000000-0000-0000-0000-000000000006', 'created', 'installer', 'Initial install seed', datetime('now'));


-- ============================================================================
-- 4. Service Config (misc settings)
-- ============================================================================
INSERT OR IGNORE INTO service_config (service_name, config_key, config_value, is_encrypted, created_at, updated_at) VALUES
    ('relay', 'max_concurrent_uploads', '10', 0, datetime('now'), datetime('now')),
    ('relay', 'max_file_size_mb', '50', 0, datetime('now'), datetime('now')),
    ('core', 'processing_timeout_seconds', '300', 0, datetime('now'), datetime('now')),
    ('core', 'max_concurrent_jobs', '5', 0, datetime('now'), datetime('now')),
    ('heartbeat', 'reconciliation_interval_minutes', '60', 0, datetime('now'), datetime('now')),
    ('heartbeat', 'retention_years', '7', 0, datetime('now'), datetime('now')),
    ('edge', 'polling_interval_seconds', '30', 0, datetime('now'), datetime('now'));

COMMIT;

-- ============================================================================
-- REGISTRY SEED COMPLETE
-- 1 service instance (heartbeat-primary) + 20 endpoints
-- 6 API credentials (1 per service + extra relay)
-- 6 rotation log entries
-- 7 config entries
-- ============================================================================
