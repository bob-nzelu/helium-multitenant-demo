# Relay Service Contract

**Version**: 2.0
**Date**: 2026-02-19
**Status**: AUTHORITATIVE — supersedes RELAY_BULK_SPEC.md, RELAY_ARCHITECTURE.md for all integration concerns
**Audience**: Float SDK, Core, HeartBeat, and any service that communicates with Relay

---

## 1. Service Identity

| Property | Value |
|----------|-------|
| Service name | `relay-api` |
| Default port | 8082 |
| Instance ID format | `relay-api-{N}` (e.g., `relay-api-1`) |
| App version | `2.0.0` |
| Framework | FastAPI + uvicorn (async single-threaded) |

Relay is the **single ingestion gateway** for all data entering the Helium ecosystem. Every file — whether from Float desktop (bulk), an external API caller, or a future poller source — enters through Relay's `POST /api/ingest` endpoint.

---

## 2. Endpoints

### 2.1 `POST /api/ingest` — File Ingestion (Authenticated)

The ONE business endpoint. Accepts file uploads from both Float (bulk) and external API callers.

**Authentication**: HMAC-SHA256 (see Section 4)

**Request**: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | `UploadFile[]` | Yes | 1–3 invoice files. Allowed: `.pdf .xml .json .csv .xlsx` |
| `call_type` | `string` | No | `"bulk"` (default) or `"external"` |
| `invoice_data_json` | `string` | No | JSON string with invoice metadata (external flow only) |

**Response** (200 OK):

```json
{
  "status": "processed | queued | error",
  "data_uuid": "uuid-v4",
  "queue_id": "queue_uuid-v4",
  "filenames": ["invoice1.pdf", "invoice2.xlsx"],
  "file_count": 2,
  "file_hash": "sha256-hex",
  "trace_id": "trc_uuid-v4",

  "preview_data": { ... },   // Bulk flow only (when status=processed)

  "irn": "IRN-string",       // External flow only (when status=processed)
  "qr_code": "base64-string" // External flow only (when status=processed)
}
```

**Status values** (3 possible, formally defined):

| Status | Meaning | When | Caller action |
|--------|---------|------|---------------|
| `processed` | Core returned a response within the timeout window. | Bulk: Core returned preview data. External: Core accepted + IRN/QR generated locally. | Bulk: display preview. External: use IRN/QR. |
| `queued` | Data is safely stored but Core did not return a timely response. | Core timed out (5 min), Core unreachable, or Core returned an error. Blob IS committed in HeartBeat. | Poll Core directly for status (Core owns queue status). File is NOT lost. |
| `error` | A failure occurred before or during processing. | Validation failed, rate limit exceeded, dedup detected, internal error, or Core returned an explicit error. | Inspect `error_code` and `message`. Fix and retry, or report to user. |

> **Note**: There is no "accepted" status. Both flows always attempt to contact Core. The difference is whether Relay **waits** for Core's response (bulk, up to 5 min) or **fires-and-forgets** (external, returns immediately with locally-generated IRN/QR). In either case, if Core responds, the status is `processed`. If Core does not respond, the status is `queued`.

**`call_type` ↔ Mode mapping** (Float UI "Mode" column in Queue tab):

The `call_type` form field on `/api/ingest` identifies the ingestion method. Float stores this as `queue_mode` in sync.db and displays it in the Queue tab's "Mode" column. HeartBeat stores it as `source_type` on `blob_entries`.

| Relay `call_type` | Float `queue_mode` | Display (Mode column) | Phase |
|---|---|---|---|
| `bulk` | `bulk` | Bulk | 1 (implemented) |
| `external` | `api` | API | 1 (implemented) |
| *(future)* `polling` | `polling` | Polling | 2+ |
| *(future)* `watcher` | `watcher` | Watcher | 2+ |
| *(future)* `dbc` | `dbc` | DBC | 2+ |
| *(future)* `email` | `email` | Email | 2+ |

> **TODO (Phase 2)**: When new relay types are added, extend `call_type` validation in `ingest.py` to accept the new values, and add corresponding entries to HeartBeat's `relay_services` table. The Float SDK `UploadMode` enum and `UploadManager._VALID_MODES` already include all Phase 2 types.

**Error responses** follow the standard shape (see Section 6).

---

### 2.2 `GET /health` — Health Check (Public, No Auth)

**Response** (always 200 OK):

```json
{
  "status": "healthy | degraded",
  "instance_id": "relay-api-1",
  "relay_type": "bulk",
  "version": "2.0.0",
  "services": {
    "heartbeat": "healthy | unavailable",
    "module_cache": "loaded | not_loaded",
    "redis": "connected | disconnected"
  },
  "timestamp": "2026-02-19T10:00:00Z",
  "message": null
}
```

**Rules**:
- Never returns an error HTTP status. Always 200.
- `status` = `"degraded"` when HeartBeat is unavailable or module cache is not loaded.
- Redis disconnected is NOT degraded (graceful degradation by design).
- HeartBeat calls this endpoint every 30 seconds for service monitoring.

---

### 2.3 `GET /metrics` — Prometheus Metrics (Public, No Auth)

**Response**: `text/plain; version=0.0.4; charset=utf-8`

Phase 1 stub returns service info gauges. Phase 2 will add real request counters and histograms via `prometheus_client`.

```
# HELP helium_relay_info Relay service information
# TYPE helium_relay_info gauge
helium_relay_info{instance_id="relay-api-1",version="2.0.0"} 1

# HELP helium_relay_up Relay service health
# TYPE helium_relay_up gauge
helium_relay_up 1

# HELP helium_relay_module_cache_loaded Module cache status
# TYPE helium_relay_module_cache_loaded gauge
helium_relay_module_cache_loaded 1

# HELP helium_relay_redis_connected Redis status
# TYPE helium_relay_redis_connected gauge
helium_relay_redis_connected 1
```

---

### 2.4 `POST /internal/refresh-cache` — Module Cache Refresh (Internal Auth)

**Authentication**: Bearer token (`Authorization: Bearer {internal_service_token}`)

**Caller**: HeartBeat pushes this when `config.db` is updated with new Transforma modules or FIRS service keys.

**Response**:

```json
{
  "status": "ok",
  "modules_updated": ["irn_generator", "qr_generator"],
  "keys_updated": true
}
```

This endpoint is NOT exposed via tunnel or public network. It is internal-only, called by HeartBeat on the same network.

---

### 2.5 Endpoints Relay Does NOT Own

| Endpoint | Owner | Notes |
|----------|-------|-------|
| `GET /api/queue/status/{queue_id}` | **Core** | Queue status polling is Core's responsibility. Relay does not track queue progress. |
| `POST /api/finalize` | **Core** | Float SDK calls Core directly to finalize previewed invoices. Relay is not in this path. |

---

## 3. Ingestion Pipeline (7 Steps)

Every call to `POST /api/ingest` runs this pipeline. Steps 1-3 can fail cleanly. Step 4 is the **commit point**. Steps 5-7 are best-effort after commit.

```
Step 1: validate_files()         — file count, extension, size checks
Step 2: check_daily_limit()      — per-company quota (Redis primary, HeartBeat fallback)
Step 3: dedup_check()            — SHA256 hash: session cache → HeartBeat
Step 4: write_blob()             ★ COMMIT POINT — HeartBeat → MinIO
Step 5: enqueue_core()           — Core processing queue (best-effort, orphan recovery exists)
Step 6: register_blob()          — HeartBeat metadata (fire-and-forget)
Step 7: audit_log()              — Immutable audit trail (fire-and-forget)
```

### Step 4: Commit Point

Once the blob is written to HeartBeat/MinIO, it **cannot be rolled back**. All subsequent failures use "best-effort + eventual consistency" recovery:

- Step 5 fails → orphaned blob; HeartBeat reconciliation recovers within 1 hour
- Step 6 fails → HeartBeat reconciliation auto-creates blob_entries
- Step 7 fails → non-blocking, doesn't affect the upload

### After Pipeline: Flow-Specific Processing

**Bulk flow** (`call_type=bulk`):
1. Pipeline completes → IngestResult
2. Call `CoreClient.process_preview(queue_id, timeout=300s)`
3. If Core responds → `status="processed"`, return `preview_data`
4. If Core times out or errors → `status="queued"` (graceful degradation)

**External flow** (`call_type=external`):
1. Pipeline completes → IngestResult
2. Fire-and-forget `CoreClient.process_immediate(queue_id)` — wrapped in try/except, always continues
3. Generate IRN from cached Transforma module (does NOT require Core)
4. Generate QR code from cached Transforma module (does NOT require Core)
5. Return `status="processed"` with `irn` and `qr_code`
6. If modules not loaded → return 503 `MODULE_NOT_LOADED`

> **Key design**: External flow generates IRN/QR **locally** from cached Transforma modules. Core failure does NOT prevent IRN/QR generation. This is deliberate — external API callers need an immediate response.

---

## 4. Authentication

### 4.1 Float SDK → Relay: HMAC-SHA256

All requests to `POST /api/ingest` must include these headers:

| Header | Description |
|--------|-------------|
| `X-API-Key` | Client API key (format: `{2-letter}_{env}_{32-hex}`) |
| `X-Timestamp` | ISO 8601 UTC timestamp (e.g., `2026-02-19T10:00:00Z`) |
| `X-Signature` | HMAC-SHA256 signature |

**Signature computation** (client side):

```
body_hash  = SHA256(request_body_bytes)
message    = "{api_key}:{timestamp}:{body_hash}"
signature  = HMAC-SHA256(api_secret, message)
```

**Verification** (Relay side):
1. Check timestamp is within 5-minute window
2. Look up API secret for the given API key
3. Recompute signature from raw request body
4. Constant-time comparison

**5-minute window**: Prevents replay attacks while tolerating typical clock skew (2-3 seconds).

> **CANONICAL**: This HMAC scheme is defined by HeartBeat Service Contract v3.0 (Part 2, Section 2.2). Float SDK, Relay, and all other consumers MUST use this exact scheme.

### 4.2 Relay → HeartBeat: Bearer Token

```
Authorization: Bearer {api_key}:{api_secret}
```

HeartBeat bcrypt-verifies the secret on each request.

### 4.3 HeartBeat → Relay (Internal): Bearer Token

```
Authorization: Bearer {internal_service_token}
```

Used only for `POST /internal/refresh-cache`. Token configured via `RELAY_INTERNAL_SERVICE_TOKEN` env var.

### 4.4 User Authentication

Relay does NOT authenticate end users. Core/Float owns user auth.

Relay passes through `X-User-ID` header (when present) to audit logs, but performs no validation. This is stubbed for future implementation by Core.

---

## 5. Concurrency, Rate Limiting, and Rapid-Fire Protection

Relay handles concurrent and rapid-fire requests through **four layers of protection**:

### Layer 1: Async Concurrency (FastAPI/uvicorn)

Relay is async single-threaded using `asyncio`. Every request handler is `async def`. Multiple concurrent requests are handled via the event loop — no threads, no blocking.

- Each `POST /api/ingest` request runs the 7-step pipeline concurrently with other requests
- I/O-bound operations (HeartBeat calls, Core calls, Redis calls) yield to the event loop
- CPU-bound operations (SHA256, HMAC verification) are fast enough to not block

**Horizontal scaling**: Multiple uvicorn workers or multiple Relay instances behind nginx. Each instance handles concurrent requests independently.

### Layer 2: Redis Rate Limiting (Per-Company Daily Quota)

**Primary mechanism** for preventing rapid-fire abuse, especially from API callers.

```
Key format:  {prefix}:daily:{company_id}:{YYYY-MM-DD}
Operation:   Atomic INCRBY + EXPIRE via Redis pipeline
TTL:         86400 seconds (24 hours, auto-cleanup)
Default limit: 500 files per company per day
```

**How it works**:
1. On each request, Redis atomically increments the company's daily counter by `file_count`
2. If counter exceeds daily limit → `429 RATE_LIMIT_EXCEEDED`
3. First request of the day sets TTL (auto-expires at midnight)

**Rapid-fire scenario** (e.g., API caller sends 100 requests in 1 second):
- All 100 requests hit Redis atomically
- Counter increments correctly (no race conditions — Redis is single-threaded)
- Once counter exceeds 500, all subsequent requests get 429
- No data loss: requests that passed the limit are processed normally

**Graceful degradation** (3-tier fallback):

| Tier | Source | Behavior |
|------|--------|----------|
| Primary | Redis | Atomic INCR, sub-millisecond |
| Fallback | HeartBeat API | HTTP call to `/api/daily_usage/check` |
| Degraded | Allow all | If both Redis and HeartBeat are down, allow the request |

Redis failure marks it unavailable for subsequent calls. No retry — rate limiting is best-effort.

### Layer 3: Deduplication (SHA256 Content Hash)

Two-level dedup prevents the same file from being processed twice:

| Level | Scope | Speed | When |
|-------|-------|-------|------|
| Session cache | Current request | O(1) memory lookup | Catches duplicates within same multi-file upload |
| HeartBeat check | All historical uploads | HTTP round-trip | Catches duplicates across sessions |

**Rapid-fire scenario**: If the same file is uploaded 50 times in quick succession:
- First request passes dedup, blob is committed
- Requests 2-50 hit HeartBeat dedup check and get `409 DUPLICATE_FILE`
- Only one blob is stored; 49 requests are rejected cleanly

### Layer 4: Validation (Pre-Pipeline Guards)

Before any expensive processing:
1. **File count**: Max 3 files per request (configurable)
2. **File size**: Max 10MB per file, 30MB total (configurable)
3. **File extension**: Whitelist only (`.pdf .xml .json .csv .xlsx`)
4. **HMAC timestamp**: 5-minute window rejects stale/replayed requests

These are **fast failures** — they reject bad requests before touching Redis, HeartBeat, or Core.

### Rapid-Fire Summary Table

| Attack vector | Protection | Response |
|--------------|------------|----------|
| Same file uploaded N times | Dedup (Layer 3) | 409 after first |
| N different files in 1 second | Rate limit (Layer 2) | 429 after daily quota |
| Replay of old request | HMAC timestamp (Layer 4) | 401 after 5 minutes |
| Oversized/invalid files | Validation (Layer 4) | 400 immediate |
| 100 concurrent requests | Async (Layer 1) | All processed concurrently |

### Configuration

| Setting | Env var | Default | Description |
|---------|---------|---------|-------------|
| Daily limit | `RELAY_RATE_LIMIT_DAILY` | 500 | Files per company per day |
| Max files per request | `RELAY_MAX_FILES` | 3 | Files per HTTP request |
| Max file size | `RELAY_MAX_FILE_SIZE_MB` | 10 | MB per file |
| Max total size | `RELAY_MAX_TOTAL_SIZE_MB` | 30 | MB per request |
| Redis URL | `RELAY_REDIS_URL` | `""` (disabled) | Redis connection string |

---

## 6. Error Response Format

All errors follow a single standardized shape:

```json
{
  "status": "error",
  "error_code": "MACHINE_READABLE_CODE",
  "message": "Human-readable error message",
  "details": [
    {
      "field": "filename",
      "error": "Specific error description"
    }
  ]
}
```

### Error Codes

| HTTP | Error Code | Meaning |
|------|-----------|---------|
| 400 | `VALIDATION_FAILED` | File extension, size, or format invalid |
| 400 | `NO_FILES_PROVIDED` | Empty upload |
| 400 | `TOO_MANY_FILES` | Exceeds max_files_per_request |
| 400 | `MALWARE_DETECTED` | ClamAV scan failed |
| 401 | `AUTHENTICATION_FAILED` | HMAC signature mismatch or timestamp expired |
| 401 | `INVALID_API_KEY` | API key not found |
| 409 | `DUPLICATE_FILE` | Content-based duplicate detected |
| 429 | `RATE_LIMIT_EXCEEDED` | Daily company quota exhausted |
| 500 | `INTERNAL_ERROR` | Unexpected server error |
| 503 | `MODULE_NOT_LOADED` | Transforma modules not cached (external flow only) |

---

## 7. Graceful Degradation

Relay never fails an upload if the data can be safely stored. The principle: **never lose user data**.

| Failure | Behavior | User sees |
|---------|----------|-----------|
| Core unavailable | Blob written, Core enqueue fails → orphan recovery | `status="queued"` |
| Core timeout (>5 min) | Blob written, Core still processing | `status="queued"` |
| Redis down | Skip rate limiting, allow request | Normal response |
| HeartBeat down (rate limit) | Skip rate limit check, allow request | Normal response |
| HeartBeat down (blob write) | **This IS a failure** — blob cannot be committed | `500 INTERNAL_ERROR` |
| Module cache cold | External flow cannot generate IRN/QR | `503 MODULE_NOT_LOADED` |
| Audit log fails | Non-blocking, doesn't affect upload | Normal response |
| Blob registration fails | Non-blocking, HeartBeat reconciliation recovers | Normal response |

---

## 8. Downstream Service Dependencies

### 8.1 HeartBeat (Required)

| Operation | HeartBeat endpoint | When | Failure mode |
|-----------|-------------------|------|-------------|
| Write blob | `POST /api/blobs/write` | Step 4 (commit point) | **Fatal** — upload fails |
| Check dedup | `POST /api/duplicate/check` | Step 3 | Fatal — upload fails |
| Record dedup | `POST /api/duplicate/record` | After step 4 | Non-blocking |
| Check daily limit | `GET /api/daily_usage/check` | Step 2 (fallback) | Allow (degraded) |
| Register blob | `POST /api/blobs/register` | Step 6 | Non-blocking |
| Audit log | `POST /api/audit/log` | Step 7 | Non-blocking |
| Health check | `GET /health` (HeartBeat's) | `/health` endpoint | Report degraded |

**Auth**: `Authorization: Bearer {api_key}:{api_secret}`

### 8.2 Core (Best-Effort)

| Operation | Core endpoint | When | Failure mode |
|-----------|--------------|------|-------------|
| Enqueue | `POST /api/enqueue` | Step 5 | Best-effort, orphan recovery |
| Process preview | `POST /api/process/preview` | Bulk flow, after pipeline | Timeout → "queued" |
| Process immediate | `POST /api/process/immediate` | External flow, fire-and-forget | Logged, continues |

**Auth**: Bearer token

**Timeouts**:
- Enqueue: 30 seconds
- Process preview: 300 seconds (5 minutes)
- Process immediate: 30 seconds

### 8.3 Redis (Optional)

| Operation | When | Failure mode |
|-----------|------|-------------|
| Rate limit check | Step 2 (primary) | Fall back to HeartBeat |

**Connection**: `redis://{host}:{port}/{db}` via `RELAY_REDIS_URL`

---

## 9. Encryption (E2EE)

Relay supports end-to-end payload encryption using NaCl X25519 + XSalsa20-Poly1305 (Box construction).

### Wire Format

```
[1 byte version][32 bytes ephemeral_public_key][encrypted_payload]
```

### Headers

| Header | Value | Meaning |
|--------|-------|---------|
| `X-Encrypted` | `true` | Request body is encrypted |
| `X-Encrypted` | `false` (or absent) | Request body is plaintext |

### Configuration

| Setting | Env var | Default |
|---------|---------|---------|
| Require encryption | `RELAY_REQUIRE_ENCRYPTION` | `true` |
| Private key file | `RELAY_PRIVATE_KEY_PATH` | `""` (ephemeral for dev) |

When `require_encryption=true` and `X-Encrypted` is not `true`, Relay returns `403 ENCRYPTION_REQUIRED`.

> **Contract owner**: HeartBeat defines the encryption protocol and key distribution. Relay implements it. See HeartBeat Service Contract for key exchange details.

---

## 10. Transforma Module Cache

Relay caches Python modules from HeartBeat's `config.db` that generate IRN and QR codes. This allows the external flow to produce IRN/QR without Core.

### Lifecycle

```
Startup:   load_all()              — fetch all modules from HeartBeat
Every 12h: refresh()               — checksum-based, reload only if changed
On-demand: POST /internal/refresh-cache  — HeartBeat pushes when config.db changes
Shutdown:  cleanup()               — release module references
```

### Cached Items

| Item | Source | Purpose |
|------|--------|---------|
| IRN generator module | HeartBeat config.db | Generate Invoice Reference Numbers |
| QR generator module | HeartBeat config.db | Generate QR codes from IRN |
| FIRS public key | HeartBeat config.db | FIRS signature verification |
| CSID | HeartBeat config.db | Compliance System ID |
| Certificate | HeartBeat config.db | Service certificate |

### Module Cache Status

- **Loaded**: External flow operational, IRN/QR generation works
- **Not loaded**: External flow returns `503 MODULE_NOT_LOADED`; bulk flow is unaffected (bulk doesn't generate IRN/QR)

---

## 11. Retry & Timeout Strategy

### Retry (Exponential Backoff)

Used for transient failures when calling HeartBeat and Core:

```
Attempt 1: immediate
Attempt 2: wait 1s
Attempt 3: wait 2s
Attempt 4: wait 4s
Attempt 5: wait 8s
Total max wait: ~15 seconds
```

**Transient** (retry): Connection timeout, 503, connection reset
**Permanent** (fail fast): 401, 404, 400

### Timeouts

| Operation | Timeout | Rationale |
|-----------|---------|-----------|
| General HTTP request | 30s | Standard for API calls |
| Core preview (bulk) | 300s (5 min) | Large batches take time |
| Redis operations | 5s | Sub-millisecond normally |
| HMAC timestamp window | 300s (5 min) | Replay prevention |
| Graceful shutdown | 30s | Wait for in-flight requests |

---

## 12. Audit & Observability

### Fire-and-Forget Audit Logging

Audit logging (Step 7) is **non-blocking**. It NEVER delays the response to the caller. If audit fails, it is logged locally and the upload succeeds.

Audit events are INSERT-only (immutable) in HeartBeat's `audit.db`, with checksum chain integrity.

### Events Logged

| Event | When |
|-------|------|
| `file.ingested` | After successful pipeline completion |
| `authentication.failed` | HMAC verification failure |
| `rate_limit.exceeded` | Daily quota exhausted |
| `duplicate.detected` | Dedup check found existing file |
| `core.unavailable` | Core enqueue or process failed |
| `cache.refreshed` | Module cache refresh completed |

### Prometheus Metrics

Exported at `GET /metrics`. Phase 1 gauges, Phase 2 adds counters/histograms:

- `helium_relay_up` — service health
- `helium_relay_info` — instance_id, version
- `helium_relay_module_cache_loaded` — cache status
- `helium_relay_redis_connected` — Redis status
- Phase 2: `helium_relay_files_ingested_total{status}`, `helium_relay_processing_duration_seconds`, `helium_relay_errors_total{error_code}`

### Structured Logging

All logs are JSON to stdout (container-native):

```json
{
  "timestamp": "2026-02-19T10:00:00Z",
  "level": "INFO",
  "service": "relay-api-1",
  "trace_id": "trc_550e8400-...",
  "event": "file.ingested",
  "data_uuid": "550e8400-...",
  "queue_id": "queue_123"
}
```

### Trace ID Propagation

Every request gets a `trace_id` (generated by TraceIDMiddleware). This ID is:
- Included in all log entries
- Passed to HeartBeat and Core in downstream calls
- Returned in the response as `trace_id`
- Available via `X-Trace-ID` response header

---

## 13. Multiple Files Per Request

When a caller uploads 2-3 files in a single request:

1. All files are sent as multipart form data
2. Relay reads all files into `(filename, bytes)` tuples
3. Files are sent to HeartBeat as a single multipart blob write — one `data_uuid` maps to all files
4. The response includes `filenames: [...]` (array of all filenames)
5. Deduplication operates on the combined content hash
6. Core receives all files under one `queue_id`

> **No ZIP**: Files are NOT zipped. They are forwarded as multipart to HeartBeat. This decision supersedes RELAY_DECISIONS.md Decision 3A.

---

## 14. Configuration Reference

All settings load from `RELAY_*` environment variables via `RelayConfig.from_env()`.

| Category | Setting | Env var | Default |
|----------|---------|---------|---------|
| Server | Host | `RELAY_HOST` | `0.0.0.0` |
| Server | Port | `RELAY_PORT` | `8082` |
| Server | Instance ID | `RELAY_INSTANCE_ID` | `relay-api-1` |
| Upstream | Core URL | `RELAY_CORE_API_URL` | `http://localhost:8080` |
| Upstream | HeartBeat URL | `RELAY_HEARTBEAT_API_URL` | `http://localhost:9000` |
| Encryption | Require encryption | `RELAY_REQUIRE_ENCRYPTION` | `true` |
| Encryption | Private key path | `RELAY_PRIVATE_KEY_PATH` | `""` (ephemeral) |
| Limits | Max files per request | `RELAY_MAX_FILES` | `3` |
| Limits | Max file size (MB) | `RELAY_MAX_FILE_SIZE_MB` | `10` |
| Limits | Max total size (MB) | `RELAY_MAX_TOTAL_SIZE_MB` | `30` |
| Limits | Allowed extensions | `RELAY_ALLOWED_EXTENSIONS` | `.pdf,.xml,.json,.csv,.xlsx` |
| Limits | Daily rate limit | `RELAY_RATE_LIMIT_DAILY` | `500` |
| Timeouts | Preview timeout | `RELAY_PREVIEW_TIMEOUT_S` | `300` |
| Timeouts | Request timeout | `RELAY_REQUEST_TIMEOUT_S` | `30` |
| Retry | Max attempts | `RELAY_MAX_RETRY_ATTEMPTS` | `5` |
| Retry | Initial delay | `RELAY_RETRY_INITIAL_DELAY_S` | `1.0` |
| Redis | URL | `RELAY_REDIS_URL` | `""` (disabled) |
| Redis | Key prefix | `RELAY_REDIS_PREFIX` | `relay` |
| Cache | Refresh interval | `RELAY_MODULE_CACHE_REFRESH_INTERVAL_S` | `43200` (12h) |
| Cache | Internal token | `RELAY_INTERNAL_SERVICE_TOKEN` | `""` |
| Workers | Uvicorn workers | `RELAY_WORKERS` | `1` |

---

## 15. Decision Log (Amendments)

This section tracks decisions that amend or supersede earlier documents.

| ID | Decision | Supersedes | Date |
|----|----------|------------|------|
| D1 | `call_type` replaces `caller_type` as the form field name | RELAY_BULK_SPEC.md | 2026-02-19 |
| D2 | `data_uuid` replaces `file_uuid` in all responses | RELAY_BULK_SPEC.md, RELAY_ARCHITECTURE.md | 2026-02-19 |
| D3 | `filenames: List[str]` replaces `filename: str` in responses | RELAY_BULK_SPEC.md | 2026-02-19 |
| D4 | No ZIP — multipart forwarding instead | RELAY_DECISIONS.md Decision 3A | 2026-02-19 |
| D5 | Queue status polling is Core's endpoint, not Relay's | RELAY_DECISIONS.md Decision 1B, RELAY_BULK_SPEC.md | 2026-02-19 |
| D6 | Finalize is Core's endpoint, not Relay's | RELAY_BULK_SPEC.md, RELAY_ARCHITECTURE.md | 2026-02-19 |
| D7 | `"accepted"` status removed — replaced by `"processed"` and `"queued"` | RELAY_ARCHITECTURE.md | 2026-02-19 |
| D8 | `trace_id` added to IngestResponse | New | 2026-02-19 |
| D9 | `"error"` is a valid status value in IngestResponse | New | 2026-02-19 |

---

## 16. Phase Roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| Phase 1A | COMPLETE | Infrastructure: config, errors, auth, crypto, clients, middleware |
| Phase 1B | COMPLETE | Bulk upload: ingestion pipeline, validation, file processing |
| Phase 1C | COMPLETE | Testing: 427 tests, 99% coverage. Health, metrics endpoints added. |
| Phase 2 | PLANNED | Wire real HeartBeat/Core HTTP clients (replace stubs). Real Redis. SDK alignment. |
| Phase 3 | PLANNED | Poller relay types (NAS, SFTP, HTTP). Prometheus real counters. |

---

## Appendix A: Flow Diagrams

### Bulk Flow (Float Desktop)

```
Float SDK                     Relay                      HeartBeat         Core
    │                           │                            │                │
    │── POST /api/ingest ──────>│                            │                │
    │   (call_type=bulk)        │                            │                │
    │                           │── validate files ──────────│                │
    │                           │── check rate limit ───────>│ (Redis/HB)     │
    │                           │── dedup check ────────────>│                │
    │                           │── write blob ─────────────>│ ★ COMMIT       │
    │                           │── enqueue ─────────────────│───────────────>│
    │                           │── register blob ──────────>│ (fire&forget)  │
    │                           │── audit log ──────────────>│ (fire&forget)  │
    │                           │                            │                │
    │                           │── process_preview ─────────│───────────────>│
    │                           │   (wait up to 5 min)       │                │
    │                           │<───────────────────────────│────────────────│
    │                           │                            │                │
    │<── 200 OK ────────────────│                            │                │
    │   status=processed        │                            │                │
    │   preview_data={...}      │                            │                │
```

### External Flow (API Caller)

```
API Caller                    Relay                      HeartBeat         Core
    │                           │                            │                │
    │── POST /api/ingest ──────>│                            │                │
    │   (call_type=external)    │                            │                │
    │                           │── [pipeline steps 1-7] ───>│──────────────>│
    │                           │                            │                │
    │                           │── fire-and-forget Core ────│───────────────>│
    │                           │   (try/except, always      │                │
    │                           │    continues)              │                │
    │                           │                            │                │
    │                           │── generate IRN (local) ────│                │
    │                           │── generate QR  (local) ────│                │
    │                           │                            │                │
    │<── 200 OK ────────────────│                            │                │
    │   status=processed        │                            │                │
    │   irn=..., qr_code=...   │                            │                │
```

---

**End of Document**
