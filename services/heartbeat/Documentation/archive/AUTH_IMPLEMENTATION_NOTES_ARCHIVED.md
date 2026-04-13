# HeartBeat Auth Implementation Notes

**Date:** 2026-02-23
**Scope:** Task 1 (auth.db), Task 2 (login flow), Task 3 (introspection), Session model
**Contract:** HEARTBEAT_SERVICE_CONTRACT_PART4.md

---

## What Was Built

### Task 1: auth.db — Database & Migrations

| File | Purpose |
|---|---|
| `databases/migrations/auth/001_initial_schema.sql` | Standard tier tables + seed data |
| `databases/migrations/auth/002_seed_test_user.sql` | Dev test user (bob.nzelu@pronalytics.ng, Owner) |
| `databases/migrations/auth/003_session_cap_and_pin.sql` | Session hard cap + permissions_version |
| `src/database/auth_migrator.py` | SQLCipher-aware migration runner |
| `src/database/auth_connection.py` | AuthDatabase connection manager |

**Tables created:** roles, permissions, users, role_permissions, user_permissions, sessions, schema_migrations

**Seed data:**
- 4 roles: owner, admin, operator, support
- 13 permissions: wildcard `*` plus 12 named permissions
- Role defaults: Owner gets `*`, Admin gets 6, Support gets health.read, Operator gets none
- Test user: `bob.nzelu@pronalytics.ng` / `1234%%%` / Owner / tenant `helium-dev`

**Migration 003 additions:**
- `sessions.session_expires_at` TEXT — 8-hour hard cap timestamp
- `sessions.last_auth_method` TEXT — "password" (future: "sso")
- `users.permissions_version` INTEGER — incremented on permission changes

**SQLCipher encryption:**
- auth.db encrypted at rest when `HEARTBEAT_AUTH_DB_KEY` is set and `sqlcipher3-binary` is installed
- Falls back to plain sqlite3 with a warning if sqlcipher3 is not available

**Migrator decision:**
- Standalone `AuthDatabaseMigrator` rather than subclassing `DatabaseMigrator`
- Mirrors all logic from DatabaseMigrator but routes connections through SQLCipher

### Task 2: Authlib Login Flow + Session Model

| File | Purpose |
|---|---|
| `src/auth/__init__.py` | Package init |
| `src/auth/jwt_manager.py` | Ed25519 keypair + JWT signing/verification |
| `src/handlers/auth_handler.py` | Business logic: login, refresh, logout, introspect |
| `src/auth/dependencies.py` | FastAPI Depends: token extraction |
| `src/api/auth.py` | Router: 4 endpoints (login, refresh, logout, introspect) |

**JWT details:**
- Algorithm: EdDSA (Ed25519) via Authlib
- Keys stored at `databases/keys/jwt_private.pem` and `jwt_public.pem`
- Generated automatically on first run if not present
- Claims: sub, tenant_id, role, permissions, permissions_version, last_auth_at, issued_at, expires_at, session_expires_at, jti
- Standard exp claim enables Authlib's automatic expiration validation

**Session model:**
- **Short-lived JWT** (30 min default, `HEARTBEAT_JWT_EXPIRY_MINUTES`)
- **Silent refresh** — SDK auto-refreshes every ~25 min (user never sees it)
- **8-hour session hard cap** (`HEARTBEAT_SESSION_HOURS`) — user MUST re-authenticate after this
- `session_expires_at` is immutable — refresh never extends it
- JWT `exp` is capped to `min(now + jwt_expiry_minutes, session_expires_at)`
- **Permission change detection**: `permissions_version` on users table. If DB version != JWT version → session revoked, re-auth required.

**Login flow:**
1. Look up user by email
2. Check user is active
3. Verify bcrypt password (12 rounds)
4. Check first-run state (scope="bootstrap" if true)
5. Build JWT payload with permissions + permissions_version
6. Sign JWT with Ed25519 private key
7. Create session with session_expires_at hard cap
8. Stamp last_login_at

**Refresh flow:**
- Verify current JWT signature + expiration
- Look up session by jti, check not revoked
- Check session hard cap → SESSION_EXPIRED if exceeded
- Verify user still active
- Check permissions_version → PERMISSIONS_CHANGED if differs
- Issue new JWT (capped to session_expires_at)
- Update session jti

**Logout flow:**
- Decode token unsafely (even expired tokens accepted)
- Revoke session by jti

### Task 3: Token Introspection

| File | Purpose |
|---|---|
| `src/auth/dependencies.py` | FastAPI Depends: service credential validation |
| `src/handlers/auth_handler.py` | introspect_token() logic |
| `src/api/auth.py` | Router: POST /introspect |

**Introspection checks (in order):**
1. Verify JWT signature + expiration
2. Check session exists and is not revoked (by jti)
3. Check session hard cap (session_expires_at)
4. Check user is still active
5. Check first-run state → `FIRST_RUN_REQUIRED`
6. Check permissions_version → `PERMISSIONS_CHANGED`
7. Build response with fresh permissions from DB
8. Check required_permission (wildcard `*` matches all)
9. Check step-up freshness via `last_auth_at` elapsed time

### PIN — Float/SDK-Level (NOT HeartBeat)

PIN is entirely a Float-side concept. HeartBeat does not store, verify, or know about PINs. Float handles PIN locally for:
- App-level screen lock / idle timeout
- Quick local gate for step-up prompts
- UX convenience before password re-auth

See `Documentation/FLOAT_SDK_AUTH_INTEGRATION.md` Section 3 for Float PIN implementation guidance.

---

## Files Created

| File | Purpose |
|---|---|
| `databases/migrations/auth/001_initial_schema.sql` | Core auth tables + seed roles/permissions |
| `databases/migrations/auth/002_seed_test_user.sql` | Dev test user |
| `databases/migrations/auth/003_session_cap_and_pin.sql` | Session hard cap + permissions_version |
| `src/auth/__init__.py` | Package init |
| `src/auth/jwt_manager.py` | Ed25519 JWT signing/verification |
| `src/auth/dependencies.py` | FastAPI Depends for auth |
| `src/handlers/auth_handler.py` | All auth business logic |
| `src/database/auth_migrator.py` | SQLCipher-aware migration runner |
| `src/api/auth.py` | Auth router (4 endpoints) |
| `Documentation/AUTH_IMPLEMENTATION_NOTES.md` | This file |
| `Documentation/FLOAT_SDK_AUTH_INTEGRATION.md` | Float/SDK team integration guide |

## Files Modified (Existing)

| File | Change |
|---|---|
| `src/config.py` | Added auth_db_path, auth_db_key, jwt_private/public_key_path, session_hours, jwt_expiry_minutes |
| `src/main.py` | Auth router registration, auth.db init + migration, JWT manager init, shutdown resets |
| `src/api/__init__.py` | Added auth_router export |
| `src/database/auth_connection.py` | Updated create_session() with session_expires_at param |
| `requirements.txt` | Added authlib>=1.3.0, cryptography>=41.0.0, sqlcipher3-binary>=0.5.0 |
| `Documentation/HEARTBEAT_SERVICE_CONTRACT_PART4.md` | Harmonized schema_migrations table |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `HEARTBEAT_AUTH_DB_PATH` | `databases/auth.db` | Path to auth database |
| `HEARTBEAT_AUTH_DB_KEY` | (empty) | SQLCipher encryption key |
| `HEARTBEAT_JWT_PRIVATE_KEY_PATH` | `databases/keys/jwt_private.pem` | Ed25519 private key |
| `HEARTBEAT_JWT_PUBLIC_KEY_PATH` | `databases/keys/jwt_public.pem` | Ed25519 public key |
| `HEARTBEAT_SESSION_HOURS` | 8 | Session hard cap (hours) |
| `HEARTBEAT_JWT_EXPIRY_MINUTES` | 30 | JWT short-lived expiry (minutes) |

---

## Error Codes

| Code | HTTP | When |
|---|---|---|
| `TOKEN_INVALID` | 401 | Bad credentials, expired/invalid JWT, missing auth header |
| `TOKEN_REVOKED` | 401 | Session explicitly revoked |
| `SESSION_EXPIRED` | 401 | 8-hour session hard cap reached |
| `PERMISSIONS_CHANGED` | 401 | User's permissions_version changed since JWT was issued |
| `FIRST_RUN_REQUIRED` | — | Introspect: user hasn't completed first-run setup |
| `PERMISSION_DENIED` | — | Introspect: user lacks required permission |
| `STEP_UP_REQUIRED` | — | Introspect: last_auth_at too old for step-up window |

---

## Design Decisions

1. **Standalone AuthDatabaseMigrator**: Mirrors DatabaseMigrator rather than subclassing, because auth.db needs SQLCipher encryption while other databases use plain sqlite3.

2. **schema_migrations harmonized**: Uses `INTEGER version` + `filename` + `description` + `execution_time_ms` to match existing blob.db/registry.db migrator pattern.

3. **JWT dual timestamps**: Both ISO strings (issued_at, expires_at) for Part 4 compliance and UNIX timestamps (iat, exp) for Authlib's automatic expiration validation.

4. **Permissions refreshed from DB**: Both refresh_token and introspect_token re-query permissions from auth.db rather than trusting JWT claims.

5. **Logout accepts expired tokens**: `decode_token_unsafe()` skips expiration validation so users can always revoke their session.

6. **First-run bootstrap token**: Login sets `scope: "bootstrap"` for first-run users. Introspect returns `FIRST_RUN_REQUIRED`.

7. **Short JWT + Session hard cap**: JWT expires every 30 min with silent refresh. Session has 8-hour immutable hard cap. After 8 hours → forced password re-auth. No silent extension.

8. **Permission change detection**: `permissions_version` integer on users table. JWT carries the version. If DB version differs → session revoked, re-auth forced. Immediate effect.

9. **PIN is Float-level, not HeartBeat**: PIN is an app-level security gate handled entirely by Float/SDK. HeartBeat only authenticates with passwords. This keeps HeartBeat's auth surface clean and lets Float control its own UX.
