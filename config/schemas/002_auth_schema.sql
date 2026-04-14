-- ============================================================================
-- PostgreSQL Auth Schema for HeartBeat
-- Adapted from HeartBeat SQLite migrations 001-006
-- Uses 'auth' schema namespace (PgAuthDatabase queries auth.*)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS auth;

-- Schema migrations tracking
CREATE TABLE IF NOT EXISTS auth.schema_migrations (
    version           INTEGER PRIMARY KEY,
    filename          TEXT NOT NULL,
    description       TEXT NOT NULL,
    checksum          TEXT NOT NULL DEFAULT '',
    applied_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_time_ms INTEGER NOT NULL DEFAULT 0
);

-- Roles
CREATE TABLE IF NOT EXISTS auth.roles (
    role_id     TEXT PRIMARY KEY,
    role_name   TEXT NOT NULL,
    description TEXT
);

-- Permissions
CREATE TABLE IF NOT EXISTS auth.permissions (
    permission_id   TEXT PRIMARY KEY,
    description     TEXT
);

-- Users
CREATE TABLE IF NOT EXISTS auth.users (
    user_id             TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT,
    role_id             TEXT NOT NULL REFERENCES auth.roles(role_id),
    owner_sequence      TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    must_reset_password BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_configured      BOOLEAN NOT NULL DEFAULT FALSE,
    is_first_run        BOOLEAN NOT NULL DEFAULT TRUE,
    permissions_version INTEGER NOT NULL DEFAULT 1,
    master_secret       TEXT,
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ,
    deactivated_at      TIMESTAMPTZ,
    deactivated_by      TEXT
);

-- Role Default Permissions
CREATE TABLE IF NOT EXISTS auth.role_permissions (
    role_id         TEXT NOT NULL REFERENCES auth.roles(role_id),
    permission_id   TEXT NOT NULL REFERENCES auth.permissions(permission_id),
    PRIMARY KEY (role_id, permission_id)
);

-- Per-User Permission Overrides
CREATE TABLE IF NOT EXISTS auth.user_permissions (
    user_id         TEXT NOT NULL REFERENCES auth.users(user_id),
    permission_id   TEXT NOT NULL REFERENCES auth.permissions(permission_id),
    granted         BOOLEAN DEFAULT TRUE,
    granted_by      TEXT NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    PRIMARY KEY (user_id, permission_id)
);

-- Sessions
CREATE TABLE IF NOT EXISTS auth.sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES auth.users(user_id),
    jwt_jti             TEXT NOT NULL UNIQUE,
    issued_at           TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    session_expires_at  TIMESTAMPTZ,
    last_refreshed      TIMESTAMPTZ,
    last_auth_at        TIMESTAMPTZ NOT NULL,
    last_auth_method    TEXT NOT NULL DEFAULT 'password',
    is_revoked          BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at          TIMESTAMPTZ,
    revoked_reason      TEXT
);

-- Password History (recycling prevention)
CREATE TABLE IF NOT EXISTS auth.password_history (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES auth.users(user_id) ON DELETE CASCADE,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pw_history_user_recent
    ON auth.password_history(user_id, created_at DESC);

-- Step-up Policies
CREATE TABLE IF NOT EXISTS auth.step_up_policies (
    operation               TEXT PRIMARY KEY,
    required_within_seconds INTEGER NOT NULL DEFAULT 300,
    tier                    TEXT NOT NULL DEFAULT 'standard'
);

-- ============================================================================
-- SEED: Roles
-- ============================================================================

INSERT INTO auth.roles (role_id, role_name, description) VALUES
    ('Owner', 'Owner', 'Foundational authority. Maximum 2 per tenant. Full unrestricted access.'),
    ('Admin', 'Admin', 'Full view, scoped write. Can create operators and support users.'),
    ('Operator', 'Operator', 'Explicitly scoped only. No default rights.'),
    ('Support', 'Support', 'Diagnostic view only by default.')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- SEED: Permissions
-- ============================================================================

INSERT INTO auth.permissions (permission_id, description) VALUES
    ('*', 'Full unrestricted access'),
    ('invoice.view', 'View invoices'),
    ('invoice.approve', 'Approve invoices for finalisation'),
    ('invoice.upload', 'Upload new invoice files'),
    ('invoice.finalize', 'Finalize invoices for FIRS submission'),
    ('user.create.operator', 'Create Operator users'),
    ('user.create.support', 'Create Support users'),
    ('user.create.admin', 'Create Admin users'),
    ('user.deactivate', 'Deactivate users'),
    ('config.view', 'View system configuration'),
    ('config.write', 'Modify system configuration'),
    ('audit.view', 'View audit logs'),
    ('health.read', 'View health endpoints'),
    ('integration.config.write', 'Configure integration settings')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- SEED: Role-Permission Defaults
-- ============================================================================

INSERT INTO auth.role_permissions (role_id, permission_id) VALUES
    ('Owner', '*'),
    ('Admin', 'invoice.view'),
    ('Admin', 'config.view'),
    ('Admin', 'audit.view'),
    ('Admin', 'health.read'),
    ('Admin', 'user.create.operator'),
    ('Admin', 'user.create.support'),
    ('Support', 'health.read')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- SEED: Step-up Policies
-- ============================================================================

INSERT INTO auth.step_up_policies (operation, required_within_seconds, tier) VALUES
    ('invoice.finalize', 300, 'standard'),
    ('invoice.approve', 300, 'standard'),
    ('user.create.admin', 60, 'elevated'),
    ('user.deactivate', 60, 'elevated'),
    ('config.write', 120, 'elevated')
ON CONFLICT DO NOTHING;
