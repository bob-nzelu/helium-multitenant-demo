-- 001_initial_schema.sql
-- license.db — Immutable after installation.
--
-- In production, the Installer creates and populates this database.
-- HeartBeat only READS it for enforcement. It never writes.
--
-- In dev mode, HeartBeat creates and seeds this database at startup
-- if it doesn't already exist.
--
-- Schema merges LICENSE_DB_SCHEMA.md v1.1 + Part 4 Section 13.

-- ── Schema Migrations (standard pattern) ─────────────────────────

CREATE TABLE IF NOT EXISTS schema_migrations (
    version             INTEGER PRIMARY KEY,
    filename            TEXT NOT NULL,
    description         TEXT NOT NULL,
    checksum            TEXT NOT NULL,
    applied_at          TEXT NOT NULL,
    execution_time_ms   INTEGER NOT NULL DEFAULT 0
);

-- ── Installation Metadata ────────────────────────────────────────

CREATE TABLE installation_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Only 1 row

    -- Machine identification
    machine_id          TEXT NOT NULL UNIQUE,
    hostname            TEXT,

    -- Installation details
    first_installed_at  TEXT NOT NULL,
    installer_version   TEXT NOT NULL,
    installed_by_user   TEXT,

    -- File paths
    install_directory   TEXT NOT NULL,
    working_directory   TEXT NOT NULL,

    -- Reinstall tracking
    last_reinstalled_at TEXT,
    reinstall_count     INTEGER DEFAULT 0
);

-- ── License Terms (merged: v1.1 schema + Part 4 Section 13) ─────

CREATE TABLE license_terms (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Only 1 row

    -- Part 4 Section 13 fields
    license_id          TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    tenant_name         TEXT NOT NULL,
    tier                TEXT NOT NULL CHECK(tier IN ('test', 'standard', 'pro', 'enterprise')),
    issued_by           TEXT NOT NULL DEFAULT 'Pronalytics Limited',
    issued_at           TEXT NOT NULL,
    expires_at          TEXT NOT NULL,

    -- v1.1 schema fields
    license_hash        TEXT,               -- Argon2 hash for offline validation
    monthly_invoice_limit INTEGER NOT NULL DEFAULT 500,
    demo_mode           BOOLEAN DEFAULT 1,

    -- Ed25519 signature (Part 4 Section 13)
    signature           TEXT,               -- NULL in dev mode

    CONSTRAINT chk_dates CHECK (issued_at < expires_at),
    CONSTRAINT chk_limit CHECK (monthly_invoice_limit > 0)
);

-- ── License Limits (Part 4 Section 13 — enforced by HeartBeat) ──

CREATE TABLE license_limits (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Only 1 row

    float_seats             INTEGER NOT NULL DEFAULT 1,
    relay_instances         INTEGER NOT NULL DEFAULT 1,
    satellite_locations     INTEGER NOT NULL DEFAULT 0,
    daily_invoice_limit     INTEGER NOT NULL DEFAULT 500,
    blob_storage_gb         INTEGER NOT NULL DEFAULT 50,
    max_owners              INTEGER NOT NULL DEFAULT 1,
    max_admins              INTEGER NOT NULL DEFAULT 5,
    max_operators           INTEGER NOT NULL DEFAULT 10,
    max_support_users       INTEGER NOT NULL DEFAULT 2,

    -- Module access (JSON array of enabled module names)
    modules                 TEXT NOT NULL DEFAULT '[]',

    -- Feature flags (JSON object)
    features                TEXT NOT NULL DEFAULT '{}'
);

-- ── Package Provenance ───────────────────────────────────────────

CREATE TABLE package_provenance (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Only 1 row

    packaged_by         TEXT NOT NULL,
    packaged_at         TEXT NOT NULL,
    package_version     TEXT NOT NULL,
    package_uuid        TEXT NOT NULL UNIQUE,

    customer_tin_uuid   TEXT NOT NULL,
    client_alias        TEXT NOT NULL,

    build_type          TEXT CHECK(build_type IN ('demo', 'production')),
    environment         TEXT CHECK(environment IN ('test', 'live')),
    dev_mode            BOOLEAN DEFAULT 0
);

-- ── Company Info ─────────────────────────────────────────────────

CREATE TABLE company_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_type        TEXT CHECK(company_type IN ('receiving', 'providing')),

    legal_name          TEXT NOT NULL,
    alias               TEXT NOT NULL,
    tin                 TEXT,
    rc_number           TEXT,
    postal_code         TEXT,

    address             TEXT,
    city                TEXT,
    state               TEXT,
    country             TEXT DEFAULT 'Nigeria',
    phone               TEXT,
    email               TEXT,
    website             TEXT,

    description         TEXT,
    domain              TEXT,

    UNIQUE(company_type)
);

-- ── Provisioned Users ────────────────────────────────────────────

CREATE TABLE provisioned_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,          -- Argon2 hash
    role_id             TEXT NOT NULL DEFAULT 'Operator'
                            CHECK(role_id IN ('Owner', 'Admin', 'Operator', 'Support')),

    is_active           BOOLEAN DEFAULT 1,
    created_at          TEXT DEFAULT (datetime('now')),
    last_login_at       TEXT,

    failed_login_attempts INTEGER DEFAULT 0,
    locked_until        TEXT
);
