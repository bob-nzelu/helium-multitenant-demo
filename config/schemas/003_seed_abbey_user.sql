-- ============================================================================
-- Seed Abbey Mortgage Owner: Charles Omoakin
-- Password: 123456 (first-time, forced change on first login)
-- ============================================================================

INSERT INTO auth.users (
    user_id, tenant_id, email, display_name,
    password_hash, role_id, is_active, is_first_run,
    master_secret, permissions_version,
    created_at, updated_at
) VALUES (
    'usr-abbey-owner-001',
    'tenant-abbey-001',
    'Charles.Omoakin@abbeymortgagebank.com',
    'Charles Omoakin',
    '$2b$12$7kNDbk5.rp.4rPefs1itHORRsi/XIRcW54MW2SJDK7.HVEk/TGPVG',
    'Owner',
    TRUE,
    TRUE,
    '4a8f3c2d9e1b7056a3d2f8c4e9b1a5d7f2c8e4a1b5d9f3c7e2a6b0d4f8c1e5a9',
    1,
    NOW(),
    NOW()
) ON CONFLICT (user_id) DO NOTHING;
