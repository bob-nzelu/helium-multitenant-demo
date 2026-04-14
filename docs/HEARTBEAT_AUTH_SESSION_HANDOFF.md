# HeartBeat Auth + Registration — Dedicated Session Handoff

**Date:** 14 April 2026
**For:** Dedicated HeartBeat_AWS session
**Repo:** `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo`
**GitHub:** `https://github.com/bob-nzelu/helium-multitenant-demo`

---

## INSTRUCTIONS

**Before writing any code or plan, you MUST:**
1. Read this ENTIRE document
2. Read `docs/UNIFIED_AUTH_CONTRACT.md` (the auth architecture spec)
3. Read the existing HeartBeat auth code (file paths below)
4. Ask the user ALL clarifying questions — align mentally before planning
5. Only after alignment should you create a plan
6. Only after plan approval should you write code
7. Test on live EC2 at 13.247.224.147 after each change

---

## LIVE INFRASTRUCTURE

| Service | Host | Port | Status |
|---------|------|------|--------|
| HeartBeat | 13.247.224.147 | 9000 | Running (MOCK_AUTH=true) |
| PostgreSQL | 13.247.224.147 | 5432 | Running (auth schema seeded) |
| Redis | 13.247.224.147 | 6379 | Running |
| RabbitMQ | 13.247.224.147 | 5672/15672 | Running |
| Relay | 13.247.224.147 | 8082 | Running |
| Core | 13.247.224.147 | 8080 | Running |
| Edge | 13.247.224.147 | 8085 | Running (stub) |
| HIS | 13.247.224.147 | 8500 | Running (stub) |
| SIS | 13.247.224.147 | 8501 | Running (stub) |
| Simulator | 13.247.224.147 | 8090 | Running |

**SSH:** `ssh -i "C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\AB Microfinance\helium-key.pem" ubuntu@13.247.224.147`

**Deploy pattern:** Edit locally → `git push` → SSH → `cd helium-multitenant-demo && git pull && sudo docker compose build heartbeat && sudo docker compose up -d heartbeat`

---

## WHAT EXISTS TODAY

### PostgreSQL Auth Schema (already created, running)

Tables in `auth` schema:
- `auth.roles` — 4 roles seeded: Owner, Admin, Operator, Support
- `auth.permissions` — 14 permissions seeded (including `*` wildcard)
- `auth.role_permissions` — Owner=`*`, Admin=6 perms, Support=`health.read`
- `auth.users` — Charles Omoakin seeded:
  - user_id: `usr-abbey-owner-001`
  - email: `Charles.Omoakin@abbeymortgagebank.com`
  - password_hash: bcrypt of `123456`
  - role_id: `Owner`
  - tenant_id: `tenant-abbey-001`
  - is_first_run: `true`
- `auth.sessions` — empty (no active sessions)
- `auth.password_history` — empty
- `auth.step_up_policies` — 5 policies seeded

Schema files: `config/schemas/002_auth_schema.sql`, `config/schemas/003_seed_abbey_user.sql`

### HeartBeat Auth Code (canonical, fully built)

| File | Purpose | Status |
|------|---------|--------|
| `services/heartbeat/src/api/auth.py` | FastAPI auth endpoints (login, refresh, logout, introspect, stepup, password/change) | **Complete** |
| `services/heartbeat/src/handlers/auth_handler.py` | Business logic for all auth operations | **Complete** |
| `services/heartbeat/src/database/pg_auth.py` | PostgreSQL queries (auth.* schema prefix) | **Complete** |
| `services/heartbeat/src/auth/jwt_manager.py` | Ed25519 keypair management, JWT sign/verify | **Complete** |
| `services/heartbeat/src/auth/dependencies.py` | FastAPI dependency injection (get_current_user_token, verify_service_credentials) | **Complete** |
| `services/heartbeat/src/api/mock_auth.py` | Mock auth router (currently active) | **To be replaced** |

### Mock Auth (currently active)

HeartBeat runs with `HEARTBEAT_MOCK_AUTH=true`. In `src/main.py` line 530:
```python
if os.environ.get("HEARTBEAT_MOCK_AUTH", "").lower() in ("true", "1", "yes"):
    from .api.mock_auth import router as mock_auth_router
    app.include_router(mock_auth_router)
else:
    app.include_router(auth_router)
```

Mock auth returns fake JWTs (base64, not Ed25519 signed). All responses are hardcoded for Charles Omoakin.

---

## WHAT TO BUILD

### Task 1: Switch to Real Auth

1. Set `HEARTBEAT_MOCK_AUTH=false` in docker-compose.yml
2. Ensure Ed25519 keys exist at `services/heartbeat/databases/keys/`
   - If not, HeartBeat auto-generates them on first boot
3. Rebuild HeartBeat, test login:
   ```
   curl -X POST http://13.247.224.147:9000/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email":"Charles.Omoakin@abbeymortgagebank.com","password":"123456"}'
   ```
4. Expected: real Ed25519 JWT, `is_first_run: true`, cipher_text

**Risk:** If real auth fails, set `HEARTBEAT_MOCK_AUTH=true` to rollback instantly.

### Task 2: Add Device Registration

Create migration `config/schemas/004_devices.sql`:

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
```

Add `device_id` to sessions:
```sql
ALTER TABLE auth.sessions ADD COLUMN IF NOT EXISTS device_id TEXT;
CREATE INDEX IF NOT EXISTS idx_sessions_device ON auth.sessions(device_id);
```

### Task 3: Add App Registration

New endpoint: `POST /api/auth/register-app`

**Request:**
```json
{
  "source_type": "float",
  "source_name": "Float_DESKTOP-PROBOOK",
  "app_version": "2.0.0",
  "machine_guid": "ABC123-DEF456",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "computer_name": "PROBOOK",
  "os_type": "windows",
  "os_version": "Windows 11 Pro",
  "device_id": "a1b2c3d4e5f60001"
}
```

**Response:**
```json
{
  "source_id": "src-float-a1b2c3-001",
  "source_type": "float",
  "source_name": "Float_DESKTOP-PROBOOK",
  "device_id": "a1b2c3d4e5f60001",
  "registered_at": "2026-04-14T10:00:00Z",
  "tenant": {
    "tenant_id": "tenant-abbey-001",
    "company_name": "Abbey Mortgage Bank PLC",
    "tin": "02345678-0001",
    "firs_service_id": "A8BM72KQ",
    "invoice_prefix": "ABB"
  },
  "endpoints": {
    "heartbeat": "http://13.247.224.147:9000",
    "heartbeat_sse": "http://13.247.224.147:9000/api/sse/stream",
    "relay": "http://13.247.224.147:8082",
    "core": "http://13.247.224.147:8080",
    "core_sse": "http://13.247.224.147:8080/api/v1/sse/subscribe"
  },
  "capabilities": {
    "can_upload": true,
    "can_finalize": true,
    "max_file_size_mb": 10.0,
    "allowed_extensions": [".pdf", ".xlsx", ".json", ".csv", ".xml"],
    "bulk_preview_timeout_s": 300
  },
  "feature_flags": {
    "sse_enabled": true,
    "bulk_upload_enabled": true,
    "inbound_review_enabled": true
  },
  "security": {
    "session_timeout_hours": 8,
    "jwt_refresh_minutes": 25,
    "step_up_required_for": ["invoice.finalize", "user.create.admin"]
  }
}
```

**Idempotent:** Same device_id + source_type → return existing registration (update last_seen_at, app_version).

**Auth required:** Bearer JWT (from shared Keyring session or fresh login).

### Task 4: Update Login to Accept device_id

Modify `POST /api/auth/login` to accept optional `device_id` in request body.

In `auth_handler.py` login():
1. If `device_id` provided → check device not revoked
2. Create session with `device_id` column populated
3. Include `device_id` in JWT claims
4. Enforce: max 1 session per device (replace existing)

### Task 5: Enforce 3-Session Limit

In `auth_handler.py` login(), after password verification:
```python
active_count = await db.count_active_sessions(user_id)
if active_count >= 3:
    oldest = await db.get_oldest_active_session(user_id)
    await db.revoke_session(oldest.session_id, reason="evicted_by_new_login")
```

Update `pg_auth.py` with:
- `count_active_sessions(user_id)` — COUNT WHERE is_revoked=false AND session_expires_at > now
- `get_oldest_active_session(user_id)` — ORDER BY issued_at ASC LIMIT 1
- Update `get_tenant_max_sessions()` to return 3 (currently returns 1)

### Task 6: Add Device/Session Management Endpoints

```
GET  /api/auth/devices              — list registered devices for current user
POST /api/auth/devices/{id}/revoke  — revoke a device (admin only)
GET  /api/auth/sessions             — list active sessions for current user
```

### Task 7: Relay Dual-Auth (JWT + HMAC)

Modify Relay's auth middleware to accept JWT in addition to HMAC:

**File:** `services/relay/src/core/auth.py`

Add a new function:
```python
def authenticate_jwt_or_hmac(request, api_key_secrets, tenant_registry):
    auth_header = request.headers.get("authorization", "")
    
    if auth_header.lower().startswith("bearer "):
        # JWT path: verify via HeartBeat introspect
        token = auth_header[7:]
        result = httpx.post(heartbeat_url + "/api/auth/introspect", json={"token": token})
        if result["active"]:
            return tenant_from_jwt_claims(result)
        raise AuthenticationFailedError()
    
    elif request.headers.get("x-api-key"):
        # HMAC path: existing flow
        return authenticate_hmac(request, api_key_secrets)
    
    raise AuthenticationFailedError("No auth credentials provided")
```

**File:** `services/relay/src/api/routes/ingest.py`

Update the `Depends(authenticate_request)` to use the new dual-auth function.

### Task 8: Test Harness (keep mock_auth as test tool)

Rename `mock_auth.py` to be part of the test harness. The `/api/auth/reset` endpoint is useful — but guard it behind the test harness key (`~/.helium/test_harness_key`) instead of `HEARTBEAT_MOCK_AUTH` env var.

See `docs/UNIFIED_AUTH_CONTRACT.md` Section 8 for test harness spec.

---

## VERIFICATION CHECKLIST

After implementation, verify ALL of the following:

1. **Real login works:**
   ```
   POST /api/auth/login {"email":"Charles.Omoakin@abbeymortgagebank.com","password":"123456"}
   → 200, real Ed25519 JWT, is_first_run=true
   ```

2. **First-time password change:**
   ```
   POST /api/auth/password/change {"new_password":"SecurePass2026!"}
   → 200, all sessions revoked
   ```

3. **Re-login with new password:**
   ```
   POST /api/auth/login {"email":"...","password":"SecurePass2026!","device_id":"test-dev-001"}
   → 200, is_first_run=false, device_id in JWT
   ```

4. **Token refresh:**
   ```
   POST /api/auth/token/refresh (Bearer JWT)
   → 200, new JWT, same session_expires_at
   ```

5. **Device registration:**
   ```
   POST /api/auth/register-app {...} (Bearer JWT)
   → 200, source_id + config bundle
   ```

6. **3-session limit:**
   Login from device A, B, C → all active.
   Login from device D → device A's session revoked.

7. **Simulator still works:**
   ```
   POST http://13.247.224.147:8090/api/single {"tenant_id":"abbey"}
   → 200 (Simulator uses HMAC to Relay, NOT HeartBeat auth)
   ```

8. **Relay dual-auth:**
   ```
   # JWT path
   POST /api/ingest (Authorization: Bearer {jwt}) → 200
   
   # HMAC path (Simulator)
   POST /api/ingest (X-API-Key + X-Signature) → 200
   ```

---

---

## FRONTEND IMPLEMENTATION NOTES (Float + Reader Harmony)

These notes are for the frontend teams. HeartBeat provides the backend — frontends must follow these patterns to share sessions correctly.

### Keyring Contract

All Helium desktop apps on the same machine share ONE Keyring entry:

| Field | Value |
|-------|-------|
| **Service name** | `helium` |
| **Account** | `session` |
| **Value** | JSON string (see below) |

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

**All apps MUST use the exact same service name and account** (`helium` / `session`). If Float writes with a different key name than Reader reads, session sharing breaks.

### device_id Computation (MUST be identical across apps)

Every app on the same machine MUST compute the same device_id:

```python
import hashlib
import uuid
import re

def compute_device_id() -> str:
    """Platform-specific machine identity → deterministic device_id."""
    import platform
    
    if platform.system() == "Windows":
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
              r"SOFTWARE\Microsoft\Cryptography")
        machine_guid = winreg.QueryValueEx(key, "MachineGuid")[0]
    elif platform.system() == "Darwin":
        import subprocess
        result = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                capture_output=True, text=True)
        for line in result.stdout.split("\n"):
            if "IOPlatformUUID" in line:
                machine_guid = line.split('"')[-2]
                break
    else:  # Linux
        machine_guid = open("/etc/machine-id").read().strip()
    
    # Get primary MAC address
    mac = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
    
    raw = f"{machine_guid}:{mac}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**CRITICAL:** Float and Reader MUST use this exact same function. If they compute different device_ids, HeartBeat sees them as different devices and creates separate sessions (wasting the 3-session limit).

### App Startup Sequence (EVERY Helium frontend app)

```
┌─────────────────────────────────────────────────┐
│ 1. Compute device_id (machine_guid + mac)       │
│                                                  │
│ 2. Check Keyring: helium/session exists?         │
│    ├── NO → goto STEP 5 (login required)        │
│    └── YES → parse JSON                          │
│                                                  │
│ 3. Is session_expires_at > now?                  │
│    ├── NO → delete Keyring → goto STEP 5        │
│    └── YES → continue                            │
│                                                  │
│ 4. Is expires_at > now?                          │
│    ├── NO → call POST /api/auth/token/refresh   │
│    │    ├── 200 → update Keyring → goto STEP 6  │
│    │    └── 401 → delete Keyring → goto STEP 5  │
│    └── YES → goto STEP 6                        │
│                                                  │
│ 5. SHOW LOGIN DIALOG                            │
│    → user enters email + password                │
│    → POST /api/auth/login {email, pw, device_id}│
│    → if is_first_run: show password change dialog│
│    → write to Keyring → goto STEP 6             │
│                                                  │
│ 6. Check local store: app registration exists?   │
│    ├── YES → proceed to main window              │
│    └── NO → silent call:                         │
│         POST /api/auth/register-app              │
│         {source_type, device_id, machine_guid..} │
│         → store registration locally             │
│         → proceed to main window                 │
│                                                  │
│ 7. MAIN WINDOW — app is fully authenticated      │
│    └── Start 25-min refresh timer                │
│    └── On refresh: update Keyring                │
│    └── On logout: delete Keyring + call logout   │
└─────────────────────────────────────────────────┘
```

### source_type Values

| App | source_type | source_name pattern |
|-----|-------------|-------------------|
| Float | `float` | `Float_{computer_name}` |
| Transforma Reader (desktop) | `transforma_reader` | `TransformaReader_{computer_name}` |
| Transforma Reader (mobile) | `transforma_reader_mobile` | `TransformaReader_{device_model}` |
| Monitoring App | `monitoring` | `Monitor_{computer_name}` |

### Registration Storage

Each app stores its registration response in its OWN local storage (NOT the shared Keyring):

| App | Storage Location |
|-----|-----------------|
| Float | `~/.helium/float/registration.json` |
| Reader | `~/.helium/reader/registration.json` |
| Mobile | App-specific secure storage |

Registration data includes `source_id`, tenant config, endpoints, capabilities, feature flags.

### Token Refresh Coordination

When multiple apps are running on the same machine:
- Each has its own 25-min refresh timer
- Whichever fires first refreshes the token and updates Keyring
- The other app reads the updated Keyring on its next API call
- No file watcher or IPC needed — Keyring is the synchronization point

**Edge case:** Both apps try to refresh at the same moment. HeartBeat handles this gracefully — the second refresh gets a new JWT (the first JWT's `jti` was already replaced). The second app updates Keyring, overwriting the first app's refresh. Both end up with valid tokens.

### Logout Behavior

| Action | Effect |
|--------|--------|
| User clicks Logout in Float | Float calls `POST /api/auth/logout` → deletes Keyring entry → shows login |
| Reader detects missing Keyring | On next API call, Keyring read fails → Reader shows login dialog |
| Session expires (8hr hard cap) | Next token refresh returns 401 → app deletes Keyring → shows login |
| Admin revokes session | Next API call → introspect returns `active: false` → app deletes Keyring → shows login |

### API Call Pattern (all services)

Every frontend-to-service HTTP call includes the JWT:

```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "X-Device-Id": device_id,
    "X-Source-Id": source_id,
    "X-Trace-Id": str(uuid.uuid7()),
}
```

Services verify the JWT and extract:
- `sub` → user_id
- `tenant_id` → data scoping
- `role` + `permissions` → authorization
- `device_id` → audit trail

---

## CRITICAL: DO NOT BREAK

- Simulator must keep working (uses HMAC to Relay, not HeartBeat auth)
- HeartBeat health endpoint must stay up
- If real auth fails catastrophically, set `HEARTBEAT_MOCK_AUTH=true` as rollback
- PostgreSQL auth schema already exists — do NOT recreate (use ALTER TABLE for new columns)
