-- 002_seed_test_user.sql
-- Seed development/test user for local HeartBeat testing.
-- User: bob.nzelu@pronalytics.ng / 1234%%% / Owner / helium-dev tenant
--
-- WARNING: This migration is for DEVELOPMENT ONLY.
-- Production environments use the enrollment flow to create users.

INSERT OR IGNORE INTO users (
    user_id,
    tenant_id,
    email,
    display_name,
    password_hash,
    role_id,
    owner_sequence,
    is_active,
    must_reset_password,
    mfa_configured,
    is_first_run,
    created_by,
    created_at,
    updated_at
) VALUES (
    'usr-bob-nzelu-001',
    'helium-dev',
    'bob.nzelu@pronalytics.ng',
    'Bob Nzelu',
    '$2b$12$WKODGSuSwAsltVMr8/2sIORaC/H40z3whEX9V3PKzC5i29fiw7haK',
    'Owner',
    'O-001',
    1,
    0,
    0,
    0,
    'system-seed',
    '2026-02-23T00:00:00Z',
    '2026-02-23T00:00:00Z'
);
