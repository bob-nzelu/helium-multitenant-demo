-- 002_seed_dev_license.sql
-- Dev seed: Pro tier, 1 float seat, 1 owner max.
-- Mimics what the Installer would write in production.

-- ── Installation metadata ────────────────────────────────────────

INSERT OR IGNORE INTO installation_metadata (
    id, machine_id, hostname,
    first_installed_at, installer_version, installed_by_user,
    install_directory, working_directory
) VALUES (
    1,
    'dev-machine-001',
    'PROBOOK-DEV',
    '2026-02-23T00:00:00Z',
    'dev-local',
    'developer',
    'C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat',
    'C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\databases'
);

-- ── License terms: Pro tier, demo mode, valid for 1 year ─────────

INSERT OR IGNORE INTO license_terms (
    id, license_id, tenant_id, tenant_name, tier,
    issued_by, issued_at, expires_at,
    license_hash, monthly_invoice_limit, demo_mode,
    signature
) VALUES (
    1,
    'lic_helium_dev_pro_001',
    'helium-dev',
    'Helium Dev Environment',
    'pro',
    'Pronalytics Limited',
    '2026-01-01T00:00:00Z',
    '2027-01-01T00:00:00Z',
    NULL,       -- No hash validation in dev
    1000,       -- 1000 invoices/month
    1,          -- demo_mode = true
    NULL        -- No Ed25519 signature in dev
);

-- ── License limits: Pro tier with 1 float seat, 1 owner ─────────

INSERT OR IGNORE INTO license_limits (
    id,
    float_seats,
    relay_instances,
    satellite_locations,
    daily_invoice_limit,
    blob_storage_gb,
    max_owners,
    max_admins,
    max_operators,
    max_support_users,
    modules,
    features
) VALUES (
    1,
    1,          -- 1 float seat
    1,          -- 1 relay instance
    0,          -- no satellites (Pro, not Enterprise)
    500,        -- 500 invoices/day
    100,        -- 100 GB blob storage
    1,          -- 1 owner max
    3,          -- 3 admins max
    10,         -- 10 operators max
    2,          -- 2 support users max
    '["relay_bulk"]',
    '{"mfa_enabled": false, "sso_entra": false}'
);

-- ── Package provenance ───────────────────────────────────────────

INSERT OR IGNORE INTO package_provenance (
    id, packaged_by, packaged_at, package_version, package_uuid,
    customer_tin_uuid, client_alias, build_type, environment, dev_mode
) VALUES (
    1,
    'developer@pronalytics.ng',
    '2026-02-23T00:00:00Z',
    'dev-local',
    'pkg-dev-001',
    'tin-dev-001',
    'Helium Dev',
    'demo',
    'test',
    1
);

-- ── Company info: receiving (dev tenant) ─────────────────────────

INSERT OR IGNORE INTO company_info (
    company_type, legal_name, alias, tin, domain, email, description
) VALUES (
    'receiving',
    'Helium Dev Tenant',
    'Helium Dev',
    'DEV-TIN-001',
    'pronalytics.ng',
    'bob.nzelu@pronalytics.ng',
    'Development and testing environment'
);

-- ── Company info: providing (Pronalytics) ────────────────────────

INSERT OR IGNORE INTO company_info (
    company_type, legal_name, alias, email, website
) VALUES (
    'providing',
    'Pronalytics Limited',
    'Pronalytics',
    'support@pronalytics.ng',
    'https://pronalytics.ng'
);

-- ── Provisioned user: matches auth.db test user ──────────────────
-- Password hash matches bob.nzelu in auth.db (1234%%%)
-- In production this would be Argon2, but dev uses bcrypt for consistency

INSERT OR IGNORE INTO provisioned_users (
    email, password_hash, role_id, is_active
) VALUES (
    'bob.nzelu@pronalytics.ng',
    '$2b$12$WKODGSuSwAsltVMr8/2sIORaC/H40z3whEX9V3PKzC5i29fiw7haK',
    'Owner',
    1
);
