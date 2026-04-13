-- ═══════════════════════════════════════════════════════════════════
-- auth.db — Migration 001: Initial Schema (Standard Tier)
-- ═══════════════════════════════════════════════════════════════════
--
-- Creates Standard tier tables:
--   users, roles, permissions, role_permissions,
--   user_permissions, sessions, schema_migrations
--
-- Seeds default roles and permissions.
-- auth.db is SQLCipher-encrypted at rest.
-- ═══════════════════════════════════════════════════════════════════

-- Schema migrations tracking (matches codebase standard)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version           INTEGER PRIMARY KEY,
    filename          TEXT NOT NULL,
    description       TEXT NOT NULL,
    checksum          TEXT NOT NULL,
    applied_at        TEXT NOT NULL,
    execution_time_ms INTEGER NOT NULL DEFAULT 0
);

-- ─── Roles ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
    role_id     TEXT PRIMARY KEY,
    role_name   TEXT NOT NULL,
    description TEXT
);

-- ─── Permissions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS permissions (
    permission_id   TEXT PRIMARY KEY,
    description     TEXT
);

-- ─── Users ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id             TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT,
    role_id             TEXT NOT NULL,
    owner_sequence      TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    must_reset_password INTEGER NOT NULL DEFAULT 0,
    mfa_configured      INTEGER NOT NULL DEFAULT 0,
    is_first_run        INTEGER NOT NULL DEFAULT 0,
    created_by          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_login_at       TEXT,
    deactivated_at      TEXT,
    deactivated_by      TEXT,
    FOREIGN KEY (role_id) REFERENCES roles(role_id)
);

-- ─── Role Default Permissions ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id         TEXT NOT NULL,
    permission_id   TEXT NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    FOREIGN KEY (role_id) REFERENCES roles(role_id),
    FOREIGN KEY (permission_id) REFERENCES permissions(permission_id)
);

-- ─── Per-User Permission Overrides ────────────────────────────────
CREATE TABLE IF NOT EXISTS user_permissions (
    user_id         TEXT NOT NULL,
    permission_id   TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    expires_at      TEXT,
    PRIMARY KEY (user_id, permission_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (permission_id) REFERENCES permissions(permission_id)
);

-- ─── Active Sessions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    jwt_jti         TEXT NOT NULL UNIQUE,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    last_refreshed  TEXT,
    last_auth_at    TEXT NOT NULL,
    is_revoked      INTEGER NOT NULL DEFAULT 0,
    revoked_at      TEXT,
    revoked_reason  TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);


-- ═══════════════════════════════════════════════════════════════════
-- SEED DATA
-- ═══════════════════════════════════════════════════════════════════

-- ─── Default Roles ────────────────────────────────────────────────
INSERT OR IGNORE INTO roles (role_id, role_name, description) VALUES
    ('Owner', 'Owner', 'Foundational authority. Maximum 2 per tenant. Full unrestricted access.');
INSERT OR IGNORE INTO roles (role_id, role_name, description) VALUES
    ('Admin', 'Admin', 'Full view, scoped write. Can create operators and support users. Write rights assigned by Owner.');
INSERT OR IGNORE INTO roles (role_id, role_name, description) VALUES
    ('Operator', 'Operator', 'Explicitly scoped only. No default rights. All permissions must be explicitly granted.');
INSERT OR IGNORE INTO roles (role_id, role_name, description) VALUES
    ('Support', 'Support', 'Diagnostic view only by default. Elevated access via time-bound grants.');

-- ─── Permission Definitions ───────────────────────────────────────
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('*', 'Full unrestricted access — all permissions');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('invoice.view', 'View invoices and invoice data');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('invoice.approve', 'Approve invoices for finalisation');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('invoice.upload', 'Upload new invoice files');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('user.create.operator', 'Create new Operator users');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('user.create.support', 'Create new Support users');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('user.create.admin', 'Create new Admin users (requires Owner approval)');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('user.deactivate', 'Deactivate users');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('config.view', 'View system configuration');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('config.write', 'Modify system configuration');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('audit.view', 'View audit logs');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('health.read', 'View health endpoints and diagnostics');
INSERT OR IGNORE INTO permissions (permission_id, description) VALUES
    ('integration.config.write', 'Configure integration settings');

-- ─── Role-Permission Defaults ─────────────────────────────────────

-- Owner: wildcard (all permissions)
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Owner', '*');

-- Admin: view rights + operator/support creation
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'invoice.view');
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'config.view');
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'audit.view');
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'health.read');
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'user.create.operator');
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Admin', 'user.create.support');

-- Operator: no default permissions (all explicitly granted by Admin)

-- Support: health diagnostics only
INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES
    ('Support', 'health.read');
