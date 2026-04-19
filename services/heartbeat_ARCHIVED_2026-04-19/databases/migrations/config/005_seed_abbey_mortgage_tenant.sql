-- ============================================================================
-- MIGRATION 005: Seed Abbey Mortgage Bank Sample Tenant
-- Database: config.db
-- Date: 2026-03-31
--
-- Creates a complete sample tenant for dev/test Float implementation.
-- Matches TENANT_CONFIG_HANDOFF_SPEC.md Section 2 (Abbey Mortgage example).
--
-- Tenant: Abbey Mortgage Bank PLC
-- User: Bob Nzelu (Owner)
-- Float: Dev instance on PROBOOK machine
-- ============================================================================


-- ============================================================================
-- 1. Tenant Config
-- ============================================================================
INSERT OR IGNORE INTO tenant_config (
    tenant_id, company_name, trading_name,
    tin, rc_number,
    address, city, state, state_code, postal_code,
    country, country_code, email, phone,
    website, domain, description,
    default_currency, default_due_date_days, invoice_prefix,
    signature_enabled, signer_name, signer_title,
    primary_color, secondary_color,
    tier, client_alias,
    packaged_by, packaged_at, installer_version,
    created_at, updated_at
) VALUES (
    'tenant-abbey-001',
    'Abbey Mortgage Bank PLC',
    'Abbey',
    '12345678-0001',
    'RC-123456',
    '45 East 78th Street, Victoria Island',
    'Lagos',
    'Lagos State',
    'LA',
    '101233',
    'Nigeria',
    'NG',
    'contact@abbeymortgage.com',
    '+234 1 555 0123',
    'https://www.abbeymortgage.com',
    'abbeymortgage.com',
    'Abbey Mortgage Bank PLC is a leading Nigerian mortgage bank providing residential and commercial property financing solutions.',
    'NGN',
    30,
    'PRO-ABB-',
    1,
    'James O. Adeyemi',
    'Chief Financial Officer',
    '#003366',
    '#0066CC',
    'standard',
    'Abbey',
    'admin@prodeus.ng',
    '2026-03-31T00:00:00Z',
    'v2.0.0',
    datetime('now'),
    datetime('now')
);


-- ============================================================================
-- 2. Float Instance (dev machine)
-- ============================================================================
INSERT OR IGNORE INTO float_instances (
    float_id, tenant_id,
    machine_guid, mac_address, computer_name,
    registered_at, status, created_at, updated_at
) VALUES (
    'float-dev-001',
    'tenant-abbey-001',
    'DEV-MACHINE-GUID-PROBOOK',
    'AA:BB:CC:DD:EE:FF',
    'PROBOOK',
    datetime('now'),
    'active',
    datetime('now'),
    datetime('now')
);


-- ============================================================================
-- 3. Float User (Bob Nzelu — Owner)
-- ============================================================================
INSERT OR IGNORE INTO float_users (
    user_id, tenant_id, float_id,
    display_name, email, role, title, phone,
    permissions, is_active, last_login_at,
    created_at, updated_at
) VALUES (
    'user-bob-001',
    'tenant-abbey-001',
    'float-dev-001',
    'Bob Nzelu',
    'bob@abbeymortgage.com',
    'Owner',
    'Managing Director',
    '+234 801 234 5678',
    '["invoices:read","invoices:write","invoices:delete","invoices:admin","reports:read","admin:full","uploads:write","uploads:delete","customers:read","customers:write","inventory:read","inventory:write","settings:read","settings:write"]',
    1,
    datetime('now'),
    datetime('now'),
    datetime('now')
);


-- ============================================================================
-- 4. Bank Accounts
-- ============================================================================
INSERT OR IGNORE INTO tenant_bank_accounts (
    tenant_id, bank_name, account_name, account_number,
    bank_code, currency, is_primary, display_order
) VALUES
    ('tenant-abbey-001', 'Guaranty Trust Bank', 'Abbey Mortgage Bank PLC', '0028893389', '058', 'NGN', 1, 0),
    ('tenant-abbey-001', 'First Bank of Nigeria', 'Abbey Mortgage Bank PLC', '2033445566', '011', 'NGN', 0, 1);


-- ============================================================================
-- 5. Service Endpoints (dev/localhost)
-- ============================================================================
-- NOTE: No api_key/api_secret here — credentials live in registry.db
INSERT OR IGNORE INTO tenant_service_endpoints (
    tenant_id, service_name, api_url, sse_url
) VALUES
    ('tenant-abbey-001', 'relay', 'http://127.0.0.1:8082', NULL),
    ('tenant-abbey-001', 'heartbeat', 'http://127.0.0.1:9000', 'http://127.0.0.1:9000/api/v1/events/stream'),
    ('tenant-abbey-001', 'core', 'http://127.0.0.1:8080', 'http://127.0.0.1:8080/api/sync/events'),
    ('tenant-abbey-001', 'his', 'http://127.0.0.1:8090', NULL);


-- ============================================================================
-- 6. Registrations
-- ============================================================================
INSERT OR IGNORE INTO tenant_registrations (
    tenant_id, authority, registration_id,
    registration_date, expiry_date, status, metadata
) VALUES
    ('tenant-abbey-001', 'FIRS', 'FIRS-TIN-12345678-0001', '2020-01-15', NULL, 'active', '{}'),
    ('tenant-abbey-001', 'CAC', 'RC-123456', '2015-06-01', NULL, 'active', '{"company_type":"PLC","date_of_incorporation":"2015-06-01"}');


-- ============================================================================
-- 7. FIRS Config
-- ============================================================================
INSERT OR IGNORE INTO tenant_firs_config (
    tenant_id, business_id, entity_id, service_id,
    endpoint_test, endpoint_live,
    api_key, api_secret,
    timeout_seconds, retry_attempts
) VALUES (
    'tenant-abbey-001',
    'BIZ_ABBEY_001',
    'ENT_ABBEY_002',
    'SVC_ABBEY_003',
    'https://dev-api.vendra.ng/api/v1/public/firs/submit',
    'https://api.firs.gov.ng/v1/firs/submit',
    'ABBEY_FIRS_API_KEY_DEMO',
    NULL,
    30,
    3
);


-- ============================================================================
-- 8. SMTP Config (providing only — dev)
-- ============================================================================
INSERT OR IGNORE INTO tenant_smtp_config (
    tenant_id, config_type, enabled,
    host, port, username, password,
    use_tls, sender_email, sender_name
) VALUES
    ('tenant-abbey-001', 'receiving', 0, NULL, NULL, NULL, NULL, 1, NULL, NULL),
    ('tenant-abbey-001', 'providing', 1, 'smtp.prodeus.ng', 587, 'einvoice@prodeus.ng', 'PRODEUS_SMTP_PASSWORD', 1, 'einvoice@prodeus.ng', 'Prodeus Helium - Abbey Mortgage');


-- ============================================================================
-- 9. Update shared config entries to link tenant
-- ============================================================================
UPDATE config_entries SET config_value = 'tenant-abbey-001'
    WHERE service_name = '_shared' AND config_key = 'tenant_id';

UPDATE config_entries SET config_value = 'Abbey Mortgage Bank PLC'
    WHERE service_name = '_shared' AND config_key = 'tenant_name';
