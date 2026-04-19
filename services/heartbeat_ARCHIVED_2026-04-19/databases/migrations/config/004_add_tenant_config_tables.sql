-- ============================================================================
-- MIGRATION 004: Tenant Configuration Tables
-- Database: config.db
-- Date: 2026-03-31
--
-- Adds structured tenant configuration tables to config.db.
-- These tables store the authoritative tenant data that HeartBeat serves
-- to SDK/Float, Relay, Core, and other services via per-service config
-- endpoints (see TENANT_CONFIG_HANDOFF_SPEC.md).
--
-- Tables added:
--   1. tenant_config          — Primary: company identity + branding (1 row per tenant)
--   2. tenant_bank_accounts   — Bank account details (N per tenant)
--   3. tenant_service_endpoints — Service URLs + credentials (N per tenant)
--   4. tenant_registrations   — Regulatory registrations (N per tenant)
--   5. float_instances        — Registered Float installations (N per tenant)
--   6. float_users            — Users provisioned per Float (N per float)
--   7. tenant_firs_config     — FIRS integration config (1 per tenant)
--   8. tenant_smtp_config     — SMTP/email config (N per tenant)
--   9. tenant_nas_config      — NAS/folder watcher config (N per tenant)
--   10. tenant_crypto_keys    — Cryptographic keys (N per tenant)
--
-- Data flow:
--   Admin Packager → Installer → config.db (at install time)
--   HeartBeat reads config.db → serves per-service JSON responses
--   SSE broadcasts config.updated when admin changes anything
-- ============================================================================


-- ============================================================================
-- TABLE 1: tenant_config (1 row per tenant)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_config (
    tenant_id               TEXT PRIMARY KEY NOT NULL,
    company_name            TEXT NOT NULL,
    trading_name            TEXT,

    -- Tax & Corporate identity
    tin                     TEXT,
    rc_number               TEXT,

    -- Contact
    address                 TEXT,
    city                    TEXT,
    state                   TEXT,
    state_code              TEXT,
    postal_code             TEXT,
    country                 TEXT DEFAULT 'Nigeria',
    country_code            TEXT DEFAULT 'NG',
    email                   TEXT,
    phone                   TEXT,
    website                 TEXT,
    domain                  TEXT,
    description             TEXT,

    -- Invoicing defaults
    default_currency        TEXT DEFAULT 'NGN',
    default_due_date_days   INTEGER DEFAULT 30,
    invoice_prefix          TEXT,

    -- Branding
    logo_image              BLOB,
    logo_mime_type          TEXT,
    primary_color           TEXT,
    secondary_color         TEXT,
    signature_enabled       INTEGER DEFAULT 1,
    signer_name             TEXT,
    signer_title            TEXT,
    signature_image         BLOB,

    -- Tier & License
    tier                    TEXT DEFAULT 'standard'
        CHECK(tier IN ('test', 'standard', 'pro', 'enterprise')),

    -- Metadata from Admin Packager
    packaged_by             TEXT,
    packaged_at             TEXT,
    installer_version       TEXT,
    client_alias            TEXT,
    package_uuid            TEXT,

    -- Extras (JSON for client-specific data like NEPZA permit, FTZ status)
    extras                  TEXT DEFAULT '{}',

    -- Audit
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================================
-- TABLE 2: tenant_bank_accounts
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_bank_accounts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    bank_name               TEXT NOT NULL,
    account_name            TEXT NOT NULL,
    account_number          TEXT NOT NULL,
    bank_code               TEXT,
    currency                TEXT DEFAULT 'NGN',
    is_primary              INTEGER DEFAULT 0,
    display_order           INTEGER DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_bank_accounts_tenant
    ON tenant_bank_accounts(tenant_id);


-- ============================================================================
-- TABLE 3: tenant_service_endpoints
-- ============================================================================
-- NOTE: api_key and api_secret are NOT stored here.
-- Credentials live in registry.db (api_credentials table) where they belong.
-- This table only stores service URLs for routing/discovery.
CREATE TABLE IF NOT EXISTS tenant_service_endpoints (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    service_name            TEXT NOT NULL
        CHECK(service_name IN ('relay', 'heartbeat', 'core', 'his', 'edge')),
    api_url                 TEXT NOT NULL,
    sse_url                 TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, service_name)
);

CREATE INDEX IF NOT EXISTS idx_tenant_endpoints_tenant
    ON tenant_service_endpoints(tenant_id);


-- ============================================================================
-- TABLE 4: tenant_registrations
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registrations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    authority               TEXT NOT NULL
        CHECK(authority IN ('FIRS', 'CAC', 'STATE_IRS', 'NEPZA', 'OTHER')),
    registration_id         TEXT,
    registration_date       TEXT,
    expiry_date             TEXT,
    status                  TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'expired', 'suspended')),
    metadata                TEXT DEFAULT '{}',
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, authority)
);

CREATE INDEX IF NOT EXISTS idx_tenant_registrations_tenant
    ON tenant_registrations(tenant_id);


-- ============================================================================
-- TABLE 5: float_instances (registered Float installations)
-- ============================================================================
CREATE TABLE IF NOT EXISTS float_instances (
    float_id                TEXT PRIMARY KEY NOT NULL,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    machine_guid            TEXT,
    mac_address             TEXT,
    computer_name           TEXT,
    registered_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at            TEXT,
    status                  TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'suspended', 'decommissioned')),
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(machine_guid, mac_address)
);

CREATE INDEX IF NOT EXISTS idx_float_instances_tenant
    ON float_instances(tenant_id);
CREATE INDEX IF NOT EXISTS idx_float_instances_machine
    ON float_instances(machine_guid);


-- ============================================================================
-- TABLE 6: float_users (users provisioned per Float)
-- ============================================================================
CREATE TABLE IF NOT EXISTS float_users (
    user_id                 TEXT PRIMARY KEY NOT NULL,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    float_id                TEXT REFERENCES float_instances(float_id),
    display_name            TEXT NOT NULL,
    email                   TEXT NOT NULL UNIQUE,
    role                    TEXT NOT NULL DEFAULT 'Operator'
        CHECK(role IN ('Owner', 'Admin', 'Operator', 'Support')),
    title                   TEXT,
    phone                   TEXT,
    avatar_image            BLOB,
    avatar_mime_type        TEXT,
    permissions             TEXT DEFAULT '[]',
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_float_users_tenant
    ON float_users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_float_users_email
    ON float_users(email);


-- ============================================================================
-- TABLE 7: tenant_firs_config (FIRS integration — 1 per tenant)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_firs_config (
    tenant_id               TEXT PRIMARY KEY NOT NULL REFERENCES tenant_config(tenant_id),
    business_id             TEXT,
    entity_id               TEXT,
    service_id              TEXT,
    endpoint_test           TEXT,
    endpoint_live           TEXT,
    api_key                 TEXT,
    api_secret              TEXT,
    timeout_seconds         INTEGER DEFAULT 30,
    retry_attempts          INTEGER DEFAULT 3,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================================
-- TABLE 8: tenant_smtp_config (email config — N per tenant for receiving/providing)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_smtp_config (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    config_type             TEXT NOT NULL
        CHECK(config_type IN ('receiving', 'providing')),
    enabled                 INTEGER DEFAULT 0,
    host                    TEXT,
    port                    INTEGER,
    username                TEXT,
    password                TEXT,
    use_tls                 INTEGER DEFAULT 1,
    sender_email            TEXT,
    sender_name             TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, config_type)
);


-- ============================================================================
-- TABLE 9: tenant_nas_config (NAS/folder watcher — N per tenant)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_nas_config (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    path                    TEXT NOT NULL,
    username                TEXT,
    password                TEXT,
    scan_interval_seconds   INTEGER DEFAULT 5,
    file_stability_seconds  INTEGER DEFAULT 3,
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tenant_nas_config_tenant
    ON tenant_nas_config(tenant_id);


-- ============================================================================
-- TABLE 10: tenant_crypto_keys (cryptographic keys — N per tenant)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_crypto_keys (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    key_type                TEXT NOT NULL
        CHECK(key_type IN ('firs_public_key', 'firs_certificate', 'signing_key', 'encryption_key')),
    key_data                TEXT,
    key_algorithm           TEXT DEFAULT 'RSA-2048',
    valid_from              TEXT,
    valid_until             TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, key_type)
);

CREATE INDEX IF NOT EXISTS idx_tenant_crypto_keys_tenant
    ON tenant_crypto_keys(tenant_id);
