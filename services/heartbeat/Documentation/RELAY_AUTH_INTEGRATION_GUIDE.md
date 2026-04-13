# Relay Auth Integration Guide

**Version:** 1.0
**Date:** 2026-03-04
**Target:** Relay service team
**Source of Truth:** `HeartBeat/src/api/auth.py` (Pydantic request/response models)

---

## 1. Overview

Relay has a **two-layer authentication model:**

| Layer | Mechanism | Purpose | Status |
|-------|-----------|---------|--------|
| **Request Auth** | HMAC-SHA256 | Authenticates the client application (Float SDK) | Existing, unchanged |
| **User Auth** | JWT introspect via HeartBeat | Authenticates the human user | **New** |

Relay does NOT validate JWTs itself. It forwards user JWTs to HeartBeat's introspect endpoint for verification, permission checking, and step-up freshness evaluation.

---

## 2. Current State (What Exists)

### 2.1 HMAC Request Authentication (Unchanged)

**File:** `Relay/src/api/deps.py` — `authenticate_request()`

Uses `X-API-Key`, `X-Timestamp`, `X-Signature` headers:
```
message = f"{api_key}:{timestamp}:{sha256(body)}"
signature = HMAC-SHA256(api_secret, message)
```

This authenticates the **client application** (Float SDK), not the **user**. Continues to work exactly as-is.

### 2.2 JWT Forwarding (Partially Stubbed)

**File:** `Relay/src/api/routes/ingest.py` — Extracts `Authorization` header
**File:** `Relay/src/clients/heartbeat.py` — `HeartBeatClient` accepts `jwt_token`, uses it as `Bearer {jwt_token}`

JWT is forwarded to HeartBeat as a Bearer header -- already stubbed but not introspected.

### 2.3 X-User-ID Header (To Be Deprecated)

Currently some code extracts `X-User-ID` from request headers. This must be replaced by extracting `user_id` from the JWT introspect response.

---

## 3. Token Introspection

### 3.1 Endpoint

```
POST /api/auth/introspect
Authorization: Bearer {relay_api_key}:{relay_api_secret}
Content-Type: application/json

Request:
{
    "token": "<user JWT from Authorization header>",
    "required_permission": "blob.upload",           // optional
    "required_within_seconds": 300                  // optional (step-up)
}
```

### 3.2 Service Credential Format

HeartBeat's introspect endpoint requires **service-level credentials**, not user JWTs.

Format: `Bearer {api_key}:{api_secret}`

The `verify_service_credentials` dependency (`HeartBeat/src/auth/dependencies.py`) splits on the first colon and validates against `registry.db`.

Relay needs a registered credential. Store as environment variables:
```
RELAY_HEARTBEAT_API_KEY=rl_prod_abc123
RELAY_HEARTBEAT_API_SECRET=secret456
```

### 3.3 Response: Active Token

```json
{
    "active": true,
    "actor_type": "human",
    "user_id": "user-abc-123",
    "role": "operator",
    "permissions": ["invoice.view", "invoice.create", "blob.upload"],
    "tenant_id": "tenant-001",
    "last_auth_at": "2026-03-04T10:00:00Z",
    "expires_at": "2026-03-04T10:30:00Z",
    "session_expires_at": "2026-03-04T18:00:00Z",
    "step_up_satisfied": true
}
```

### 3.4 Response: Inactive Token

```json
{
    "active": false,
    "error_code": "TOKEN_INVALID | TOKEN_REVOKED | SESSION_EXPIRED | PERMISSIONS_CHANGED | STEP_UP_REQUIRED | FIRST_RUN_REQUIRED",
    "message": "Human-readable description"
}
```

### 3.5 Response: Permission Denied

When `required_permission` is specified but user lacks it:

```json
{
    "active": true,
    "actor_type": "human",
    "user_id": "user-abc-123",
    "role": "support",
    "error_code": "PERMISSION_DENIED",
    "message": "User lacks required permission: blob.upload"
}
```

### 3.6 Response: Step-Up Required

When `required_within_seconds` is specified but `last_auth_at` is too old:

```json
{
    "active": true,
    "step_up_satisfied": false,
    "error_code": "STEP_UP_REQUIRED",
    "required_within_seconds": 300,
    "user_id": "user-abc-123",
    "role": "operator"
}
```

---

## 4. HeartBeatClient Changes

### 4.1 Add introspect_token() Method

**File:** `Relay/src/clients/heartbeat.py`

```python
async def introspect_token(
    self,
    user_jwt: str,
    required_permission: Optional[str] = None,
    required_within_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Verify a user JWT via HeartBeat introspect endpoint.

    Uses service-level credentials (not the user JWT).
    """
    body = {"token": user_jwt}
    if required_permission:
        body["required_permission"] = required_permission
    if required_within_seconds is not None:
        body["required_within_seconds"] = required_within_seconds

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{self.heartbeat_api_url}/api/auth/introspect",
            json=body,
            headers={
                "Authorization": f"Bearer {self._service_api_key}:{self._service_api_secret}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()
```

### 4.2 Service Credential Configuration

```python
class HeartBeatClient:
    def __init__(
        self,
        heartbeat_api_url: str,
        jwt_token: Optional[str] = None,         # User JWT passthrough
        service_api_key: Optional[str] = None,    # NEW: for introspect
        service_api_secret: Optional[str] = None, # NEW: for introspect
    ):
        self.heartbeat_api_url = heartbeat_api_url
        self.jwt_token = jwt_token
        self._service_api_key = service_api_key or os.environ.get("RELAY_HEARTBEAT_API_KEY", "")
        self._service_api_secret = service_api_secret or os.environ.get("RELAY_HEARTBEAT_API_SECRET", "")
```

---

## 5. Ingest Route Integration

### 5.1 Updated Flow

**File:** `Relay/src/api/routes/ingest.py`

```
1. HMAC authentication (existing -- validates request integrity)
2. Extract JWT from Authorization header (if present)
3. If JWT present:
   a. Call heartbeat_client.introspect_token(jwt, required_permission="blob.upload")
   b. If active=false:
      - STEP_UP_REQUIRED  -> return 403 with error details
      - TOKEN_INVALID/REVOKED/EXPIRED -> return 401 with error details
      - PERMISSIONS_CHANGED -> return 401 with "PERMISSIONS_CHANGED"
      - FIRST_RUN_REQUIRED -> return 403 with "Account setup incomplete"
   c. If active=true but PERMISSION_DENIED:
      - return 403 with "Insufficient permissions"
   d. If active=true and step_up_satisfied:
      - Extract user_id from introspect response (NOT from X-User-ID)
      - Pass user_id to BulkService for audit/metadata
4. If no JWT: use HMAC api_key as identity (machine-to-machine)
5. Continue with file processing (existing flow)
```

### 5.2 Example Implementation

```python
# In ingest route:
user_jwt = request.headers.get("Authorization", "").replace("Bearer ", "")

if user_jwt:
    introspect = await heartbeat_client.introspect_token(
        user_jwt,
        required_permission="blob.upload",
    )

    if not introspect.get("active", False):
        error_code = introspect.get("error_code", "TOKEN_INVALID")
        status = 403 if error_code in ("STEP_UP_REQUIRED", "FIRST_RUN_REQUIRED") else 401
        raise HTTPException(status_code=status, detail=introspect)

    if introspect.get("error_code") == "PERMISSION_DENIED":
        raise HTTPException(status_code=403, detail=introspect)

    if not introspect.get("step_up_satisfied", True):
        raise HTTPException(status_code=403, detail=introspect)

    # Use introspect-derived user_id instead of X-User-ID header
    user_id = introspect["user_id"]
    tenant_id = introspect["tenant_id"]
```

---

## 6. X-User-ID Deprecation Timeline

| Phase | When | Behavior |
|-------|------|----------|
| **Phase 1** (now) | Immediate | Accept both X-User-ID header and JWT introspect. Prefer JWT when available. |
| **Phase 2** | Next release | Log warning when X-User-ID is used without JWT. |
| **Phase 3** | Removal | Reject requests without JWT for user-scoped operations. X-User-ID no longer read. |

---

## 7. Error Code Mapping

| HeartBeat Error | Relay HTTP | Relay Response |
|-----------------|-----------|---------------|
| `active=true, step_up_satisfied=true` | (proceed) | Continue processing |
| `TOKEN_INVALID` | 401 | `{"error": "Authentication failed"}` |
| `TOKEN_REVOKED` | 401 | `{"error": "Session revoked"}` |
| `SESSION_EXPIRED` | 401 | `{"error": "Session expired"}` |
| `PERMISSIONS_CHANGED` | 401 | `{"error": "Permissions changed, re-login required"}` |
| `STEP_UP_REQUIRED` | 403 | `{"error": "Step-up auth required", "required_within_seconds": N}` |
| `FIRST_RUN_REQUIRED` | 403 | `{"error": "Account setup incomplete"}` |
| `PERMISSION_DENIED` | 403 | `{"error": "Insufficient permissions"}` |

---

## 8. Internal Service Tokens

### 8.1 HeartBeat -> Relay Communication

- HeartBeat calls Relay's `POST /internal/refresh-cache`
- Uses pre-shared `internal_service_token` (already implemented)
- `Relay/src/api/deps.py` `verify_internal_token()` handles this
- **No changes needed** for this direction

### 8.2 Relay -> HeartBeat Service Auth

- For introspect and other service-level calls
- Uses `registry.db` API credentials (`api_key:api_secret` format)
- These credentials must be pre-registered in HeartBeat's registry database
- Relay stores them as `RELAY_HEARTBEAT_API_KEY` and `RELAY_HEARTBEAT_API_SECRET` env vars

---

## 9. Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| HeartBeat unreachable, HMAC-only request | Proceed (Relay authenticates the request itself) |
| HeartBeat unreachable, JWT request | Return 503: "Auth service unavailable, try again" |
| HeartBeat introspect timeout (>5s) | Return 503 (fail-fast) |
| HeartBeat returns unexpected response | Return 500, log error |

**NEVER silently accept unverified JWTs.** If HeartBeat cannot be reached, JWT-bearing requests must fail.

### 9.1 Introspect Result Caching (Optional)

To avoid calling introspect on every request for the same token:
- Cache introspect results for **60 seconds** per `jti` (JWT ID)
- Invalidate cache entry if any error is returned
- Use in-memory dict or Redis (if available)

---

## 10. Config Changes

**File:** `Relay/src/config.py`

Add:

```python
# HeartBeat service credentials for introspect
relay_heartbeat_api_key: str = ""      # RELAY_HEARTBEAT_API_KEY
relay_heartbeat_api_secret: str = ""   # RELAY_HEARTBEAT_API_SECRET
```

---

## 11. Code Changes Summary

| File | Change |
|------|--------|
| `Relay/src/clients/heartbeat.py` | Add `introspect_token()` method |
| `Relay/src/clients/heartbeat.py` | Add service credential config (`_service_api_key`, `_service_api_secret`) |
| `Relay/src/api/routes/ingest.py` | Add JWT introspect before processing; extract user_id from response |
| `Relay/src/config.py` | Add `RELAY_HEARTBEAT_API_KEY`, `RELAY_HEARTBEAT_API_SECRET` env vars |
| `Relay/src/errors.py` | Add `StepUpRequiredError` (403) if not already present |

---

## 12. Platform Services (Transforma Modules + FIRS Keys)

### 12.1 Endpoint

```
GET /api/platform/transforma/config
Authorization: Bearer {api_key}:{api_secret}
```

Relay's `TransformaModuleCache` calls this endpoint at startup and every 12 hours to fetch IRN/QR generator source code and FIRS service keys.

### 12.2 Authentication

Uses **service credentials** (same `verify_service_credentials` dependency as introspect). Relay authenticates with `RELAY_HEARTBEAT_API_KEY` / `RELAY_HEARTBEAT_API_SECRET`.

### 12.3 Response

```json
{
    "modules": [
        {
            "module_name": "irn_generator",
            "source_code": "def generate_irn(invoice_data: dict) -> str:\n    ...\n",
            "version": "1.0.0",
            "checksum": "sha256:abc123...",
            "updated_at": "2026-03-04T14:00:00Z"
        },
        {
            "module_name": "qr_generator",
            "source_code": "import base64, json\n\ndef generate_qr_data(irn, keys=None):\n    ...\n",
            "version": "1.0.0",
            "checksum": "sha256:def456...",
            "updated_at": "2026-03-04T14:00:00Z"
        }
    ],
    "service_keys": {
        "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\n...",
        "csid": "CSID-TOKEN",
        "csid_expires_at": "2026-06-01T00:00:00Z",
        "certificate": "base64-cert-data"
    }
}
```

### 12.4 Data Source

HeartBeat stores Transforma modules in `config.db` (the config_entries table):
- `service_name="transforma"`, `config_key="irn_generator"` (JSON)
- `service_name="transforma"`, `config_key="qr_generator"` (JSON)
- `service_name="transforma"`, `config_key="service_keys"` (JSON)

In production, the Installer seeds these entries. Stub modules are provided for development.

### 12.5 Contract Match

This endpoint matches the existing `Relay/src/clients/heartbeat.py` `HeartBeatClient.get_transforma_config()` contract and `Relay/src/core/module_cache.py` `TransformaModuleCache.load_all()` expected format exactly. No changes needed on the Relay side.

---

## 13. Multi-File Dedup Model (Q4)

### 13.1 Per-File SHA256

- Each file is hashed individually: `SHA256(file_content_bytes)`
- No filename is included in the hash
- Each file is checked individually via `GET /api/dedup/check?file_hash={sha256}`

### 13.2 One blob_entries Row Per File

- Relay sends one `POST /api/blobs/write` per file (not concatenated)
- HeartBeat creates one `blob_entries` row per file
- Each file gets its own `blob_uuid`

### 13.3 Multi-File Grouping

- Multi-file requests (1-3 files per `data_uuid`) share a `batch_uuid`
- `blob_batches` table groups related files
- `blob_batch_entries` is the join table (batch_uuid + blob_uuid)

### 13.4 Dedup Table Constraint

```sql
-- blob_deduplication: per-file granularity
UNIQUE(file_hash, source_system)
```

Same file content from the same source system is rejected as duplicate. Different source systems uploading the same file are NOT considered duplicates.

---

## 14. Blob Write Auth Model Update

### 14.1 JWT Validation Moved to HeartBeat

HeartBeat now validates user JWTs directly on blob write/register endpoints. Relay no longer needs to call `/api/auth/introspect` before uploading files.

**Updated blob write flow:**

```
1. SDK sends file to Relay (HMAC + JWT)
2. Relay validates HMAC (request integrity)
3. Relay forwards to HeartBeat:
   POST /api/blobs/write
   Authorization: Bearer {user_jwt}       ← user JWT (optional)
   Form: blob_uuid, filename, file, metadata  ← SDK identity fields as JSON
4. HeartBeat validates JWT in-process (Ed25519, no HTTP call)
5. HeartBeat stores file + identity metadata on blob_entries
6. Returns {blob_uuid, blob_path, file_hash, status}
```

### 14.2 JWT is Optional

Machine-to-machine uploads (HMAC-only, no human user) skip JWT validation. If `Authorization` header is absent, blob write proceeds without user identity.

### 14.3 Identity Metadata Form Field

Relay passes SDK identity fields as a JSON-encoded `metadata` form field:

```json
{
    "user_trace_id": "ut-abc-123",
    "x_trace_id": "xt-def-456",
    "float_id": "fl-ghi-789",
    "machine_guid": "AAAA-BBBB-CCCC",
    "mac_address": "00:11:22:33:44:55",
    "computer_name": "WORKSTATION-01"
}
```

HeartBeat merges these with JWT-derived fields (`helium_user_id` from `sub`, `session_id` from `jti`) and stores all 8 identity columns on `blob_entries`.

### 14.4 HeartBeatClient.write_blob() Already Has the Contract

The existing `Relay/src/clients/heartbeat.py` `write_blob()` signature already accepts `metadata` and `jwt_token` parameters. In Phase 2 (real HTTP), Relay should:
- Set `Authorization: Bearer {jwt_token}` header
- Set `metadata` as a JSON-encoded form field alongside `blob_uuid`, `filename`, `file`
