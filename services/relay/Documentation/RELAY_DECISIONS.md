# Relay Implementation - Design Decisions

**Version**: 1.1
**Last Updated**: 2026-03-07
**Status**: BINDING FOR ALL PHASES

---

## Executive Summary

This document captures all **design decisions** made for Relay implementation. These are **non-negotiable constraints** that all Claude variants must follow.

**Decision Hierarchy**: RELAY_SERVICE_CONTRACT.md (authoritative) > RELAY_DECISIONS.md (this file)

> **Note (v1.1)**: RELAY_ARCHITECTURE.md and RELAY_BULK_SPEC.md were deleted on 2026-03-07 — fully superseded by RELAY_SERVICE_CONTRACT.md v2.0. Three decisions below (1B, 3A, field names) have been annotated as SUPERSEDED where the CONTRACT overrides them.

---

## SERVICE ARCHITECTURE

### Decision 1A: Relay Service Types (Standardized Taxonomy)

**Status**: ✅ APPROVED

**Decision**: Relay services are categorized as:

| Type | Purpose | Phase | Status |
|------|---------|-------|--------|
| **Bulk** | HTTP multipart upload (1-3 files) from Float UI | Phase 1B | ✅ IMPLEMENT |
| **Queue** | Internal relay queue for processing backlog | Phase 2+ | 🔄 DEFER |
| **Watcher** | File system monitoring (NAS, SMB, external repos) | Phase 2+ | 🔄 DEFER |
| **DBC** | Database connectivity (ODBC/JDBC) | Phase 2+ | 🔄 DEFER |
| **API** | Webhook/custom HTTP endpoints | Phase 2+ | 🔄 DEFER |
| **Polling** | Time-based polling of external sources | Phase 2+ | 🔄 DEFER |
| **Email** | Email attachment processing | Phase 2+ | 🔄 DEFER |

**Rationale**:
- Clear, standard naming across all Helium services
- Single mental model (not "NAS Relay" vs "RelayNAS" vs different naming)
- Future implementations use same pattern
- Deferred types are stubbed in Phase 1A to prevent code churn

**Implementation**:
- All relay types inherit from BaseRelayService
- Deferred types have stub classes with NotImplementedError
- Each type follows same structure: service class → HTTP handlers → validation → async workflow

**No Exceptions**: This taxonomy is final.

---

### Decision 1B: Single FastAPI App (Not Multiple Services)

**Status**: ✅ APPROVED — endpoint list SUPERSEDED by CONTRACT v2.0 (see below)

**Decision**: All relay types run in **ONE FastAPI application** on a single port (8082), not separate services.

> **⚠️ SUPERSEDED (v1.1)**: The endpoint list below is stale. Per RELAY_SERVICE_CONTRACT.md v2.0:
> - `POST /api/bulk/ingest` → renamed to **`POST /api/ingest`** (handles both bulk and external flows)
> - `POST /api/bulk/finalize` → **REMOVED** (finalize is Core's endpoint, not Relay's)
> - `GET /api/bulk/status/{queue_id}` → **REMOVED** (queue status is Core's responsibility)
> - Added: `POST /internal/refresh-cache` (HeartBeat pushes module refresh)
>
> The single-app architecture decision itself remains valid.

**Structure** _(stale — see CONTRACT v2.0 for current endpoints)_:
```
Relay Service (Single FastAPI App)
├── POST /api/ingest              ← CURRENT (renamed from /api/bulk/ingest)
├── POST /internal/refresh-cache  ← CURRENT (added in Phase 1C)
├── POST /api/queue/enqueue (Deferred)
├── POST /api/watcher/scan (Deferred)
├── POST /api/dbc/query (Deferred)
├── POST /api/webhook/ingest (Deferred)
├── POST /api/polling/start (Deferred)
├── POST /api/email/process (Deferred)
├── GET /health
└── GET /metrics
```

**Rationale**:
- **Resource efficiency**: One process, one connection pool to Core/HeartBeat/MinIO
- **Operational simplicity**: One config file, one docker image, one health check
- **Architectural alignment**: Services not microservices philosophy
- **Scaling**: Load balancer (nginx) distributes across multiple Relay instances, not one instance per type

**Alternatives Rejected**:
- ❌ Multiple FastAPI apps (one per relay type) — wasteful, complex deployment
- ❌ Relay type selected via query parameter — awkward, not RESTful

**No Exceptions**: This is the architecture. All relay types share this app.

---

### Decision 1C: Inheritance Pattern (BaseRelayService)

**Status**: ✅ APPROVED

**Decision**: All relay service types inherit from **BaseRelayService** and call `super().ingest_file()`.

**Pattern**:
```python
class BaseRelayService:
    async def ingest_file(self, file_data):
        # Shared logic: deduplication, blob write, error handling
        ...

class RelayBulkService(BaseRelayService):
    async def ingest_file(self, file_data):
        # Override to add Bulk-specific logic
        # Call super() for shared functionality
        ...
```

**Rationale**:
- DRY principle — deduplication, HeartBeat integration, error handling are shared
- Consistency — all relay types follow same pattern
- Testability — test base class once, test overrides separately
- Future-proofing — adding new relay types is straightforward

**Shared Responsibilities** (BaseRelayService):
- Deduplication (local cache + HeartBeat check)
- HeartBeat integration (blob write, status checks, usage limits)
- Error handling (retry logic, graceful degradation)
- Audit logging
- Trace ID propagation

**Type-Specific Responsibilities** (Override ingest_file):
- Input validation (file extensions, sizes, counts)
- Data transformation (ZIP creation for Bulk, SQL queries for DBC, etc.)
- Pre-processing before Core enqueue

**No Exceptions**: All relay types must inherit from BaseRelayService.

---

## INTEGRATION ARCHITECTURE

### Decision 2A: Blob Storage Access (Via HeartBeat Only)

**Status**: ✅ APPROVED

**Decision**: Relay **never accesses MinIO directly**. All blob operations go through **HeartBeat API**.

**Access Pattern**:
```
Relay Bulk Service
    ↓
HeartBeat Client.write_blob(file_uuid, data)
    ↓
HeartBeat Service
    ├── Validates blob
    ├── Writes to MinIO
    ├── Stores metadata
    └── Manages 7-day cleanup
    ↓
HeartBeat responds with blob_path
    ↓
Relay uses blob_path in Core enqueue call
```

**Rationale**:
- **Security**: MinIO credentials only in HeartBeat, not in Relay
- **Encapsulation**: HeartBeat owns blob lifecycle (retention, cleanup)
- **Consistency**: Single source of truth for blob metadata
- **Resilience**: If MinIO needs replacement, only HeartBeat changes

**Restrictions**:
- ❌ Relay cannot call MinIO directly
- ❌ Relay cannot store MinIO credentials
- ❌ Relay cannot manage blob lifecycle

**HeartBeat APIs Relay Must Use**:
- `POST /api/blob/write` — Write file to MinIO
- `GET /api/blob/{uuid}/metadata` — Get blob metadata (including preview URLs)
- `GET /api/daily_usage/check` — Check daily file quota
- `POST /api/duplicate/check` — Check if file already processed
- `POST /api/duplicate/record` — Record file hash for future dedup

**No Exceptions**: This is the architectural boundary.

---

### Decision 2B: Service Registry (Eureka)

**Status**: ✅ APPROVED

**Decision**: All inter-service discovery uses **Eureka** (or Eureka-compatible service registry).

**Two-Tier Approach**:

**Test/Standard Tier** (Mock Eureka):
```python
# In-memory mock, no external dependencies
registry = MockEurekaClient()
core_url = registry.get_service("core-api")["url"]  # Returns "http://localhost:8080"
```

**Pro/Enterprise Tier** (Real Eureka):
```python
# Real Eureka server (Spring Cloud Eureka, Consul, or etcd)
registry = EurekaClient(eureka_url="http://eureka:8761")
core_url = registry.get_service("core-api")["url"]  # Queries live registry
```

**Configuration**:
```json
{
  "service_registry": {
    "type": "mock",  // Test/Standard
    "eureka_url": "http://eureka:8761",  // Pro/Enterprise (ignored if type=mock)
    "services": {
      "relay-bulk": {"url": "http://localhost:8082", "health_check": "/health"},
      "core-api": {"url": "http://localhost:8080", "health_check": "/health"},
      "heartbeat": {"url": "http://localhost:9000", "health_check": "/health"}
    }
  }
}
```

**Rationale**:
- **Single mental model**: All tiers use same registry code
- **No hardcoding**: Services discovered dynamically
- **Scaling**: Pro/Enterprise adds Relay instances without code changes
- **Resilience**: Fallback to config-defined URLs if registry unavailable

**No Hardcoding Rule**:
- ❌ Do NOT hardcode service URLs (except localhost:8082 for Relay itself in Test/Standard)
- ✅ Always query registry, fallback to config

**No Exceptions**: This is the discovery mechanism for all services.

---

### Decision 2C: Core API Interaction (Async/Await)

**Status**: ✅ APPROVED

**Decision**: Relay calls Core API **asynchronously** with proper timeout handling.

**Call Pattern**:
```python
# Phase 1B: RelayBulkService
async def ingest_file(self, file_data):
    # 1. Dedup check
    if is_duplicate:
        return {"status": "duplicate"}

    # 2. Write to blob (HeartBeat)
    blob_path = await heartbeat_client.write_blob(...)

    # 3. Enqueue to Core
    queue_id = await core_client.enqueue(blob_path=blob_path, ...)

    # 4. Process preview (with timeout)
    try:
        preview_data = await asyncio.wait_for(
            core_client.process_preview(queue_id),
            timeout=300  # 5 minutes
        )
        return {"status": "processed", "preview_data": preview_data}

    except asyncio.TimeoutError:
        # Core is taking too long (large batch)
        return {"status": "queued", "queue_id": queue_id}

    except CoreUnavailableError:
        # Core is down, but file is safely queued
        return {"status": "queued", "queue_id": queue_id}
```

**Timeout Behavior**:
- **Initial write (blob + enqueue)**: 10 seconds max
- **Processing (Core preview)**: 300 seconds (5 minutes) max
- **On timeout**: Return "queued" status (file is safe, processing continues)
- **User experience**: Float UI shows spinner, waits for callback when preview ready

**Rationale**:
- **Non-blocking**: Relay doesn't wait forever for Core
- **Resilience**: Core unavailable → user still gets success response
- **Scalability**: Large batches (1000+ invoices) may take 5+ minutes

**No Exceptions**: This timeout pattern is mandatory.

---

## BULK UPLOAD SPECIFICS

### Decision 3A: Multiple File Handling

**Status**: ⚠️ **SUPERSEDED** by RELAY_SERVICE_CONTRACT.md v2.0

> **⚠️ SUPERSEDED (v1.1)**: The ZIP-all approach below was **reversed** during Phase 1D contract alignment. Per CONTRACT v2.0:
> - **No ZIP packaging**. Multi-file requests are forwarded as **multipart** to HeartBeat.
> - One `data_uuid` maps to all files in a request.
> - Field name: `filenames: List[str]` (not `filename: str`).
> - Field name: `data_uuid` (not `file_uuid`).
>
> The original decision below is preserved for historical context only.

~~**Decision**: When uploading 2-3 files, **ZIP them together** and send as single batch to Core.~~

**Original Logic** _(no longer applies)_:
```python
# HISTORICAL — replaced by multipart forwarding
def process_files(files):
    if len(files) == 1:
        return files[0].data, f"{uuid}-{files[0].name}"
    else:
        zip_buffer = create_zip()
        for file in files:
            add_to_zip(zip_buffer, file.name, file.data)
        return get_zip_bytes(zip_buffer), f"{uuid}-bulk.zip"
```

**Current Approach** (CONTRACT v2.0):
- Multi-file requests forwarded as multipart to HeartBeat
- One `data_uuid` covers all files in the request
- No ZIP creation, no ZIP extraction — simpler pipeline

---

### Decision 3B: Deduplication (Defensive Double-Check)

**Status**: ✅ APPROVED

**Decision**: Deduplication uses **two-level check**: local session cache + HeartBeat persistent check.

**Pattern**:
```python
async def check_duplicate(file_data):
    file_hash = SHA256(file_data)

    # Level 1: Local session cache (current batch)
    if file_hash in self.session_cache:
        return {"status": "duplicate", "source": "session_cache"}

    # Level 2: HeartBeat persistent check (across all uploads)
    hb_result = await heartbeat_client.check_duplicate(file_hash)
    if hb_result["is_duplicate"]:
        return {
            "status": "duplicate",
            "source": "heartbeat",
            "original_queue_id": hb_result["queue_id"]
        }

    # Not a duplicate
    return {"status": "new"}
```

**Rationale**:
- **Batch dedup**: Local cache catches duplicates within same upload (user error)
- **Persistent dedup**: HeartBeat catches duplicates across sessions (resilience)
- **Defensive**: Both levels check independently
- **Performance**: Local cache is fast (memory lookup); HeartBeat is fallback

**Session Cache**:
- Scoped to single HTTP request
- Cleared after request completes
- Different browser sessions have different caches

**No Exceptions**: All files must be checked at both levels.

---

### Decision 3C: HMAC Signature Verification (5-Minute Window)

**Status**: ✅ APPROVED

**Decision**: All ingest requests must include **HMAC-SHA256 signature** with **5-minute timestamp window**.

**Request Headers**:
```
X-API-Key: client_api_key_12345
X-Timestamp: 2026-01-31T10:00:00Z
X-Signature: a1b2c3d4e5f6...
```

**Signature Computation** (Client side):
```
body_hash = SHA256(request_body_bytes)
message = "{api_key}:{timestamp}:{body_hash}"
signature = HMAC-SHA256(secret, message)
```

**Verification** (Server side, Relay):
```python
# 1. Check timestamp (5-minute window)
if abs(now - parse_iso(timestamp)) > 300:
    return Error(401, "Timestamp expired")

# 2. Lookup secret for API key
secret = config.db.get_secret(api_key)

# 3. Recompute signature
computed_sig = HMAC-SHA256(secret, message)

# 4. Constant-time comparison
if secure_compare(signature, computed_sig):
    return Success()
else:
    return Error(401, "Signature mismatch")
```

**Rationale**:
- **Authentication**: Verifies request came from authorized client
- **Integrity**: Detects tampering during transit
- **Replay attack prevention**: 5-minute window prevents old requests being replayed
- **Constant-time comparison**: Prevents timing attacks

**5-Minute Window Rationale**:
- **Tolerance**: Allows for clock skew between client & server (typical: 2-3 seconds)
- **Security**: Prevents replay attacks beyond 5 minutes
- **Production standard**: AWS, Google, etc. use similar windows

**No Exceptions**: This signature is mandatory for all ingest requests.

---

### Decision 3D: Error Response Format (Standardized JSON)

**Status**: ✅ APPROVED

**Decision**: All error responses follow **single standardized format**.

**Format**:
```json
{
  "status": "error",
  "error_code": "ERROR_CODE_ENUM",
  "message": "Human-readable error message",
  "details": [
    {
      "field": "filename",
      "error": "File size exceeds limit"
    }
  ],
  "request_id": "req_550e8400-...",
  "timestamp": "2026-01-31T10:00:00Z"
}
```

**Error Codes** (from RELAY_BULK_SPEC.md):
```
VALIDATION_FAILED (400)
NO_FILES_PROVIDED (400)
TOO_MANY_FILES (400)
MALWARE_DETECTED (400)
AUTHENTICATION_FAILED (401)
INVALID_API_KEY (401)
RATE_LIMIT_EXCEEDED (429)
INTERNAL_ERROR (500)
SERVICE_UNAVAILABLE (503)
```

**Rationale**:
- **Consistency**: Clients expect same format for all errors
- **Debuggability**: request_id allows tracing
- **User feedback**: message field is human-readable
- **Details**: details field provides specific error per field

**No Exceptions**: Every error must match this format.

---

## OPERATIONAL DECISIONS

### Decision 4A: Test/Standard Deployment (Subprocess in FloatWindow)

**Status**: ✅ APPROVED

**Decision**: In Test/Standard tier, **Relay runs as subprocess spawned by FloatWindow**, not as separate service.

**Startup Pattern**:
```python
# In FloatWindow.__init__()
def start_relay_service(self):
    # Spawn Relay as subprocess
    self.relay_process = subprocess.Popen(
        [sys.executable, "-m", "helium.relay.bulk.main"],
        env={"PORT": "8082", "BLOB_PATH": "/tmp/helium_blobs", "REGISTRY_TYPE": "mock"}
    )

    # Wait for /health to respond (max 6 seconds)
    for attempt in range(30):  # 30 × 200ms
        try:
            response = requests.get("http://localhost:8082/health")
            if response.status_code == 200:
                return  # Ready!
        except:
            pass
        time.sleep(0.2)

    # Log warning but continue (graceful degradation)
    logger.warning("Relay service startup timeout")

def closeEvent(self):
    # Gracefully shutdown on Float close
    if self.relay_process:
        self.relay_process.terminate()
        self.relay_process.wait(timeout=5)
```

**Rationale**:
- **Isolation**: If Relay crashes, doesn't crash Float
- **Self-contained**: No separate service to manage
- **Resource efficient**: Single machine, no network overhead
- **Bundled**: Float installer includes Relay bytecode

**Pro/Enterprise** (Different pattern):
- Relay runs as Docker container (separate from Float)
- Float connects to remote Relay service
- Multiple Relay instances behind nginx load balancer

**No Exceptions**: Test/Standard always uses subprocess pattern.

---

### Decision 4B: Float UI Blocking (No Polling)

**Status**: ✅ APPROVED

**Decision**: When uploading files, **Float UI blocks** and waits synchronously (with spinner/overlay).

**User Experience**:
```
User clicks "Send to Queue"
    ↓
overlay_2 shows (semi-transparent blocking overlay)
loader starts (animated progress bar)
cursor shows WaitCursor
    ↓
Float UI awaits (HTTP request to POST /api/bulk/ingest)
    ↓
One of two things happens:
    A) Core responds with preview data (within 300 seconds)
       → Display preview immediately
       → User reviews/edits

    B) Timeout occurs (Core still processing large batch)
       → Return "queued" status
       → Float async listener checks MinIO for preview data
       → When ready, pop up preview automatically
       → User reviews/edits
    ↓
User clicks "Finalize"
    ↓
POST /api/bulk/finalize
    ↓
Spinner stops, overlay_2 hides
```

**Rationale**:
- **User clarity**: Clear that upload is in progress
- **No confusion**: No polling in background (user unaware of status)
- **Modern UX**: Blocking overlay is expected for long operations
- **Responsive**: Float SDK handles timeout gracefully

**No Polling in Loop**:
- ❌ Do NOT poll `/api/bulk/status` every 500ms
- ✅ Use timeout + async listener instead

**No Exceptions**: This is the blocking pattern.

---

### Decision 4C: Graceful Degradation (Core Unavailable)

**Status**: ✅ APPROVED

**Decision**: When Core API is unavailable, return **"queued" status** (don't fail the upload).

**Pattern**:
```python
try:
    # Attempt Core enqueue + processing
    preview_data = await core_client.process_preview(queue_id, timeout=300)
    return {"status": "processed", "preview_data": preview_data}

except CoreUnavailableError:
    # Core is down, but file is safely queued in blob storage
    # HeartBeat will reconcile later
    return {
        "status": "queued",
        "message": "File accepted. Processing will resume when Core is available.",
        "queue_id": queue_id
    }
except asyncio.TimeoutError:
    # Core is taking too long (large batch)
    return {
        "status": "queued",
        "message": "File accepted. Preview is being processed, check back shortly.",
        "queue_id": queue_id
    }
```

**User Impact**:
- ✅ Upload succeeds (blob is safe)
- ⏳ User sees "queued, retrying" message
- 🔄 Float SDK monitors for preview data arrival
- No loss of data

**Rationale**:
- **Resilience**: Service outage doesn't break user workflow
- **Data safety**: Blob is written before Core call
- **Transparency**: User knows file is queued, not lost
- **Operational**: Admin can investigate Core issues without user impact

**No Exceptions**: This is the resilience pattern.

---

### Decision 4D: Retry Logic (5 Attempts, Exponential Backoff)

**Status**: ✅ APPROVED

**Decision**: On transient failures, **retry with exponential backoff** (max 5 attempts).

**Pattern**:
```python
async def call_with_retries(func, max_attempts=5, initial_delay=1):
    for attempt in range(max_attempts):
        try:
            return await func()
        except TransientError as e:
            if attempt < max_attempts - 1:
                delay = initial_delay * (2 ** attempt)  # 1, 2, 4, 8, 16 seconds
                await asyncio.sleep(delay)
            else:
                raise
```

**Transient vs Permanent Errors**:
- **Transient** (retry): Connection timeout, 503 Service Unavailable, connection reset
- **Permanent** (fail fast): 401 Unauthorized, 404 Not Found, 400 Bad Request

**When All Retries Fail**:
- Call HeartBeat `/api/reconcile` (best-effort)
- Return "queued" status (file is safe in blob)
- Log error for admin investigation

**Rationale**:
- **Resilience**: Temporary network blips don't fail the request
- **Exponential backoff**: Prevents overwhelming services
- **No infinite loops**: 5 attempts max (~31 seconds total)

**No Exceptions**: This retry pattern is mandatory.

---

### Decision 4E: Audit Logging (Mandatory)

**Status**: ✅ APPROVED

**Decision**: **All significant events** are logged to audit trail (via AuditAPIClient).

**Events to Log**:
1. **Batch ingestion started**: batch_id, file_count, total_size
2. **Individual file ingested**: file_uuid, filename, file_size, queue_id
3. **Batch ingestion completed**: successful_count, duplicate_count, failed_count
4. **Errors**: error_code, filename, details
5. **Authentication failures**: api_key, error
6. **Rate limit exceeded**: api_key, current_usage, limit

**Log Format** (structured JSON):
```json
{
  "timestamp": "2026-01-31T10:00:00Z",
  "service": "relay-bulk",
  "event_type": "batch.ingestion.started",
  "batch_id": "batch_550e8400-...",
  "api_key": "client_api_key_12345",
  "total_files": 2,
  "total_size_mb": 15.3
}
```

**Rationale**:
- **Compliance**: Audit trail for financial/invoice processing
- **Debugging**: Trace requests end-to-end
- **Analytics**: Usage patterns, error rates
- **Security**: Detect suspicious activity

**No Exceptions**: All events must be logged.

---

## MONITORING & OBSERVABILITY

### Decision 5A: Prometheus Metrics (Mandatory)

**Status**: ✅ APPROVED

**Decision**: Relay exports **Prometheus metrics** on `/metrics` endpoint.

**Metrics**:
```
# Files ingested
helium_relay_files_ingested_total{relay_type="bulk", status="success"}
helium_relay_files_ingested_total{relay_type="bulk", status="duplicate"}
helium_relay_files_ingested_total{relay_type="bulk", status="error"}

# Processing latency (histogram)
helium_relay_processing_duration_seconds{relay_type="bulk", quantile="0.95"}

# Error rates
helium_relay_errors_total{relay_type="bulk", error_code="VALIDATION_FAILED"}

# Health status
helium_relay_health_status{service="core_api"}  # 1=healthy, 0=unhealthy
```

**Rationale**:
- **Visibility**: Admin can monitor Relay performance in real-time
- **Alerting**: Prometheus rules trigger on error spikes, latency degradation
- **Lightweight**: Minimal overhead (<1% CPU/memory)
- **Standard**: Industry-standard (Kubernetes, Cloud Native Computing Foundation)

**No Exceptions**: Metrics endpoint is mandatory.

---

### Decision 5B: Structured JSON Logging (Stdout)

**Status**: ✅ APPROVED

**Decision**: All logs are **structured JSON** sent to **stdout** (not files).

**Format**:
```json
{
  "timestamp": "2026-01-31T10:00:00Z",
  "level": "INFO",
  "service": "relay-bulk-1",
  "request_id": "req_550e8400-...",
  "event": "file.ingested",
  "file_uuid": "550e8400-...",
  "filename": "invoice1.pdf",
  "file_size_mb": 8.2,
  "queue_id": "queue_123",
  "processing_time_ms": 1500
}
```

**Why stdout (not files)**:
- **Container-native**: Docker logs captured automatically
- **Log aggregation**: Fluentd/ELK/Splunk can parse JSON
- **No disk space issues**: Logs don't consume local disk
- **Ephemerality**: Logs exist for container lifetime, deleted on restart

**Rationale**:
- **Debuggability**: Structured logs are queryable (grep/jq)
- **Tracing**: request_id propagated across all logs
- **Performance**: Stdout is faster than file I/O
- **Operations**: Matches Docker best practices

**No Exceptions**: All logs must be JSON to stdout.

---

## CONFIGURATION

### Decision 6A: Config Source (Admin Packager)

**Status**: ✅ APPROVED

**Decision**: Configuration comes from **Admin Packager** (primary) or **environment variables** (override).

**Priority Order**:
1. Environment variables (highest - overrides file)
2. Admin Packager JSON (config section)
3. Default values (lowest)

**Config Schema**:
```json
{
  "relay": {
    "bulk": {
      "enabled": true,
      "port": 8082,
      "api_key": "PLAINTEXT:relay_api_key_12345",
      "max_file_size_mb": 10,
      "max_files_per_request": 3,
      "allowed_extensions": [".pdf", ".xml", ".xlsx"],
      "daily_limit_per_company": 500,
      "preview_mode": true,
      "timeout_seconds": 300
    },
    "service_registry": {
      "type": "mock",  // or "eureka"
      "eureka_url": "http://eureka:8761"
    }
  }
}
```

**Rationale**:
- **Single source of truth**: Admin Packager is configuration authority
- **Environment overrides**: Testing/deployment can override without file edits
- **No hardcoding**: All config externalized
- **Encryption**: Sensitive fields (api_key, secrets) encrypted in Admin Packager

**No Exceptions**: This is the config hierarchy.

---

### Decision 6B: No Hardcoded URLs (Service Discovery)

**Status**: ✅ APPROVED

**Decision**: **Never hardcode service URLs**. Always use service registry with fallback.

**Pattern**:
```python
# WRONG (hardcoded):
core_url = "http://localhost:8080"  # ❌ NO

# RIGHT (service registry):
registry = get_service_registry()  # Mock or Real based on config
core_service = registry.get_service("core-api")
core_url = core_service["url"]  # From Eureka or config
```

**Exceptions**:
- ✅ Relay itself on localhost:8082 (Test/Standard only, for Float subprocess launch)
- ❌ All other services must be discovered

**Rationale**:
- **Flexibility**: Services can move without code changes
- **Scaling**: New instances registered in Eureka automatically
- **Resilience**: Fallback to config if Eureka unavailable

**No Exceptions**: Use service registry for all inter-service communication.

---

## SUMMARY TABLE

| Category | Decision | Status |
|----------|----------|--------|
| **Service Types** | Bulk, Queue, Watcher, DBC, API, Polling, Email (7 types) | ✅ APPROVED |
| **App Architecture** | Single FastAPI app, multiple endpoints | ✅ APPROVED (endpoints updated in CONTRACT v2.0) |
| **Inheritance** | All relay types inherit from BaseRelayService | ✅ APPROVED |
| **Blob Access** | Via HeartBeat API only (not direct MinIO) | ✅ APPROVED |
| **Service Discovery** | Eureka (Mock for Test/Standard, Real for Pro/Enterprise) | ✅ APPROVED |
| **Core Integration** | Async/await with 5-minute timeout | ✅ APPROVED |
| **Multiple Files** | ~~ZIP together~~ → Multipart forwarding | ⚠️ SUPERSEDED by CONTRACT v2.0 |
| **Deduplication** | Local cache + HeartBeat check (defensive) | ✅ APPROVED |
| **HMAC** | HMAC-SHA256 with 5-minute timestamp window | ✅ APPROVED |
| **Errors** | Standardized JSON format with error codes | ✅ APPROVED |
| **Deployment (T/S)** | Subprocess in FloatWindow | ✅ APPROVED |
| **UX** | Blocking (no polling), spinner/overlay | ✅ APPROVED |
| **Degradation** | Core down → "queued" status (not error) | ✅ APPROVED |
| **Retries** | Exponential backoff, 5 attempts max | ✅ APPROVED |
| **Audit** | All events logged to audit trail | ✅ APPROVED |
| **Metrics** | Prometheus /metrics endpoint | ✅ APPROVED |
| **Logging** | Structured JSON to stdout | ✅ APPROVED |
| **Config** | Admin Packager + environment variables | ✅ APPROVED |
| **URLs** | Service registry (no hardcoding) | ✅ APPROVED |

---

## HOW TO USE THIS DOCUMENT

**For All Claude Variants**:
1. Read RELAY_DECISIONS.md FIRST (this document)
2. Understand each decision and rationale
3. FOLLOW all decisions without exception
4. If conflict arises, ask user (don't override)

**For Haiku (Phase 1A)**:
- Implement all infrastructure (clients, registry, config)
- Respect all architecture decisions

**For Sonnet (Phase 1B)**:
- Implement Bulk service following all integration decisions
- Respect all operational decisions (timeouts, retries, degradation)

**For Opus (Phase 1C)**:
- Test all decisions are implemented correctly
- Validate Float SDK follows UX decisions
- Ensure deployment follows all operational decisions

---

## REVISION HISTORY

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-01-31 | Initial decisions finalized |
| 1.1 | 2026-03-07 | Annotated 1B (endpoints), 3A (ZIP→multipart), field names as SUPERSEDED by CONTRACT v2.0. Deleted ARCHITECTURE + BULK_SPEC references. |

---

**These decisions are BINDING. No exceptions without explicit user approval.**
**Authoritative source for current contracts**: RELAY_SERVICE_CONTRACT.md v2.0

**Last Updated**: 2026-03-07

