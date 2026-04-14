# HeartBeat Auth + Registration — AWS Session Handoff (v2)

**Date:** 14 April 2026
**For:** Dedicated HeartBeat_AWS session
**Repo:** `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo`
**Supersedes:** Previous version of this file (v1, same date)

---

## INSTRUCTIONS

**Before ANY code or plan, you MUST:**
1. Read `docs/HELIUM_DEPLOYMENT_ARCHITECTURE.md` (master architecture — start here)
2. Read `docs/UNIFIED_AUTH_CONTRACT.md` (auth decisions)
3. Read this entire document
4. Read the **Transforma Reader** codebase for auth patterns:
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\src\services\auth_service.py` (AuthManager, observer pattern)
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\src\services\auth.py` (PINManager)
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\src\services\session.py` (DPAPI session, shared with Float)
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\src\clients\heartbeat_client.py` (Reader → HeartBeat calls)
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\src\ui\login_page.py` (login UI)
5. Read **Float's** auth:
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Float\App\src\sdk\auth\auth_provider.py` (JWT validation)
6. Read HeartBeat's existing auth code:
   - `services/heartbeat/src/api/auth.py` (endpoints)
   - `services/heartbeat/src/handlers/auth_handler.py` (business logic)
   - `services/heartbeat/src/database/pg_auth.py` (PostgreSQL ops)
   - `services/heartbeat/src/auth/jwt_manager.py` (Ed25519 signing)
7. Ask the user ALL clarifying questions — painstakingly
8. Understand the **test harness** (elevated user flow, `~/.helium/test_harness_key`)
9. Understand **multi-tenancy** (single PG, tenant_id scoping, demo vs production)

---

## CONTEXT: WHY THIS MATTERS

HeartBeat's auth system serves EVERY frontend app in the ecosystem:
- **Float** (desktop, PySide6) — bulk uploads, invoice management
- **Transforma Reader** (desktop, PySide6) — PDF viewing, single-file submission
- **Reader Mobile** (future) — field workers, approval flows
- **Monitoring** (future) — dashboards, alerts

These apps share sessions on the same machine, register independently with HeartBeat, and all use JWT for service calls. HeartBeat is the single auth authority.

**Reader already has auth code** (AuthManager + PINManager + DPAPI session sharing). It talks to HeartBeat for login, refresh, and duplicate checks. The contract you implement here MUST work with Reader's existing `heartbeat_client.py` — don't break its expected response shapes.

---

## LIVE INFRASTRUCTURE

EC2 13.247.224.147 — all services running and healthy.

**SSH:** `ssh -i "C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\AB Microfinance\helium-key.pem" ubuntu@13.247.224.147`

**Deploy:** `git push` → SSH → `cd helium-multitenant-demo && git pull && sudo docker compose build heartbeat && sudo docker compose up -d heartbeat`

**Current auth state:** `HEARTBEAT_MOCK_AUTH=true` (mock returns hardcoded Charles Omoakin responses)

**PostgreSQL auth schema:** Seeded with roles, permissions, Charles Omoakin user (password: `123456`, is_first_run: true)

---

## TASKS (ordered by dependency)

### Task 1: Switch to Real Auth

Set `HEARTBEAT_MOCK_AUTH=false` in docker-compose.yml. The real auth code is fully built — it just needs to be activated.

**Verify:**
```bash
curl -X POST http://13.247.224.147:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"Charles.Omoakin@abbeymortgagebank.com","password":"123456"}'
```

**Expected:** Real Ed25519 JWT, `is_first_run: true`, cipher_text.

**Rollback:** Set `HEARTBEAT_MOCK_AUTH=true` if anything breaks.

### Task 2: Add device_id to Login + Sessions

**Schema change** (`config/schemas/004_devices.sql`):
```sql
CREATE TABLE IF NOT EXISTS auth.devices (
    device_id       TEXT PRIMARY KEY,
    user_id         TEXT,
    machine_guid    TEXT NOT NULL,
    mac_address     TEXT,
    computer_name   TEXT,
    os_type         TEXT NOT NULL,
    os_version      TEXT,
    last_app_type   TEXT,
    last_app_version TEXT,
    last_seen_at    TIMESTAMPTZ,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT
);

ALTER TABLE auth.sessions ADD COLUMN IF NOT EXISTS device_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_device ON auth.sessions(device_id);
```

**Login change** (`auth_handler.py`): Accept optional `device_id` in request. Include in JWT claims. Store in session row.

### Task 3: 3-Session Limit

In `auth_handler.py` login():
1. Count active sessions for user: `SELECT COUNT(*) FROM auth.sessions WHERE user_id=$1 AND is_revoked=false AND session_expires_at > NOW()`
2. If >= 3: revoke oldest (`ORDER BY issued_at ASC LIMIT 1`)
3. If device already has active session: revoke it (replace)

### Task 4: App Registration Endpoint

**New endpoint:** `POST /api/auth/register-app`

See `HELIUM_DEPLOYMENT_ARCHITECTURE.md` Section 4 for full request/response contract.

**Implementation:**
- Create `auth.app_registrations` table (source_id, source_type, source_name, device_id, tenant_id, app_version, registered_at)
- Generate source_id: `src-{source_type[:5]}-{device_id[:6]}-{sequence}`
- Return tenant config + endpoints + capabilities + feature flags + security settings
- Idempotent: same device_id + source_type → update last_seen, return existing source_id

**Tenant config source:** HeartBeat's config.db / config_entries table. The response merges:
- Tenant info from auth.users (tenant_id) → config_entries
- Endpoints from config_entries (service_name → URL)
- Capabilities from tier_limits (tenant tier → limits)
- Feature flags from feature_flags table

### Task 5: Device + Session Management Endpoints

```
GET  /api/auth/devices                — list registered devices for current user
POST /api/auth/devices/{id}/revoke    — admin: revoke a device
GET  /api/auth/sessions               — list active sessions for current user
```

### Task 6: Test Harness Security Model

1. Generate key: `python -c "import secrets; open('test_harness_key','wb').write(secrets.token_bytes(32))"` → store at `~/.helium/test_harness_key` on Bob's laptop
2. Create `services/heartbeat/src/auth/test_harness_manager.py`:
   - Load key from env `HEARTBEAT_TEST_HARNESS_KEY_HASH` (SHA-256 of the key)
   - Validate HMAC signature: `X-Test-Harness-Signature` header
   - Constant-time comparison via `hmac.compare_digest()`
3. Create `services/heartbeat/src/api/test_harness/endpoints.py`:
   - `POST /api/test/auth/reset` — reset user to first-time login
   - `POST /api/test/auth/create-user` — create test user on the fly
   - `POST /api/test/data/seed` — seed sample data
   - `POST /api/test/data/clear` — wipe tenant data (logged)
   - `POST /api/test/sse/emit` — push custom SSE event
   - `POST /api/test/config/override` — temporarily override config
   - `GET /api/test/state` — dump system state for debugging
4. Register conditionally: `HEARTBEAT_TEST_HARNESS_ENABLED=true`
5. All test operations are audit-logged

### Task 7: Update Engine (Foundation)

Add update management endpoints:
```
POST /api/admin/updates/apply     — upload + apply update package
GET  /api/admin/updates/status    — current update progress  
GET  /api/admin/updates/history   — past updates
POST /api/admin/updates/rollback  — rollback to previous version
```

This is the foundation — full implementation is a separate session, but the endpoints should exist (can return 501 for now except /history which returns empty list).

---

## READER HARMONY: WHAT NOT TO BREAK

Reader's `heartbeat_client.py` expects these response shapes:

**Login response:**
```json
{
  "access_token": "...",
  "cipher_text": "...",    // Reader uses this for DPAPI
  "user": {
    "user_id": "...",
    "role": "...",
    "display_name": "...",
    "tenant_id": "...",
    "is_first_run": true
  }
}
```

**Password change response:**
Reader expects `200 OK` with any body. On success, Reader clears local session and re-shows login.

**Token refresh:**
Reader calls `POST /api/auth/refresh` (note: Reader uses `/refresh`, Float uses `/token/refresh`). HeartBeat should accept BOTH paths.

**Duplicate check:**
Reader calls `GET /api/v1/heartbeat/blob/{hash}/status`. This is a HeartBeat blob endpoint, not auth — but ensure it works alongside auth changes.

---

## DPAPI SESSION SHARING (Reader ↔ Float)

**Reader checks three locations for existing auth:**
1. `~/.transforma/session.token.enc` (Reader's own DPAPI session)
2. `C:\ProgramData\Helium\sessions\{user}.token.enc` (shared with Float)
3. HeartBeat login (if neither found)

**Float writes to location 2.** Reader reads from it. If found + valid → Reader skips login entirely.

**HeartBeat implications:** The session token in these files must match what HeartBeat considers valid. When HeartBeat revokes a session (logout, password change, eviction), the DPAPI file becomes stale. Reader's next API call will get 401, triggering re-login.

---

## MULTI-TENANCY NOTES

**Demo (current AWS):** Multiple tenants in one PostgreSQL. HeartBeat auth tables have tenant_id. Login returns tenant_id in JWT. All downstream services scope by it.

**Production (tenant-controlled):** One HeartBeat per tenant. tenant_id is constant. Auth tables still have it (for consistency) but there's only one value.

**HeartBeat must work in both modes.** Don't hardcode tenant_id assumptions. Always read from the user's auth record.

---

## INSTALLER AWARENESS

When a tenant deploys Helium:
1. Installer runs `config/schemas/002_auth_schema.sql` + `003_seed_abbey_user.sql`
2. HeartBeat starts, picks up the seeded user
3. First Owner logs in with temp password → forced change
4. Owner uses Float Admin to create additional users (Admin, Operator, Support)

HeartBeat's auth system must support this bootstrap flow. The `is_first_run` flag and `scope: "bootstrap"` JWT are critical for the Installer experience.

---

## VERIFICATION CHECKLIST

1. Real login → Ed25519 JWT with device_id in claims
2. First-time flow → is_first_run=true → password change → re-login → is_first_run=false
3. Token refresh → new JWT, same session_expires_at
4. 3-session limit → 4th device evicts oldest
5. App registration → source_id + config bundle returned
6. Test harness → HMAC-signed reset call works
7. Simulator still works → HMAC to Relay, no HeartBeat auth involvement
8. Reader login response shape → matches existing heartbeat_client.py expectations
9. Reader `/refresh` path → works (alias for `/token/refresh`)
10. DPAPI session file → valid JWT recognized by HeartBeat introspect
