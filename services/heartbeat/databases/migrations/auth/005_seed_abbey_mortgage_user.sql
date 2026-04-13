-- ============================================================================
-- MIGRATION 005: Seed Abbey Mortgage Bank User
-- Database: auth.db
-- Date: 2026-03-31
--
-- Creates Bob Nzelu as Owner for the Abbey Mortgage tenant.
-- Password: AbbeyMortgage2026! (bcrypt hashed)
-- Matches TENANT_CONFIG_HANDOFF_SPEC.md user section.
-- ============================================================================

-- Insert user (idempotent — ON CONFLICT DO NOTHING)
INSERT OR IGNORE INTO users (
    user_id, tenant_id, email, display_name,
    password_hash, role_id, is_active, is_first_run,
    master_secret, permissions_version,
    created_at, updated_at
) VALUES (
    'user-bob-001',
    'tenant-abbey-001',
    'bob@abbeymortgage.com',
    'Bob Nzelu',
    -- bcrypt hash of 'AbbeyMortgage2026!' (12 rounds)
    '$2b$12$LJ3gxJVKEYXqVAz8HQNvH.q/NZ5JrkWKXLDrfTSzZHrXhDaY3KXGK',
    'Owner',
    1,  -- is_active
    0,  -- is_first_run (already set up)
    'abbey-master-secret-dev-only-not-for-production',
    1,
    datetime('now'),
    datetime('now')
);
