# Float SDK — HeartBeat Auth Integration Guide

**Date:** 2026-02-23
**For:** Float App / SDK team
**HeartBeat Contract:** Part 4

---

## Overview

HeartBeat provides user authentication for the Helium platform. Float must authenticate users before making any Relay or Core API calls. This document covers everything the Float/SDK team needs to integrate.

---

## 1. Starting HeartBeat (Dev Mode)

From the HeartBeat root directory:

```bash
# Windows
scripts\run_dev.bat

# Linux/Mac
bash scripts/run_dev.sh
```

HeartBeat starts on **http://127.0.0.1:9000**. All databases are auto-created on first run:
- `blob.db` — File metadata
- `registry.db` — Service discovery + API credentials
- `auth.db` — Users, sessions, roles, permissions

Swagger UI: http://127.0.0.1:9000/docs

### Test User (Pre-seeded)

| Field | Value |
|---|---|
| Email | `bob.nzelu@pronalytics.ng` |
| Password | `1234%%%` |
| Role | `owner` (full access, wildcard `*` permission) |
| Tenant | `helium-dev` |

### Service Credentials (Float SDK → Relay/Core)

| Field | Value |
|---|---|
| API Key | `fl_test_float001` |
| API Secret | `secret-float-sdk-dev-001` |

---

## 2. Authentication Flow

### 2.1 Login (Password)

Float shows a login screen. User enters email + password.

```
POST http://127.0.0.1:9000/api/auth/login
Content-Type: application/json

{
    "email": "bob.nzelu@pronalytics.ng",
    "password": "1234%%%"
}
```

**Response (200):**
```json
{
    "access_token": "<JWT string>",
    "token_type": "bearer",
    "expires_at": "2026-02-23T08:30:00Z",
    "session_expires_at": "2026-02-23T16:00:00Z",
    "user": {
        "user_id": "usr-bob-nzelu-001",
        "role": "owner",
        "display_name": "Bob Nzelu",
        "tenant_id": "helium-dev",
        "is_first_run": false
    }
}
```

**Key fields:**
- `access_token` — JWT, include in all subsequent API calls as `Authorization: Bearer <token>`
- `expires_at` — JWT expires in ~30 minutes. Silent refresh before this time.
- `session_expires_at` — **Hard cap (8 hours from login)**. After this, user MUST re-authenticate with password. No silent refresh past this point.

**Error (401):**
```json
{
    "error_code": "TOKEN_INVALID",
    "message": "Invalid credentials"
}
```

### 2.2 Silent Token Refresh

Float SDK should automatically refresh the JWT before it expires (~every 25 minutes). The user never sees this.

```
POST http://127.0.0.1:9000/api/auth/token/refresh
Authorization: Bearer <current_jwt>
```

**Response (200):**
```json
{
    "access_token": "<new JWT>",
    "expires_at": "2026-02-23T09:00:00Z",
    "session_expires_at": "2026-02-23T16:00:00Z",
    "last_auth_at": "2026-02-23T08:00:00Z"
}
```

**Important error cases:**
- `SESSION_EXPIRED` (401) — 8-hour cap reached. Show login screen.
- `PERMISSIONS_CHANGED` (401) — Admin changed user's permissions. Show login screen.

### 2.3 Logout

```
POST http://127.0.0.1:9000/api/auth/logout
Authorization: Bearer <current_jwt>
```

**Response (200):**
```json
{
    "status": "logged_out"
}
```

Even expired tokens can be used for logout (the session is revoked by JWT ID).

---

## 3. PIN — Float/SDK-Level App Lock

**PIN is entirely a Float-side concept.** HeartBeat does not store, verify, or know about PINs.

PIN is an app-level security gate that Float implements locally for:
- Quick re-authentication when the 8-hour session expires
- Step-up prompts during sensitive operations
- Screen lock / idle timeout unlock

### Float's PIN responsibilities:

1. **PIN setup UI** — Prompt user to set a 6-digit PIN after first password login
2. **PIN storage** — Hash with bcrypt and store in Float's local `config.db` (never plaintext)
3. **PIN verification** — Verify locally against stored hash
4. **3-minute grace period** — SDK must NEVER prompt for PIN if a successful authentication (password or PIN) happened less than 3 minutes ago. Track `last_auth_timestamp` locally and skip PIN gate when `now - last_auth_timestamp < 180 seconds`.
5. **PIN lockout** — 3 consecutive wrong PINs → forced logout (call `/api/auth/logout`), clear all local session state, show full password re-auth screen, then require new PIN setup after successful login
6. **After PIN verify** — Call `POST /api/auth/login` with the user's password to get a new session from HeartBeat

### PIN lockout flow:

```
Wrong PIN (attempt 1) → "Incorrect PIN, 2 attempts remaining"
Wrong PIN (attempt 2) → "Incorrect PIN, 1 attempt remaining"
Wrong PIN (attempt 3) → LOCKOUT:
    1. Call POST /api/auth/logout (revoke session)
    2. Clear access_token, session state from memory
    3. Delete stored PIN hash from config.db
    4. Show password login screen
    5. After successful password login → prompt to set new PIN
```

### Important: PIN does NOT replace password for HeartBeat

When the 8-hour session expires, HeartBeat requires a **password re-authentication**. Float's options:

- **Option A (recommended)**: Show login screen, user enters password, call `/api/auth/login`
- **Option B (with PIN gate)**: Show PIN screen first as an app-level gate, then show password screen for HeartBeat re-auth

PIN is SDK's way of knowing locally that the user is who they say they are. HeartBeat never sees PINs.

---

## 4. Session Lifecycle — What Float Must Implement

```
┌─────────────────────────────────────────────────────────┐
│  FLOAT SDK SESSION TIMELINE                             │
│                                                         │
│  T+0:00  ── Password Login ──────── JWT issued (30m)    │
│  T+0:25  ── Silent Refresh ──────── New JWT (30m)       │
│  T+0:50  ── Silent Refresh ──────── New JWT (30m)       │
│  ...     ── (repeats automatically every ~25 min)       │
│  T+7:30  ── Silent Refresh ──────── New JWT (30m, last) │
│  T+8:00  ── SESSION_EXPIRED ─────── RE-LOGIN REQUIRED   │
│           └─ Show login screen (password to HeartBeat)  │
│                                                         │
│  FLOAT-LEVEL (optional):                                │
│  ─ Idle timeout → PIN lock screen → PIN verify local    │
│  ─ Step-up prompt → PIN verify local                    │
└─────────────────────────────────────────────────────────┘
```

### What Float SDK must track:

| State | Store in memory | Persist to config.db? |
|---|---|---|
| `access_token` | Yes | No (security) |
| `expires_at` | Yes (for refresh timer) | No |
| `session_expires_at` | Yes (for re-auth timer) | No |
| `user.email` | Yes | **Yes** (remember for login screen) |
| PIN hash | No (load on demand) | **Yes** (bcrypt hash in config.db) |
| Password | **Never** | **Never** |

### Timer-based logic:

1. **Refresh timer**: Set a timer for `expires_at - 5 minutes`. When it fires, call `/token/refresh`. Replace `access_token` in memory.

2. **Session cap timer**: Set a timer for `session_expires_at`. When it fires (or when `/token/refresh` returns `SESSION_EXPIRED`), show the login screen for password re-auth.

3. **Step-up freshness**: Downstream services check `last_auth_at` elapsed time via introspect. If a step-up error comes back from Relay/Core, Float can prompt with a local PIN gate before showing the password re-auth screen if needed.

### Credential storage:

- **Remember email only** — pre-fill the login field
- **Store PIN hash locally** — bcrypt in config.db (never plaintext)
- **Never store password** — not in config.db, not in memory after login
- Enterprise SSO will handle convenience when that tier ships

---

## 5. Float Instance ID — Registry Registration

Each Float installation must register with HeartBeat's registry to get API credentials.

### How Instance ID Works

**The caller (Float) generates the instance_id.** HeartBeat does not auto-generate it.

Suggested format: `float-desktop-{uuid4}` or `float-{machine-name}-{uuid4}`

### Registration Flow

```
POST http://127.0.0.1:9000/api/registry/register
Content-Type: application/json

{
    "service_instance_id": "float-desktop-001",
    "service_name": "float-sdk",
    "display_name": "Float Desktop (Bob's Laptop)",
    "base_url": "http://localhost:0",
    "endpoints": [],
    "version": "2.0.0",
    "tier": "standard"
}
```

After registration, request API credentials:

```
POST http://127.0.0.1:9000/api/registry/credentials/generate
Content-Type: application/json

{
    "service_name": "float-sdk",
    "issued_to": "float-desktop-001"
}
```

**Response includes `api_key` and `api_secret`.** Store these in Float's local `config.db` for Relay/Core calls.

**Note:** In production, HeartBeat will check `license.db` before generating credentials to verify the tenant/instance has the required license tier. This is not yet enforced in dev mode.

### Current Dev Setup

For development, Float uses hardcoded test credentials (see `sdk_lifecycle.py`):
```python
api_key="fl_test_float001"
api_secret="secret-float-sdk-dev-001"
```

Production flow: register → get credentials → store in config.db → use for all Relay/Core calls.

### Instance ID in Registry Schema

```sql
-- service_instances table (registry.db)
service_instance_id TEXT PRIMARY KEY  -- "float-desktop-001"
service_name        TEXT NOT NULL     -- "float-sdk"
display_name        TEXT NOT NULL     -- "Float Desktop (Bob's Laptop)"
base_url            TEXT NOT NULL     -- URL or placeholder
```

Re-registration with the same `service_instance_id` performs an **upsert** (updates the existing record).

---

## 6. Auth Endpoints Quick Reference

| Endpoint | Auth Required | Purpose |
|---|---|---|
| `POST /api/auth/login` | None | Password login → JWT |
| `POST /api/auth/token/refresh` | Bearer JWT | Silent refresh → new JWT |
| `POST /api/auth/logout` | Bearer JWT | Revoke session |
| `POST /api/auth/introspect` | Bearer api_key:api_secret | Service-to-service verify |

---

## 7. Error Codes Float Should Handle

| Error Code | HTTP | What to do |
|---|---|---|
| `TOKEN_INVALID` | 401 | Show login screen |
| `TOKEN_REVOKED` | 401 | Show login screen |
| `SESSION_EXPIRED` | 401 | Show login screen (password required) |
| `PERMISSIONS_CHANGED` | 401 | Show login screen (password required) |
| `STEP_UP_REQUIRED` | — | Prompt re-auth (local PIN gate + password if needed) |
| `PERMISSION_DENIED` | — | Show "access denied" message |
| `FIRST_RUN_REQUIRED` | — | Redirect to first-run setup flow |

---

## 8. Bulk Upload Flow (With Auth)

```
1. Float → POST HeartBeat:9000/api/auth/login
   ← JWT (30 min) + session_expires_at (8 hr)

2. User selects files, clicks Upload

3. Float SDK → POST Relay:8082/api/ingest
   Headers:
     Authorization: Bearer <JWT>
     X-Api-Key: fl_test_float001
     X-Timestamp: <ISO>
     X-Signature: <HMAC-SHA256>
   Body: multipart/form-data (files + metadata)

4. Relay → POST HeartBeat:9000/api/auth/introspect
   Headers: Bearer fl_test_float001:secret-float-sdk-dev-001
   Body: {"token": "<JWT>", "required_permission": "invoice.upload"}
   ← {active: true, permissions: ["*"], ...}

5. Relay processes upload → forwards to HeartBeat blob storage
   ← Returns {data_uuid, status: "processed"/"queued"}

6. Float refreshes JWT automatically (SDK timer)
   → POST HeartBeat:9000/api/auth/token/refresh

7. After 8 hours: SESSION_EXPIRED
   → Float shows login screen (password to HeartBeat)
```
