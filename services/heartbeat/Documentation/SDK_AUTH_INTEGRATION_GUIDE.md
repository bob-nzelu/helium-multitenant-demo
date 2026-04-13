# SDK Auth Integration Guide

**Version:** 1.0
**Date:** 2026-03-04
**Target:** Float/App/SDK team
**Source of Truth:** `HeartBeat/src/api/auth.py` (Pydantic request/response models)

---

## 1. Overview

HeartBeat is the authentication authority for the Helium platform. The SDK (Float) is a consumer that:

- Authenticates users via HeartBeat's login endpoint
- Receives Ed25519-signed JWTs for session management
- Receives cipher_text for SQLCipher database encryption
- Maintains an SSE connection for real-time auth events (cipher refresh, permission changes, session revocation)
- Handles step-up re-authentication for sensitive operations

**Session Model:**
- JWT lifetime: 30 minutes (silent refresh at 25-min mark)
- Session hard cap: 8 hours from login (re-auth required)
- Cipher text rotation: every ~9 minutes via SSE

---

## 2. Login Flow

### 2.1 Endpoint

```
POST /api/auth/login
Content-Type: application/json

Request:
{
    "email": "operator@company.ng",
    "password": "SecurePass123"
}

Response (200):
{
    "access_token": "eyJhbGciOiJFZERTQSIs...",
    "token_type": "bearer",
    "cipher_text": "a1b2c3d4...64_hex_chars",
    "expires_at": "2026-03-04T10:30:00Z",
    "session_expires_at": "2026-03-04T18:00:00Z",
    "user": {
        "user_id": "user-abc-123",
        "role": "operator",
        "display_name": "John Operator",
        "tenant_id": "tenant-001",
        "is_first_run": false
    }
}
```

### 2.2 SDK Actions on Login Success

1. Store `access_token` in memory (AuthContext.raw_token)
2. Use `cipher_text` immediately to open/rekey SQLCipher databases
3. Schedule silent refresh timer at 25-minute mark
4. Connect to SSE endpoint with the access_token
5. Update UI with user role and display name

### 2.3 First-Run Bootstrap

When `user.is_first_run == true`:

- Token has `scope: "bootstrap"` (restricted permissions)
- SDK **must** show password change dialog before any business operations
- Call `POST /api/auth/password/change` with only `new_password` (no `current_password`)
- After success, all sessions are revoked -- SDK must log in again with the new password
- On second login, `is_first_run` will be `false`

### 2.4 Session Limit (409)

```
Response (409):
{
    "error_code": "SESSION_LIMIT",
    "message": "Concurrent session limit reached (1).",
    "details": [{"max_sessions": 1, "active_sessions": 1}]
}
```

SDK should show dialog: **"Maximum sessions reached. Please close another session or contact your administrator."**

---

## 3. JWT Claims Mapping

### 3.1 HeartBeat JWT Payload

```json
{
    "sub": "user-abc-123",
    "tenant_id": "tenant-001",
    "role": "operator",
    "permissions": ["invoice.view", "invoice.create", "blob.upload"],
    "permissions_version": 3,
    "last_auth_at": "2026-03-04T10:00:00Z",
    "issued_at": "2026-03-04T10:00:00Z",
    "expires_at": "2026-03-04T10:30:00Z",
    "session_expires_at": "2026-03-04T18:00:00Z",
    "jti": "tok-uuid-string",
    "iat": 1741082400,
    "exp": 1741084200
}
```

### 3.2 Required Changes to AuthContext Model

**File:** `Float/App/SDK/src/ws5_auth_ratelimit/models.py`

Current AuthContext fields:
- `user_id`, `email`, `scopes`, `token_valid`, `is_admin`

**Add these fields:**

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `role` | `str` | JWT `role` | User role (owner/admin/operator/support) |
| `tenant_id` | `str` | JWT `tenant_id` | Tenant identifier |
| `raw_token` | `str` | JWT string | Store for refresh/logout calls |
| `last_auth_at` | `Optional[str]` | JWT `last_auth_at` | Step-up freshness evaluation |
| `session_expires_at` | `Optional[str]` | JWT `session_expires_at` | Display session cap countdown |
| `permissions_version` | `int` | JWT `permissions_version` | Permission change detection |

### 3.3 Required Changes to AuthProvider.validate_token()

**File:** `Float/App/SDK/src/ws5_auth_ratelimit/auth.py`

Current code (lines 163-178) already extracts `sub`, `role`, `permissions` from the JWT payload. Changes needed:

1. **Store `role` in AuthContext** (currently only used to derive `is_admin`)
2. **Extract and store `tenant_id`** from payload
3. **Extract and store `last_auth_at`**, `session_expires_at`, `permissions_version`
4. **Store `raw_token`** (the JWT string) in AuthContext

---

## 4. Silent Token Refresh

### 4.1 Strategy

- JWT expires at 30 minutes (`jwt_expiry_minutes=30`)
- SDK should proactively refresh at the **25-minute mark** (do NOT wait for 401)
- This avoids latency gaps where the user has no valid token

### 4.2 Endpoint

```
POST /api/auth/token/refresh
Authorization: Bearer {current_access_token}

Response (200):
{
    "access_token": "eyJhbGciOiJFZERTQSIs...(new)",
    "expires_at": "2026-03-04T11:00:00Z",
    "session_expires_at": "2026-03-04T18:00:00Z",
    "last_auth_at": "2026-03-04T10:00:00Z"
}
```

### 4.3 Implementation Pattern

```python
# In AuthProvider or SyncManager:
def _schedule_refresh(self, expires_at: str):
    """Schedule refresh 5 minutes before token expiry."""
    expiry_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    refresh_at = expiry_dt - timedelta(minutes=5)
    delay_ms = max(0, int((refresh_at - now).total_seconds() * 1000))
    QTimer.singleShot(delay_ms, self._do_refresh)

async def _do_refresh(self):
    try:
        result = await self._http.post(
            f"{heartbeat_url}/api/auth/token/refresh",
            headers={"Authorization": f"Bearer {self._auth_context.raw_token}"},
        )
        # Update AuthContext with new token
        self._auth_context.raw_token = result["access_token"]
        self._schedule_refresh(result["expires_at"])
    except AuthError as e:
        if e.code in ("TOKEN_INVALID", "SESSION_EXPIRED", "PERMISSIONS_CHANGED"):
            self._force_relogin(reason=e.code)
```

### 4.4 Refresh Failure Scenarios

| Error Code | SDK Action |
|------------|-----------|
| `TOKEN_INVALID` | Token already expired -- force full re-login |
| `SESSION_EXPIRED` | 8-hour cap reached -- force full re-login |
| `PERMISSIONS_CHANGED` | Permissions modified -- force re-login, show banner |
| `TOKEN_REVOKED` | Session revoked by admin -- force re-login |

---

## 5. Cipher Text and SQLCipher

### 5.1 What cipher_text Is

```
cipher_text = HMAC-SHA256(master_secret, floor(unix_timestamp / 540))
```

- 64-character hex string
- Derived from per-user `master_secret` (stored in PostgreSQL) + 9-minute time window
- Used as the SQLCipher encryption key for local databases
- Rotates every 9 minutes (540 seconds)

### 5.2 Login Delivers Initial cipher_text

The login response includes `cipher_text` -- use immediately to open/rekey SQLCipher databases.

### 5.3 SSE Pushes cipher_refresh Events

HeartBeat's `CipherTextScheduler` pushes `auth.cipher_refresh` to each connected user every ~9 minutes:

```
event: auth.cipher_refresh
data: {"cipher_text": "new_hex_64", "valid_until": "2026-03-04T10:18:00Z", "window_seconds": 540}
```

SDK must rekey SQLCipher databases with the new `cipher_text` value.

### 5.4 CRITICAL: Add Auth Events to ServerEventType Enum

**File:** `Float/App/SDK/src/ws3_sync_layer/models.py`

The current `ServerEventType` enum is **missing** auth events. Add:

```python
# Auth Events (HeartBeat)
CIPHER_REFRESH = "auth.cipher_refresh"
PERMISSION_CHANGED = "permission.changed"
SESSION_REVOKED = "session.revoked"
```

---

## 6. SSE Event Handling

### 6.1 Connection Setup

```
GET /api/sse/stream
Authorization: Bearer {access_token}
Accept: text/event-stream

# Server sends keepalive every 30 seconds:
: keepalive

# Initial connection event:
event: connected
data: {"status": "connected"}
```

The existing `SSEClient` at `Float/App/SDK/src/ws3_sync_layer/sse_client.py` handles connection, reconnection with exponential backoff, and SSE wire protocol parsing.

### 6.2 auth.cipher_refresh

**Payload:** `{"cipher_text": "...", "valid_until": "...", "window_seconds": 540}`

SDK action:
1. Rekey all SQLCipher databases with `cipher_text`
2. Log: "Cipher text rotated, valid until {valid_until}"

### 6.3 permission.changed

**Payload:** `{"user_id": "...", "permissions": [...], "permissions_version": N}`

SDK action:
1. Trigger token refresh (to get updated JWT claims)
2. Update local permissions cache
3. Show banner: "Your permissions have been updated"

### 6.4 session.revoked

**Payload:** `{"user_id": "...", "reason": "admin_revoked | password_changed"}`

SDK action:
1. Force immediate logout (clear token, cipher_text, AuthContext)
2. Disconnect SSE
3. Close SQLCipher databases
4. Show dialog: "Your session has been terminated by an administrator"

---

## 7. Step-Up Authentication

### 7.1 When HeartBeat Returns STEP_UP_REQUIRED

When a downstream service (Relay/Core) introspects a JWT with a freshness requirement (e.g., `required_within_seconds=300`), and the user's `last_auth_at` is too old, the service returns:

```json
{"error_code": "STEP_UP_REQUIRED", "required_within_seconds": 300}
```

SDK should show a PIN dialog (SDK-local concept) or re-authentication dialog.

### 7.2 Endpoint

```
POST /api/auth/stepup
Authorization: Bearer {current_access_token}
Content-Type: application/json

Request:
{"password": "UserPassword123"}

Response (200):
{
    "access_token": "eyJ...(new, with fresh last_auth_at)",
    "cipher_text": "fresh_hex_64",
    "expires_at": "2026-03-04T10:30:00Z",
    "session_expires_at": "2026-03-04T18:00:00Z",
    "last_auth_at": "2026-03-04T10:05:00Z"
}
```

### 7.3 Operation Policy Pre-Check

SDK can pre-check whether an operation needs step-up before attempting it:

```
GET /api/auth/operations/{operation}/policy

Response:
{
    "operation": "invoice.finalize",
    "required_within_seconds": 300,
    "tier": "auth"
}
```

### 7.4 Step-Up Tiers

| Tier | Window | Operations | SDK Behavior |
|------|--------|-----------|-------------|
| `routine` | 3600s (1hr) | invoice.view, blob.upload | Normal JWT sufficient |
| `auth` | 300s (5min) | invoice.finalize, config.edit | Show re-auth dialog |
| `immediate` | 0s | owner.deactivate | Re-auth RIGHT NOW |

---

## 8. Logout

### 8.1 Endpoint

```
POST /api/auth/logout
Authorization: Bearer {access_token}

Response (200):
{"status": "logged_out"}
```

Even expired tokens are accepted (so sessions can always be cleaned up).

### 8.2 SDK Cleanup Checklist

On logout, SDK must:
1. Clear `AuthContext.raw_token`
2. Clear cipher_text from memory
3. Close SQLCipher databases
4. Disconnect SSE connection
5. Cancel refresh timer
6. Clear any cached permissions

---

## 9. Password Change

### 9.1 Endpoint

```
POST /api/auth/password/change
Authorization: Bearer {access_token}
Content-Type: application/json

Request:
{
    "new_password": "NewSecurePass1",
    "current_password": "OldPassword123"    // NOT required for bootstrap
}

Response (200):
{"status": "password_changed"}
```

### 9.2 After Password Change

- All active sessions are revoked (including the current one)
- SDK must force re-login with the new password
- Old cipher_text is invalidated (master_secret unchanged, but session is gone)

### 9.3 Password Requirements

- Minimum 10 characters
- At least one uppercase letter (A-Z)
- At least one lowercase letter (a-z)
- At least one digit (0-9)
- Cannot match current password or last 5 passwords

---

## 10. Secure Token Storage

### 10.1 access_token: Memory Only

- Store in `AuthContext.raw_token` (in-memory only)
- Never persist access_tokens to disk

### 10.2 refresh_token (Future): Windows Credential Manager

When refresh tokens are implemented:

```python
import keyring

# Store
keyring.set_password("helium", user_id, refresh_token)

# Retrieve
refresh_token = keyring.get_password("helium", user_id)

# Delete (on logout)
keyring.delete_password("helium", user_id)
```

---

## 11. Error Code Reference

| Error Code | HTTP | SDK Action |
|------------|------|-----------|
| `TOKEN_INVALID` | 401 | Force re-login |
| `TOKEN_REVOKED` | 401 | Force re-login, show "session revoked" |
| `SESSION_EXPIRED` | 401 | Force re-login, show "session expired" |
| `SESSION_LIMIT` | 409 | Show "max sessions reached" dialog |
| `PERMISSIONS_CHANGED` | 401 | Force re-login, show "permissions updated" |
| `STEP_UP_REQUIRED` | 200* | Show PIN/re-auth dialog |
| `FIRST_RUN_REQUIRED` | 200* | Show password change dialog |
| `PW_WEAK` | 400 | Show password requirements |
| `PW_RECYCLED` | 400 | Show "cannot reuse recent password" |
| `PW_WRONG_CURRENT` | 400 | Show "current password incorrect" |
| `PERMISSION_DENIED` | 200* | Show "insufficient permissions" |

*Note: `STEP_UP_REQUIRED`, `FIRST_RUN_REQUIRED`, and `PERMISSION_DENIED` come inside a 200 introspect response with `active=true` or `active=false` -- they are not HTTP-level errors.

---

## 12. Required SDK Code Changes Summary

| File | Change |
|------|--------|
| `ws5_auth_ratelimit/models.py` | Add `role`, `tenant_id`, `raw_token`, `last_auth_at`, `session_expires_at`, `permissions_version` to AuthContext |
| `ws5_auth_ratelimit/auth.py` | Store role, tenant_id, and other new fields in AuthContext during validate_token() |
| `ws3_sync_layer/models.py` | Add `CIPHER_REFRESH`, `PERMISSION_CHANGED`, `SESSION_REVOKED` to ServerEventType enum |
| `ws3_sync_layer/sse_client.py` | Add handlers for auth.cipher_refresh, permission.changed, session.revoked events |
| Auth flow (new) | Implement login via POST /api/auth/login, store token, schedule refresh |
| Auth flow (new) | Implement silent refresh at 25-min mark via POST /api/auth/token/refresh |
| Auth flow (new) | Implement step-up dialog triggered by STEP_UP_REQUIRED |
| Auth flow (new) | Implement first-run bootstrap (password change on first login) |
