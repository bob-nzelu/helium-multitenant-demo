-- 003_session_cap_and_pin.sql
-- Add session hard cap (session_expires_at) and permission change detection.
--
-- Session model: JWT expires every 30 min (silent refresh), but the SESSION
-- has an 8-hour hard cap from login time. After 8 hours, user must re-authenticate.
--
-- permissions_version: incremented when a user's permissions change.
-- JWT carries the version at issue time. If DB version differs on refresh/introspect,
-- session is revoked and re-auth is forced.
--
-- PIN is a Float/SDK-level concept — HeartBeat does not store or verify PINs.

-- Add session hard cap to sessions table
ALTER TABLE sessions ADD COLUMN session_expires_at TEXT;

-- Add auth_method tracking to sessions (password, future: sso)
ALTER TABLE sessions ADD COLUMN last_auth_method TEXT NOT NULL DEFAULT 'password';

-- Add permissions_version to users (for forced re-auth on permission changes)
ALTER TABLE users ADD COLUMN permissions_version INTEGER NOT NULL DEFAULT 1;
