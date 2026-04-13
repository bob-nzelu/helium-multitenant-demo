-- Migration 006: Add require_2fa to tenant_config, last_login_at to float_users
-- Date: 2026-03-31
-- Reason: Per-tenant 2FA toggle (default false) and user session tracking

-- R1: Add require_2fa column to tenant_config
-- Default false — PIN-only remains the default authentication mode.
-- When true, Float must prompt for a second factor after PIN verification.
ALTER TABLE tenant_config ADD COLUMN require_2fa INTEGER DEFAULT 0;

-- R2: Add last_login_at to float_users
-- Populated by HeartBeat on successful authentication.
-- Delivered to Float via FloatConfigResponse.user.last_login_at.
-- Useful for security timeout decisions (dormant session detection).
ALTER TABLE float_users ADD COLUMN last_login_at TEXT;
