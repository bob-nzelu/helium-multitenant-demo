# Unified Auth Contract — Helium Frontend Apps

**Version:** 1.0
**Date:** 14 April 2026
**Status:** Design — pending implementation in dedicated session
**Scope:** All Helium frontend apps (Float, Transforma Reader, Reader Mobile, Monitoring)

---

## INSTRUCTIONS FOR DEDICATED SESSION

**Before writing any code or plan, you MUST:**
1. Read this ENTIRE document
2. Read HeartBeat's existing auth implementation:
   - `services/heartbeat/src/api/auth.py` — current endpoints
   - `services/heartbeat/src/handlers/auth_handler.py` — business logic
   - `services/heartbeat/src/database/pg_auth.py` — database operations
   - `services/heartbeat/src/auth/jwt_manager.py` — Ed25519 JWT signing
   - `services/heartbeat/databases/migrations/auth/001_initial_schema.sql` — schema
3. Read Float's existing auth:
   - `Float/App/src/sdk/auth/auth_provider.py`
   - `Float/App/src/sdk/auth/models.py`
4. Read Reader's login spec:
   - Check memory: `project_login_dialog_spec.md`
5. Ask the user ALL clarifying questions before planning

**Live infrastructure:** EC2 13.247.224.147
- HeartBeat: port 9000 (currently running with HEARTBEAT_MOCK_AUTH=true)
- PostgreSQL: port 5432 (auth schema seeded, Charles Omoakin user exists)
- Mock auth user: Charles.Omoakin@abbeymortgagebank.com / 123456

---

## 1. CORE PRINCIPLE

**One user, one machine, one session.** All Helium frontend apps on the same physical machine share a single authenticated session. Different machines get separate sessions. Maximum 3 concurrent sessions per user across all devices.

---

## 2. DEVICE IDENTITY

### Device Registration

Every machine registers with HeartBeat once (on first run of ANY Helium app). The `device_id` is derived from the **machine**, not the app:

```
device_id = SHA256(machine_guid + ":" + primary_mac_address)[:16]
```

- **Windows:** `machine_guid` from registry `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`
- **macOS:** `IOPlatformUUID` from IOKit
- **Linux:** `/etc/machine-id`
- **iOS:** `identifierForVendor`
- **Android:** `ANDROID_ID`

### Registration Endpoint

```
POST /api/auth/register-device
{
  "device_id": "a1b2c3d4e5f60001",
  "machine_guid": "ABC123-DEF456-...",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "computer_name": "PROBOOK",
  "os_type": "windows",
  "os_version": "Windows 11 Pro 10.0.26100",
  "app_type": "float",
  "app_version": "2.0.0"
}
```

**Response:**
```json
{
  "device_id": "a1b2c3d4e5f60001",
  "status": "registered",
  "registered_at": "2026-04-14T10:00:00Z"
}
```

**Idempotent:** If device_id already exists, HeartBeat updates the metadata (os_version, app_version, last_seen) but does NOT create a new registration.

### Device Table (PostgreSQL)

```sql
CREATE TABLE IF NOT EXISTS auth.devices (
    device_id       TEXT PRIMARY KEY,
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
```

---

## 3. LOGIN FLOW

### Standard Login (all apps, all devices)

```
POST /api/auth/login
{
  "email": "Charles.Omoakin@abbeymortgagebank.com",
  "password": "123456",
  "device_id": "a1b2c3d4e5f60001"
}
```

**HeartBeat logic:**
1. Verify email + password (bcrypt)
2. Check user is active, device is not revoked
3. Check concurrent session count for this user:
   - If < 3 active sessions → proceed
   - If = 3 active sessions → revoke the OLDEST session → proceed
4. Check if this device already has an active session:
   - If yes → revoke it (replace) → create new session
   - If no → create new session
5. Create session in `auth.sessions` with `device_id`
6. Issue Ed25519-signed JWT with device_id in claims
7. Derive cipher_text from master_secret + time window
8. Return response

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "cipher_text": "a1b2c3d4...",
  "expires_at": "2026-04-14T10:30:00Z",
  "session_expires_at": "2026-04-14T18:00:00Z",
  "user": {
    "user_id": "usr-abbey-owner-001",
    "role": "Owner",
    "display_name": "Charles Omoakin",
    "tenant_id": "tenant-abbey-001",
    "is_first_run": true
  },
  "device_id": "a1b2c3d4e5f60001"
}
```

### First-Time Login (is_first_run = true)

Same as existing flow:
1. JWT has `scope: "bootstrap"`
2. App MUST show password change dialog
3. User sets new password via `POST /api/auth/password/change`
4. All sessions revoked → user re-authenticates with new password

---

## 4. SAME-MACHINE SESSION SHARING

### How It Works

On a single machine (e.g., PROBOOK), both Float and Reader share the same `device_id` because it's derived from `machine_guid`. They share a session via the **OS Keyring**.

**Keyring entry:**
- **Service:** `helium`
- **Account:** `{user_id}` (e.g., `usr-abbey-owner-001`)
- **Password (value):** JSON string:

```json
{
  "access_token": "eyJ...",
  "cipher_text": "a1b2c3d4...",
  "expires_at": "2026-04-14T10:30:00Z",
  "session_expires_at": "2026-04-14T18:00:00Z",
  "device_id": "a1b2c3d4e5f60001",
  "heartbeat_url": "http://13.247.224.147:9000",
  "user_id": "usr-abbey-owner-001",
  "tenant_id": "tenant-abbey-001",
  "role": "Owner",
  "display_name": "Charles Omoakin",
  "updated_at": "2026-04-14T10:00:00Z"
}
```

### Startup Sequence (any Helium app)

```
1. Compute device_id from machine_guid + mac_address
2. Check Keyring: does "helium" / {any_user_id} entry exist?
   ├── NO entry → show login dialog
   └── YES entry →
       3. Parse JSON from Keyring
       4. Check: is expires_at > now?
          ├── NO (token expired) →
          │   5. Check: is session_expires_at > now?
          │   ├── NO (session expired) → delete Keyring entry → show login
          │   └── YES (session alive, token just expired) →
          │       6. Call POST /api/auth/token/refresh with old token
          │       7. Update Keyring with new token
          │       8. Proceed to main window
          └── YES (token valid) →
              8. Proceed to main window
```

### Token Refresh (any app)

When ANY app on the machine refreshes the token:
1. Call `POST /api/auth/token/refresh`
2. Receive new JWT + cipher_text
3. Update the Keyring entry
4. Other apps on the same machine pick up the new token on their next Keyring read

**No file watchers needed.** Each app reads the Keyring when it needs the token (before API calls). If the token was refreshed by the other app, it gets the fresh one.

### Logout (any app)

1. Call `POST /api/auth/logout`
2. Delete Keyring entry
3. Other app detects missing Keyring → shows login dialog on next action

---

## 4B. AUTH METHOD PER CALLER

**CRITICAL DECISION: Frontend apps NEVER receive HMAC credentials.**

JWT for humans (frontend apps). HMAC for machines (ERP, Simulator).

| Caller | → Service | Auth Method | Why |
|--------|-----------|------------|-----|
| Float → HeartBeat | JWT | User-scoped, revocable |
| Float → Relay | **JWT** | User-scoped (Relay introspects via HeartBeat) |
| Float → Core | JWT | User-scoped |
| Float → Edge | JWT | User-scoped |
| Float → HIS | JWT | User-scoped |
| Reader → HeartBeat | JWT | Shared session from Keyring |
| Reader → Relay | **JWT** | Same — no HMAC exposure |
| Reader → Core | JWT | User-scoped |
| Reader → SIS | JWT | User-scoped |
| ERP system → Relay | **HMAC** | Machine identity, no human user |
| Simulator → Relay | **HMAC** | Test tool, tenant-level |
| Core → HeartBeat | Service token | Internal, service-to-service |
| Core → Edge | Service token | Internal |
| Core → HIS | Service token | Internal |

**Relay accepts BOTH JWT and HMAC.** When a request arrives:
1. Check for `Authorization: Bearer {jwt}` → if present, verify via HeartBeat introspect → extract tenant_id from claims
2. Else check for `X-API-Key` + `X-Signature` → HMAC flow (existing)
3. Neither → 401

**Registration response does NOT include relay_credentials for frontend apps.** Only machine integrations (ERP, Simulator) receive HMAC credentials, and those are provisioned by Admin_Packager — not through the register-app endpoint.

---

## 5. JWT CLAIMS

```json
{
  "sub": "usr-abbey-owner-001",
  "tenant_id": "tenant-abbey-001",
  "role": "Owner",
  "permissions": ["*"],
  "permissions_version": 1,
  "device_id": "a1b2c3d4e5f60001",
  "last_auth_at": "2026-04-14T10:00:00Z",
  "iat": 1713088800,
  "exp": 1713090600,
  "iss": "helium-heartbeat",
  "jti": "tok-abc123def456",
  "alg": "EdDSA"
}
```

**New claim:** `device_id` — ties the JWT to a specific machine. If a JWT is used from a different device_id, HeartBeat rejects it.

---

## 6. SESSION MODEL

### Session Table Changes

Add `device_id` to `auth.sessions`:

```sql
ALTER TABLE auth.sessions ADD COLUMN device_id TEXT;
CREATE INDEX idx_sessions_device ON auth.sessions(device_id) WHERE device_id IS NOT NULL;
```

### Concurrency Rules

| Rule | Value | Enforcement |
|------|-------|-------------|
| Max sessions per user | 3 | At login: if count >= 3, revoke oldest |
| Max sessions per device | 1 | At login: if device already has session, replace it |
| Session hard cap | 8 hours | `session_expires_at` — immutable from login |
| JWT lifetime | 30 minutes | `expires_at` — refreshable within session cap |
| Refresh window | 25 minutes | Client-side timer triggers silent refresh |

### Session Lifecycle

```
LOGIN → session created (ACTIVE)
  ↓
REFRESH (every 25 min) → new JWT, same session
  ↓
8 HOURS → session expired (EXPIRED) → must re-authenticate
  ↓
LOGOUT → session revoked (REVOKED)
  ↓
4TH DEVICE LOGIN → oldest session evicted (EVICTED)
```

---

## 7. MOBILE CONSIDERATIONS (Future)

Mobile apps follow the same contract with these adaptations:

| Aspect | Desktop | Mobile |
|--------|---------|--------|
| device_id source | machine_guid + mac | ANDROID_ID / identifierForVendor |
| Token storage | OS Keyring (Credential Manager) | iOS Keychain / Android Keystore |
| Session sharing | Same machine = shared | Per device (phones don't share) |
| Biometric auth | Not applicable | Future: fingerprint/face → step-up |
| Push notifications | Not applicable | Future: session expiry warnings |
| Background refresh | App timer (25 min) | Platform-specific background task |

---

## 8. TEST HARNESS INTEGRATION

The test harness (`~/.helium/test_harness_key`) is **orthogonal** to user auth. It provides privileged operations (reset auth, seed data) that work regardless of user session state.

Test harness requests include `X-Test-Harness-Signature` header. HeartBeat validates the HMAC separately from JWT validation. A request can have:
- JWT only → normal user request
- Test harness signature only → privileged tool request (no user context)
- Both → privileged user request (rare, for admin tools)

---

## 9. ENDPOINTS SUMMARY

### Existing (already in HeartBeat):
| Endpoint | Change |
|----------|--------|
| `POST /api/auth/login` | Add `device_id` to request + response |
| `POST /api/auth/token/refresh` | No change (device_id in JWT) |
| `POST /api/auth/logout` | No change |
| `POST /api/auth/introspect` | Add `device_id` to response |
| `POST /api/auth/stepup` | No change |
| `POST /api/auth/password/change` | No change |

### New:
| Endpoint | Purpose |
|----------|---------|
| `POST /api/auth/register-device` | Register a machine with HeartBeat |
| `GET /api/auth/devices` | List registered devices for current user |
| `POST /api/auth/devices/{device_id}/revoke` | Admin: revoke a specific device |
| `GET /api/auth/sessions` | List active sessions for current user |

---

## 10. MIGRATION PATH

### Phase 1: HeartBeat Changes
1. Add `auth.devices` table
2. Add `device_id` column to `auth.sessions`
3. Update login handler to accept + validate device_id
4. Update session creation to enforce 3-session limit
5. Add device registration endpoint
6. Switch `HEARTBEAT_MOCK_AUTH=false`

### Phase 2: Float SDK Changes
1. Compute device_id on startup (machine_guid + mac)
2. Register device on first run
3. Include device_id in login request
4. Write session to Keyring with device_id
5. Read Keyring on startup → skip login if valid session exists

### Phase 3: Reader SDK Changes
1. Same device_id computation as Float (same machine = same ID)
2. On startup: check Keyring for existing session
3. If found + valid → skip login → use shared session
4. If not found → show login → write to Keyring

### Phase 4: Mobile (Future)
1. Platform-specific device_id
2. Platform-specific secure storage
3. Background refresh handling

---

## 11. KEY FILES TO MODIFY

### HeartBeat:
- `src/api/auth.py` — add device_id to login, add register-device endpoint
- `src/handlers/auth_handler.py` — session limit logic, device validation
- `src/database/pg_auth.py` — devices table CRUD, session queries with device_id
- `databases/migrations/auth/007_devices_and_session_limits.sql` — schema changes

### Float SDK:
- `src/sdk/auth/auth_provider.py` — device_id computation, Keyring read/write
- `src/sdk/auth/models.py` — add device_id to session model

### Reader:
- Auth module (matches Float pattern) — device_id, Keyring sharing
