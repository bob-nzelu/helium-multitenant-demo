-- HeartBeat Auth Schema v1.0
-- PostgreSQL DDL for auth schema
-- Date: 2026-03-03
-- Reference: AUTH_SERVICE_CONTRACT.md Section 13

-- Roles table (must be first, users references it)
CREATE TABLE IF NOT EXISTS auth.roles (
    role_id     TEXT PRIMARY KEY,
    description TEXT
);

-- Permissions table
CREATE TABLE IF NOT EXISTS auth.permissions (
    permission_id   TEXT PRIMARY KEY,
    description     TEXT
);

-- Users table
CREATE TABLE IF NOT EXISTS auth.users (
    user_id             TEXT PRIMARY KEY,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT,
    display_name        TEXT NOT NULL,
    role_id             TEXT NOT NULL REFERENCES auth.roles(role_id),
    tenant_id           TEXT NOT NULL,
    is_active           BOOLEAN DEFAULT TRUE,
    is_first_run        BOOLEAN DEFAULT TRUE,
    must_reset_password BOOLEAN DEFAULT FALSE,
    mfa_configured      BOOLEAN DEFAULT FALSE,
    permissions_version INTEGER DEFAULT 1,
    master_secret       TEXT NOT NULL,
    last_login_at       TIMESTAMP WITH TIME ZONE,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Role-permission mapping
CREATE TABLE IF NOT EXISTS auth.role_permissions (
    role_id         TEXT REFERENCES auth.roles(role_id),
    permission_id   TEXT REFERENCES auth.permissions(permission_id),
    PRIMARY KEY (role_id, permission_id)
);

-- Per-user permission overrides
CREATE TABLE IF NOT EXISTS auth.user_permissions (
    user_id         TEXT REFERENCES auth.users(user_id),
    permission_id   TEXT REFERENCES auth.permissions(permission_id),
    granted         BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (user_id, permission_id)
);

-- Sessions table
CREATE TABLE IF NOT EXISTS auth.sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES auth.users(user_id),
    jwt_jti             TEXT NOT NULL,
    issued_at           TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    session_expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    last_auth_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    last_refreshed      TIMESTAMP WITH TIME ZONE,
    last_auth_method    TEXT DEFAULT 'password',
    is_revoked          BOOLEAN DEFAULT FALSE,
    revoked_at          TIMESTAMP WITH TIME ZONE,
    revoked_reason      TEXT,
    device_fingerprint  TEXT,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Password history (recycling prevention)
CREATE TABLE IF NOT EXISTS auth.password_history (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES auth.users(user_id),
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Schema migrations tracking
CREATE TABLE IF NOT EXISTS auth.schema_migrations (
    version             INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    checksum            TEXT NOT NULL,
    applied_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    execution_time_ms   INTEGER
);

-- Step-up policies (operation tier definitions)
CREATE TABLE IF NOT EXISTS auth.step_up_policies (
    operation               TEXT PRIMARY KEY,
    tier                    TEXT NOT NULL CHECK (tier IN ('routine', 'pin_only', 'auth', 'immediate')),
    required_within_seconds INTEGER NOT NULL DEFAULT 3600,
    description             TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON auth.sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_jwt_jti ON auth.sessions(jwt_jti);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON auth.sessions(user_id, is_revoked) WHERE is_revoked = FALSE;
CREATE INDEX IF NOT EXISTS idx_password_history_user ON auth.password_history(user_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON auth.users(email);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON auth.users(tenant_id);
