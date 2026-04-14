# Relay Service — AWS Session Handoff

**Date:** 14 April 2026
**For:** Dedicated Relay_AWS session
**Repo:** `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo`

---

## INSTRUCTIONS

**Before ANY code or plan, you MUST:**
1. Read `docs/HELIUM_DEPLOYMENT_ARCHITECTURE.md` (master architecture)
2. Read `docs/UNIFIED_AUTH_CONTRACT.md` (auth decisions)
3. Read this entire document
4. Read the Transforma Reader codebase at `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader\` — especially:
   - `src/clients/relay_client.py` (Reader's Relay integration)
   - `src/services/submission.py` (submission flow)
   - `src/services/auth_service.py` (auth patterns)
   - `SPEC.md` (architecture rules)
5. Read Float's Relay integration:
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Float\App\src\sdk\clients\relay_client.py`
   - `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Float\App\src\sdk\workers\upload_worker.py`
6. Ask the user ALL clarifying questions
7. Consolidate understanding of auth + registration (Section 3 of HELIUM_DEPLOYMENT_ARCHITECTURE.md)
8. Understand the test harness (elevated user flow for Bob's laptop)
9. Understand multi-tenancy (tenant_id column, API key routing for demo infra)

---

## WHAT THE SESSION MUST DO

### Task 1: Implement JWT Dual-Auth on Relay

**Current state:** Relay only accepts HMAC (`X-API-Key` + `X-Signature`).

**Required:** Relay must accept BOTH JWT and HMAC on `/api/ingest`.

**File:** `services/relay/src/core/auth.py`

Add function:
```python
async def authenticate_jwt_or_hmac(
    request: Request,
    api_key_secrets: dict,
    tenant_registry: dict,
    heartbeat_url: str,
) -> tuple[str, str]:  # Returns (api_key_or_user_id, tenant_id)
```

**Logic:**
1. Check `Authorization: Bearer {jwt}`:
   - If present → POST to HeartBeat `/api/auth/introspect` with `{"token": jwt}`
   - If `active: true` → extract `tenant_id` from claims → return (user_id, tenant_id)
   - Cache introspect result for 30-60 seconds (per jti)
2. Else check `X-API-Key` header:
   - If present → existing HMAC flow → return (api_key, tenant_id)
3. Neither → raise `AuthenticationFailedError`

**File:** `services/relay/src/api/routes/ingest.py`

Update `Depends(authenticate_request)` to use the new dual-auth function.

**Impact:** Float and Reader will send JWT. Simulator and ERP integrations continue using HMAC. Both paths work simultaneously.

### Task 2: Harmonize Reader + Float Relay Contracts

**Reader sends to Relay:**
```python
# From Reader's relay_client.py
relay.submit(pdf_path, user_email, auth_token, metadata)
# Multipart: file + HMAC headers + Bearer JWT
# Returns: {success, irn, qr_data, firs_reference, file_uuid}
```

**Float sends to Relay:**
```python
# From Float's relay_client.py
client.upload_files(file_paths, metadata={...}, jwt_token="...")
# Multipart: files + metadata JSON + call_type + HMAC headers + Bearer JWT
# Returns: {status, queue_id, data_uuid, file_uuids, filenames, irn, qr_code}
```

**Differences to resolve:**

| Aspect | Float | Reader | Relay Must Accept |
|--------|-------|--------|-------------------|
| Auth | HMAC + JWT | HMAC + JWT | **Either HMAC or JWT** (Task 1) |
| Files | Multiple files | Single PDF | Both (already works) |
| call_type | `bulk` or `external` | `external` | Both (already works) |
| Metadata | Full SDK identity (12 fields) | Minimal (email, source) | Both (metadata is optional) |
| Response shape | Full IngestResponse | Subset (irn, qr_data) | Return full, clients parse what they need |

**Action:** Ensure Relay's `/api/ingest` returns a consistent response that BOTH Float and Reader can parse. The existing IngestResponse is fine — Reader just uses fewer fields.

### Task 3: Multi-Tenant Routing (Already Partially Built)

**Current state:** `services/relay/src/core/tenant.py` has `TenantConfig` with `format_type` (flat vs UBL). `tenants.json` has Abbey + ABMFB.

**For JWT-auth requests:** When a frontend sends JWT (no API key), Relay needs to determine the tenant. Two options:
- **From JWT claims:** `tenant_id` is in the JWT payload → Relay reads it directly
- **From HeartBeat introspect response:** `tenant_id` returned in introspect result

**Implementation:** After introspect, look up tenant in `tenant_registry` by `tenant_id` (not by API key). Add a second index to the registry:

```python
# In load_tenants():
by_api_key: Dict[str, TenantConfig] = {}   # existing
by_tenant_id: Dict[str, TenantConfig] = {} # NEW

for tenant_id, cfg in data.items():
    tenant = TenantConfig(...)
    by_api_key[tenant.api_key] = tenant
    by_tenant_id[tenant_id] = tenant
```

### Task 4: Source Identity Tracking

When a request comes from a frontend app (JWT auth), the headers include:
```
X-Device-Id: {device_id}
X-Source-Id: {source_id}
X-Trace-Id: {uuid7}
```

Relay must forward these to HeartBeat (blob metadata) and Core (invoice metadata). Currently Relay forwards `user_trace_id`, `helium_user_id`, etc. from the metadata form field. The new headers are additional — Relay should:
1. Read `X-Source-Id` and `X-Device-Id` from request headers
2. Include them in the metadata sent to HeartBeat's blob write
3. Include them in the Core `/api/v1/enqueue` payload
4. Log them in Relay's own audit entries

### Task 5: IRN/QR Inline Fallback (Already Done)

IRN and QR generators already fall back to inline generation when Transforma modules aren't cached. This was implemented in this session. Verify it still works after the dual-auth changes.

---

## READER'S RELAY INTEGRATION (Reference)

**Key files in Reader:**
- `src/clients/relay_client.py` — httpx-based, HMAC signing, single file upload
- `src/services/submission.py` — SubmitWorker thread, QR stamping after success
- `src/config.py` — relay_url() resolution chain

**Reader's submission flow:**
1. User clicks "Submit to NRS" in ReaderShell
2. SubmitWorker thread starts
3. RelayClient.submit() → HMAC sign → multipart POST → /api/ingest
4. On success: extract IRN → QR stamp → fixed PDF → crossfade animation
5. On offline: enqueue to transforma.db queue table → drain later

**Reader currently uses HMAC.** After Task 1, Reader should switch to JWT (from shared Keyring). This is a Reader code change, not a Relay change — but Relay must accept it.

## FLOAT'S RELAY INTEGRATION (Reference)

**Key files in Float:**
- `src/sdk/clients/relay_client.py` — HMAC signing, multi-file upload, 310s timeout
- `src/sdk/workers/upload_worker.py` — QThread, 3 retries with backoff
- `src/sdk/managers/upload_manager.py` — staging, metadata build, status tracking

**Float's upload flow:**
1. User drags files → BulkContainer
2. UploadManager.stage_files() → sync.db entries
3. UploadWorker → RelayClient.upload_files() → multipart POST → /api/ingest
4. On success: status → "queued", SSE events track processing
5. Core processes → HLX generated → ReviewPage → Finalize

**Float currently uses HMAC + JWT (JWT forwarded in Authorization header).** After Task 1, Float can drop HMAC and use JWT only.

---

## TEST HARNESS AWARENESS

The test harness (`~/.helium/test_harness_key`) is a HeartBeat feature, not Relay's concern. Relay does NOT validate test harness signatures. However:

- Test harness can trigger Relay actions indirectly (e.g., `/api/test/data/seed` on HeartBeat → HeartBeat calls Relay internally)
- Relay's dual-auth must not interfere with test harness operations
- Simulator (HMAC auth) must continue working regardless of test harness state

---

## MULTI-TENANCY AWARENESS

**Demo infrastructure (current):** One Relay serves Abbey + ABMFB. Tenant determined by API key (HMAC) or JWT claims.

**Production (tenant-controlled):** One Relay per tenant. tenant_id is hardcoded in config. Multi-tenant routing is unnecessary but harmless.

**Relay must work in both modes.** If `RELAY_TENANTS_FILE` is set → multi-tenant mode. If not → single-tenant mode (tenant from HeartBeat config).

---

## VERIFICATION CHECKLIST

1. **HMAC still works:** Simulator `/api/single` → Relay → 200 with IRN
2. **JWT works:** `curl -X POST /api/ingest -H "Authorization: Bearer {jwt}" -F "files=@invoice.pdf" -F "call_type=external"` → 200
3. **Wrong JWT rejected:** Expired/invalid JWT → 401
4. **Tenant resolved from JWT:** JWT for Abbey tenant → Relay uses Abbey config
5. **Source headers forwarded:** X-Source-Id and X-Device-Id appear in HeartBeat blob metadata
6. **IRN/QR fallback:** Module cache cold → inline IRN/QR still generated

---

## KEY FILES TO MODIFY

| File | Change |
|------|--------|
| `services/relay/src/core/auth.py` | Add JWT verification via HeartBeat introspect |
| `services/relay/src/core/tenant.py` | Add by_tenant_id index to registry |
| `services/relay/src/api/routes/ingest.py` | Update Depends to use dual-auth |
| `services/relay/src/api/app.py` | Pass HeartBeat URL to auth dependency |
| `services/relay/src/config.py` | Already has heartbeat_api_url — no change needed |
