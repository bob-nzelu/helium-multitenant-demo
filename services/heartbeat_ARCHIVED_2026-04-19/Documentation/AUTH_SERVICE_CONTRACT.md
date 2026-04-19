# HeartBeat Auth Service — Canonical Contract

**Document:** AUTH_SERVICE_CONTRACT
**Version:** 1.0
**Date:** 2026-03-03
**Status:** AUTHORITATIVE — canonical reference for all auth implementation
**Supersedes:** Part 4 auth sections (Part 4 remains valid for non-auth topics like tenancy, license)
**Audience:** SDK team, HeartBeat team, Core team, Relay team
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## 1. Overview

The Auth component of HeartBeat is the sole identity authority for the Helium platform. It authenticates users, issues JWTs, manages sessions, delivers cipher text for sync.db encryption, and provides token introspection for service-to-service verification.

**All services fail closed when HeartBeat is unreachable.** No HeartBeat = no operations.

---

## 2. Endpoint Summary

| # | Endpoint | Method | Status | Auth Required |
|---|---|---|---|---|
| 1 | `/api/auth/login` | POST | BUILT | None |
| 2 | `/api/auth/token/refresh` | POST | BUILT | Bearer JWT |
| 3 | `/api/auth/logout` | POST | BUILT | Bearer JWT (accepts expired) |
| 4 | `/api/auth/introspect` | POST | BUILT | Bearer {api_key}:{api_secret} |
| 5 | `/api/auth/password/change` | POST | BUILT | Bearer JWT |
| 6 | `/api/auth/stepup` | POST | TO BUILD | Bearer JWT |
| 7 | `/api/auth/operations/{op}/policy` | GET | TO BUILD | Bearer {api_key}:{api_secret} |
| 8 | `/api/sse/stream` | GET | TO BUILD | Bearer JWT |

---

## 3. JWT Specification

### 3.1 Algorithm

**EdDSA (Ed25519)** — canonical, non-negotiable.

- HeartBeat signs JWTs with the Ed25519 private key (`databases/keys/jwt_private.pem`)
- All verifiers use the Ed25519 public key (`databases/keys/jwt_public.pem`)
- Key pair generated at HeartBeat first-run
- SDK AuthProvider configured with `JWT_ED25519_PUBLIC_KEY_PATH` or `JWT_ED25519_PUBLIC_KEY`

### 3.2 Claims

```json
{
  "sub": "usr-abc123",
  "tenant_id": "abbey-001",
  "role": "admin",
  "permissions": ["invoice.view", "invoice.approve", "blob.upload"],
  "permissions_version": 1,
  "last_auth_at": "2026-03-03T10:00:00Z",
  "issued_at": "2026-03-03T10:00:00Z",
  "expires_at": "2026-03-03T10:30:00Z",
  "session_expires_at": "2026-03-03T18:00:00Z",
  "jti": "tok-uuid-here",
  "iat": 1709460000,
  "exp": 1709461800,
  "iss": "helium-heartbeat"
}
```

### 3.3 Timing

| Parameter | Value | Configurable |
|---|---|---|
| JWT expiry | 30 minutes | `HEARTBEAT_JWT_EXPIRY_MINUTES` (default: 30) |
| Silent refresh | At 25-minute mark | SDK-side timer |
| Session hard cap | 8 hours | `HEARTBEAT_SESSION_HOURS` (default: 8) |
| Session hard cap behavior | Immutable — refresh NEVER extends | Not configurable |

### 3.4 Issuer

`helium-heartbeat` (default). Configurable via `JWT_ISSUER` env var for multi-tenant distinguishing.

---

## 4. Flow 1 — Initial Login

### 4.1 Sequence

```
1. User → Float UI:           Enter email + password
2. Float SDK → HeartBeat:      POST /api/auth/login {email, password}
   (SDK calls HeartBeat DIRECTLY — never through Relay)
3. HeartBeat:                  Validate bcrypt password
                               Check user is_active
                               Check concurrent session limit
                               Create session (8-hour hard cap)
                               Sign JWT (Ed25519)
                               Derive cipher_text (HMAC(master_secret, time_window))
4. HeartBeat → SDK:            {access_token, cipher_text, expires_at,
                                session_expires_at, user: {...}}
5. SDK:                        Store JWT in OS keyring (Windows Credential Manager / libsecret)
                               Use cipher_text to decrypt SQLCipher key wrapper
                               Open sync.db with SQLCipher key
                               ZERO cipher_text from memory immediately
6. SDK → HeartBeat:            GET /api/sse/stream (Authorization: Bearer {jwt})
7. SSE established:            Subsequent cipher_text refreshes arrive every ~9 min
8. Float UI:                   Main screen with data
```

### 4.2 Login Request

```http
POST /api/auth/login
Content-Type: application/json

{
  "email": "bob.nzelu@pronalytics.ng",
  "password": "..."
}
```

### 4.3 Login Response (200 OK)

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "cipher_text": "hex-encoded-cipher-text",
  "expires_at": "2026-03-03T10:30:00Z",
  "session_expires_at": "2026-03-03T18:00:00Z",
  "user": {
    "user_id": "usr-abc123",
    "role": "admin",
    "display_name": "Bob Nzelu",
    "tenant_id": "pronalytics-001",
    "is_first_run": false
  }
}
```

### 4.4 Login Errors

| Error Code | Status | Condition |
|---|---|---|
| `TOKEN_INVALID` | 401 | Wrong email, wrong password, or account deactivated |
| `SESSION_LIMIT` | 409 | Concurrent session limit exceeded (configurable per tenant) |

### 4.5 Concurrent Session Enforcement

`max_concurrent_sessions` is a tenant-level configuration (default: 1). When a new login would exceed the limit, HeartBeat returns `SESSION_LIMIT`. The SDK can offer to revoke the oldest session and retry.

---

## 5. Flow 2 — Silent Token Refresh

### 5.1 Sequence

```
1. SDK timer fires at 25-minute mark (5 min before JWT expiry)
2. SDK → HeartBeat:    POST /api/auth/token/refresh
                       Authorization: Bearer {current_jwt}
3. HeartBeat validates:
   a. JWT signature valid?
   b. Session not revoked (by jti)?
   c. Session hard cap (8hr) not reached?
   d. permissions_version unchanged?
   e. User still active?
4a. All pass → new JWT issued (new exp, same session_expires_at)
4b. permissions_version changed → PERMISSIONS_CHANGED (force re-login)
4c. Session cap reached → SESSION_EXPIRED (force re-login)
5. SDK stores new JWT in OS keyring
```

### 5.2 Refresh Response (200 OK)

```json
{
  "access_token": "eyJ...",
  "expires_at": "2026-03-03T11:00:00Z",
  "session_expires_at": "2026-03-03T18:00:00Z",
  "last_auth_at": "2026-03-03T10:00:00Z"
}
```

### 5.3 Refresh Errors

| Error Code | Status | Condition | SDK Action |
|---|---|---|---|
| `TOKEN_INVALID` | 401 | JWT signature invalid or session not found | Show login screen |
| `TOKEN_REVOKED` | 401 | Session explicitly revoked (admin action) | Show login screen |
| `SESSION_EXPIRED` | 401 | 8-hour hard cap reached | Show login screen |
| `PERMISSIONS_CHANGED` | 401 | Role/permissions changed by admin | Show login screen + notify user |

---

## 6. Flow 3 — Step-Up Authentication

### 6.1 Step-Up Tiers

Operations are classified into tiers based on security sensitivity:

| Tier | Window | Operations | Verification |
|---|---|---|---|
| Routine | 1 hour | View invoices, list batches, dashboard | None (JWT sufficient) |
| PIN-only | Per HEL-FLOAT-001 | Tab switch (sensitive data), DataBox visibility | SDK-local PIN check |
| Auth-only | 5-10 minutes | Finalize, approve, config changes | Password re-entry via HeartBeat |
| Immediate | 0 seconds | Create admin, deactivate owner | Full re-login |

**PIN-only step-ups are handled exclusively by the SDK.** HeartBeat is not involved.

**Auth step-ups go through HeartBeat's `/api/auth/stepup` endpoint.**

**Re-auth naturally resets both `last_auth_at` AND `last_PIN_at`** — entering a password implies fresh session freshness for PIN as well.

### 6.2 Auth Step-Up Sequence

```
1. User clicks "Finalize" in Float
2. SDK checks local last_auth_at: within required window (e.g., 5 min)?
3. If YES → call Core directly (fast path, no prompts)
4. If NO → SDK shows password prompt (Float Auth Dialog)
5. SDK → HeartBeat:   POST /api/auth/stepup
                      Authorization: Bearer {current_jwt}
                      Body: {"password": "..."}
6. HeartBeat:         Verify password against auth.users
                      Update last_auth_at on session
                      Issue new JWT with last_auth_at = NOW
                      Derive fresh cipher_text
7. HeartBeat → SDK:   {access_token, cipher_text, expires_at,
                       session_expires_at, last_auth_at}
8. SDK:               Store new JWT in keyring
                      Update local last_auth_at
                      Zero cipher_text after use
9. SDK → Core:        POST /api/finalize with fresh JWT
10. Core introspects (cached 30-60s) → step_up_satisfied: true
```

### 6.3 Step-Up Request

```http
POST /api/auth/stepup
Authorization: Bearer {current_jwt}
Content-Type: application/json

{
  "password": "..."
}
```

### 6.4 Step-Up Response (200 OK)

```json
{
  "access_token": "eyJ...",
  "cipher_text": "hex-encoded-cipher-text",
  "expires_at": "2026-03-03T14:30:00Z",
  "session_expires_at": "2026-03-03T18:00:00Z",
  "last_auth_at": "2026-03-03T14:00:00Z"
}
```

### 6.5 Step-Up Policy Endpoint

Services (Core, Relay) query HeartBeat to learn step-up requirements:

```http
GET /api/auth/operations/finalize/policy
Authorization: Bearer {service_api_key}:{service_api_secret}

Response:
{
  "operation": "finalize",
  "required_within_seconds": 300,
  "tier": "auth"
}
```

Services cache this for 5 minutes. Do not hardcode step-up windows.

---

## 7. Flow 4 — Token Introspection (Service-to-Service)

### 7.1 Purpose

Downstream services (Core, Relay, Edge) verify user JWTs by calling HeartBeat.

### 7.2 Caching

**All services cache introspection results for 30-60 seconds.** Same TTL for all operations (routine and sensitive). This is acceptable because:
- JWT is already self-verifiable (Ed25519 public key)
- Introspection adds revocation checking and step-up enforcement
- 30-60s cache is short enough for security, long enough for performance

### 7.3 Request

```http
POST /api/auth/introspect
Authorization: Bearer {service_api_key}:{service_api_secret}
Content-Type: application/json

{
  "token": "eyJ...",
  "required_permission": "invoice.finalize",
  "required_within_seconds": 300
}
```

### 7.4 Response (Active)

```json
{
  "active": true,
  "actor_type": "human",
  "user_id": "usr-abc123",
  "role": "admin",
  "permissions": ["invoice.view", "invoice.finalize"],
  "tenant_id": "abbey-001",
  "last_auth_at": "2026-03-03T14:00:00Z",
  "expires_at": "2026-03-03T18:00:00Z",
  "session_expires_at": "2026-03-03T18:00:00Z",
  "step_up_satisfied": true
}
```

### 7.5 Response (Inactive)

```json
{
  "active": false,
  "error_code": "TOKEN_REVOKED|SESSION_EXPIRED|PERMISSIONS_CHANGED|PERMISSION_DENIED|STEP_UP_REQUIRED",
  "message": "Human-readable explanation"
}
```

### 7.6 Fail-Closed Behavior

**If HeartBeat is unreachable, introspection fails. The calling service MUST reject the request.** No fallback to local JWT verification. HeartBeat down = platform down.

---

## 8. Flow 5 — SSE Cipher Text Delivery

### 8.1 SSE Endpoint

```http
GET /api/sse/stream
Authorization: Bearer {user_jwt}
```

### 8.2 Cipher Text Event

```
event: auth.cipher_refresh
data: {"cipher_text": "hex-encoded", "valid_until": "2026-03-03T10:09:00Z", "window_seconds": 540}
```

Pushed every ~9 minutes. SDK uses it to verify the SQLCipher connection is still valid.

### 8.3 Cipher Text Derivation

```
cipher_text = HMAC-SHA256(user.master_secret, time_window_id)
time_window_id = floor(unix_timestamp / 540)  // 540 seconds = 9 minutes
```

`master_secret` is per-user, stored in `auth.users.master_secret` column. Generated at user creation (32 random bytes, hex-encoded).

### 8.4 Cipher Text Lifecycle in SDK

```
1. SSE delivers cipher_text
2. SDK uses cipher_text to verify/refresh the SQLCipher key wrapper
3. SDK ZEROS cipher_text from memory immediately (bytearray.zfill)
4. Open DB connection persists in native SQLite C layer
5. Raw key bytes are NOT accessible in Python heap

On cipher text not received for >10 minutes:
6. SDK retries via POST /api/auth/stepup or POST /api/auth/token/refresh
7. After N retries fail → SDK closes SQLCipher connection
8. SDK logs out user (purge JWT from keyring)
9. Float shows login screen
10. Cipher text is never persisted on disk anywhere
```

### 8.5 Connection Restoration

When HeartBeat comes back online after an outage:

- **Automatic**: SDK polls `GET /health` every 30 seconds during outage
- **Manual**: Float UI shows "Retry Connection" button
- When HeartBeat responds → Float shows login screen (full re-auth required)
- On successful login → cipher text delivered in login response → sync.db reopens → SSE re-established

---

## 9. Flow 6 — Logout

### 9.1 Sequence

```
1. User clicks Logout in Float
2. SDK → HeartBeat:    POST /api/auth/logout
                       Authorization: Bearer {jwt} (even if expired)
3. HeartBeat:          Revoke session by jti
4. SDK:                Purge JWT from OS keyring
                       Close SSE connection
                       Close sync.db SQLCipher connection
                       Zero all key material from memory
                       (cipher text was already zeroed — never held)
5. Float UI:           Show login screen
6. sync.db:            Remains on disk, encrypted, inaccessible
                       (no cipher text = no SQLCipher key = no access)
```

### 9.2 Request

```http
POST /api/auth/logout
Authorization: Bearer {jwt}
```

HeartBeat accepts even expired tokens for logout (decode without verification) to ensure sessions can always be revoked.

### 9.3 Response

```json
{"status": "logged_out"}
```

---

## 10. Flow 7 — First-Run Bootstrap

### 10.1 Sequence

```
1. Pronalytics admin tool creates Owner user during provisioning
   (email, temporary password, is_first_run: true)
2. Owner receives credentials out-of-band
3. Owner launches Float, enters temp credentials
4. HeartBeat: validates, returns JWT with scope: "bootstrap"
   (restricted — can ONLY call /api/auth/password/change)
5. Float: detects is_first_run, shows forced password change screen
6. Owner enters new password (strength rules enforced client+server)
7. SDK → HeartBeat: POST /api/auth/password/change
   {new_password} (no current_password required in bootstrap mode)
8. HeartBeat: validates strength, checks recycling, updates hash
   Clears is_first_run and must_reset_password
   Revokes all sessions (bootstrap token invalidated)
9. Float: shows login screen
10. Owner logs in with new password → normal session begins
```

### 10.2 Password Rules (Server-Enforced)

- Minimum 10 characters
- At least one uppercase letter (A-Z)
- At least one lowercase letter (a-z)
- At least one digit (0-9)
- Cannot match current password or last 5 passwords (history check)

---

## 11. Flow 8 — Permission Change

### 11.1 Sequence

```
1. Admin changes user's role (e.g., operator → admin)
2. HeartBeat: increments permissions_version on auth.users
3. HeartBeat: pushes SSE event: permission.changed
   {user_id, old_role, new_role, permissions_version}
4. SDK receives event: clears last_typed_in (PIN trigger)
5. On next token refresh (within 5 min):
   HeartBeat: detects permissions_version mismatch
   Returns: PERMISSIONS_CHANGED (401)
6. SDK: shows login screen with message
   "Your permissions have been updated. Please log in again."
7. User re-authenticates → new JWT has updated role + permissions
```

---

## 12. Master Secret Rotation

### 12.1 Frequency

Once every 6-12 months. Manual trigger by admin or automated schedule.

### 12.2 Timing

Rotation ONLY occurs at hard re-auth (login) or app restart. NEVER mid-session.

### 12.3 Sequence

```
1. Admin triggers rotation (or scheduled job)
2. HeartBeat: generates new master_secret for user
   Stores new secret, marks rotation pending
3. On next user login:
   HeartBeat: returns cipher_text derived from NEW master_secret
   Response includes: rotation_pending: true
4. SDK:
   a. Decrypt SQLCipher key wrapper using OLD cipher text (from keyring cache)
      — if this fails, use new cipher text directly
   b. Re-encrypt (re-wrap) SQLCipher key with NEW cipher text
   c. Write new wrapped key to disk
   d. Zero old key material
   e. Show brief UI message: "Updating security credentials..."
5. HeartBeat: clears rotation_pending flag
6. Subsequent cipher texts derived from new master_secret
```

### 12.4 Important

**No database rebuild required.** Only the key wrapper changes. The SQLCipher key itself (and therefore sync.db's encryption) is unchanged. The re-wrap operation takes milliseconds.

---

## 13. Database Schema (auth schema in PostgreSQL)

```sql
-- auth.users
CREATE TABLE auth.users (
    user_id         TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT,          -- bcrypt (NULL for SSO-only users)
    display_name    TEXT NOT NULL,
    role_id         TEXT NOT NULL REFERENCES auth.roles(role_id),
    tenant_id       TEXT NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    is_first_run    BOOLEAN DEFAULT TRUE,
    must_reset_password BOOLEAN DEFAULT FALSE,
    mfa_configured  BOOLEAN DEFAULT FALSE,
    permissions_version INTEGER DEFAULT 1,
    master_secret   TEXT NOT NULL,  -- 32 random bytes, hex-encoded (cipher text derivation)
    last_login_at   TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- auth.roles
CREATE TABLE auth.roles (
    role_id     TEXT PRIMARY KEY,  -- 'owner', 'admin', 'operator', 'support'
    description TEXT
);

-- auth.permissions
CREATE TABLE auth.permissions (
    permission_id   TEXT PRIMARY KEY,  -- 'invoice.view', 'invoice.approve', etc.
    description     TEXT
);

-- auth.role_permissions
CREATE TABLE auth.role_permissions (
    role_id         TEXT REFERENCES auth.roles(role_id),
    permission_id   TEXT REFERENCES auth.permissions(permission_id),
    PRIMARY KEY (role_id, permission_id)
);

-- auth.user_permissions (per-user overrides)
CREATE TABLE auth.user_permissions (
    user_id         TEXT REFERENCES auth.users(user_id),
    permission_id   TEXT REFERENCES auth.permissions(permission_id),
    granted         BOOLEAN DEFAULT TRUE,
    PRIMARY KEY (user_id, permission_id)
);

-- auth.sessions
CREATE TABLE auth.sessions (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES auth.users(user_id),
    jwt_jti         TEXT NOT NULL,
    issued_at       TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    session_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,  -- 8-hour hard cap (immutable)
    last_auth_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    last_refreshed  TIMESTAMP WITH TIME ZONE,
    is_revoked      BOOLEAN DEFAULT FALSE,
    revoked_at      TIMESTAMP WITH TIME ZONE,
    revoked_reason  TEXT,
    device_fingerprint TEXT,      -- machine_guid + mac_address + computer_name
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- auth.password_history (recycling prevention)
CREATE TABLE auth.password_history (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES auth.users(user_id),
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- auth.schema_migrations
CREATE TABLE auth.schema_migrations (
    version         INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    checksum        TEXT NOT NULL,
    applied_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    execution_time_ms INTEGER
);
```

---

## 14. Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `HEARTBEAT_JWT_EXPIRY_MINUTES` | 30 | JWT short-lived expiry (minutes) |
| `HEARTBEAT_SESSION_HOURS` | 8 | Session hard cap (hours) |
| `HEARTBEAT_JWT_PRIVATE_KEY_PATH` | `databases/keys/jwt_private.pem` | Ed25519 private key |
| `HEARTBEAT_JWT_PUBLIC_KEY_PATH` | `databases/keys/jwt_public.pem` | Ed25519 public key |
| `JWT_ISSUER` | `helium-heartbeat` | JWT issuer claim |
| `HEARTBEAT_CIPHER_WINDOW_SECONDS` | 540 | Cipher text time window (9 min) |
| `HEARTBEAT_MAX_CONCURRENT_SESSIONS` | 1 | Default concurrent session limit (overridden per tenant) |

SDK-side:

| Variable | Default | Description |
|---|---|---|
| `JWT_ALGORITHM` | `EdDSA` | JWT verification algorithm |
| `JWT_ED25519_PUBLIC_KEY_PATH` | — | Path to HeartBeat's Ed25519 public key |
| `JWT_ISSUER` | `helium-heartbeat` | Expected JWT issuer |
| `HEARTBEAT_URL` | `http://localhost:9000` | HeartBeat base URL |

---

## 15. Security Invariants

1. **Cipher text is NEVER persisted on disk.** SSE delivers → SDK uses → SDK zeros. Every time.
2. **SQLCipher key is in memory ONLY as a native SQLite connection.** Python heap never holds it after `PRAGMA key`.
3. **JWT is stored in OS-level credential store** (Windows Credential Manager / libsecret), not in files.
4. **All services fail closed.** HeartBeat unreachable = reject all requests.
5. **Permission changes force re-auth.** No silent permission updates.
6. **Session hard cap is immutable.** 8 hours from login, never extended.
7. **Logout accepts expired tokens.** Users can always revoke their session.
8. **Password history prevents recycling.** Last 5 passwords checked.
9. **10-minute maximum exposure window.** Cipher text cycle ensures sync.db locks within 10 minutes of session loss.

---

*End of Auth Service Contract*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-03-03*
