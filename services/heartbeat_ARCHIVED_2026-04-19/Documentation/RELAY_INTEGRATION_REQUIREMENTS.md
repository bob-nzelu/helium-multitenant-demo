# Relay Integration Requirements for HeartBeat

**Created by**: Relay team (during API rewrite Phase 3)
**Date**: 2026-02-16
**Purpose**: Document capabilities Relay needs from HeartBeat. Update this file as new requirements are discovered during Relay development.

---

## 1. API Key Management (Push Model)

Relay authenticates ingest requests via HMAC-SHA256 using API key + secret pairs. Currently, these are loaded from environment variables at startup (`RELAY_DEV_API_KEY` / `RELAY_DEV_API_SECRET`).

**HeartBeat must**:
- Store API key/secret pairs in `config.db` (one row per company)
- When keys are created, rotated, or revoked, push updates to Relay via:
  `POST /internal/refresh-cache` (already implemented in Relay)
- Include API keys in the refresh payload alongside Transforma modules

**Relay's existing endpoint**:
- `POST /internal/refresh-cache` at `src/api/routes/internal.py`
- Protected by `RELAY_INTERNAL_SERVICE_TOKEN` Bearer auth
- Currently refreshes Transforma modules only
- **TODO (Relay)**: Extend to also accept and reload API key/secret map

**Payload format HeartBeat should push**:
```json
{
  "api_keys": {
    "key-001": "secret-001",
    "key-002": "secret-002"
  },
  "modules": [...],
  "service_keys": {...}
}
```

**Rate limit tiers** (already in HEARTBEAT_API_CONTRACTS.md):
- Standard: 1,000 uploads/day
- Pro: 5,000 uploads/day
- Enterprise: 10,000 uploads/day

HeartBeat should push the company's tier limit alongside the key, so Relay can pass it to Redis as the `limit` parameter.

---

## 2. Concurrent Duplicate Prevention (Redis SETNX)

Relay's dedup has two levels:
- **Level 1**: In-memory session cache (per-request, catches within-batch dupes)
- **Level 2**: HeartBeat persistent check via `GET /api/dedup/check?hash={sha256}`

**Race condition**: Two concurrent requests with the same file hash can both pass Level 2 before either records the hash. Both requests would be accepted.

**HeartBeat must use Redis SETNX internally**:
```
SETNX heartbeat:dedup:{file_hash} "{queue_id}:{timestamp}"
EXPIRE heartbeat:dedup:{file_hash} 7776000   # 90 days
```

- `SETNX` returns 1 (key set = new file) or 0 (key exists = duplicate)
- Single atomic operation eliminates the race condition
- The existing `blob_deduplication` table in `blob.db` becomes a backup/queryable store
- Redis is the hot path; SQLite is the cold path for historical lookups

**Why HeartBeat owns this, not Relay**:
- Dedup is a persistent, cross-tenant, cross-instance check
- HeartBeat already has the `blob_deduplication` table
- Relay only has session-level dedup (in-memory, per-request, per-instance)

---

## 3. Orphan Blob Reconciliation

When Relay writes a blob (Step 4) but the subsequent Core enqueue (Step 5) fails after 5 retry attempts, the blob becomes "orphaned" — it exists in MinIO but has no `core_queue` entry.

**Relay's behavior**:
- Returns `queue_id = "orphan_{file_uuid}"` to the caller
- Logs a warning but does NOT fail the upload (blob is safely stored)
- Does NOT retry Core beyond the 5 attempts in `BaseClient.call_with_retries()`
- Trusts HeartBeat reconciliation to recover

**HeartBeat must** (see also `RECONCILIATION_KICKSTART.md`):
- Run hourly reconciliation scanning MinIO for blobs not in `blob_entries`
- Cross-reference with Core's `core_queue` status endpoint
- Auto-create missing `core_queue` entries for orphaned blobs
- Create notifications/alerts for anomalies
- Target: orphans recovered within 1 hour

---

## 4. Float Queue Polling (Timed Listener)

After Float submits files to Relay, Relay returns a `queue_id`. Float needs to check if Core has finished processing.

**Current approach** (being replaced): BulkService blocks for up to 5 minutes waiting for Core preview.

**New approach**: Float polls HeartBeat on a timer (every 5-10 seconds) using the `queue_id`.

**HeartBeat must implement**:
```
GET /api/v1/core_queue/{queue_id}/status
```

**Response**:
```json
{
  "queue_id": "queue_abc123",
  "status": "processing | preview_ready | finalized | error",
  "progress_pct": 65,
  "preview_data": {...},     // only when status = "preview_ready"
  "error_message": "...",    // only when status = "error"
  "created_at": "2026-02-16T10:00:00Z",
  "updated_at": "2026-02-16T10:00:05Z"
}
```

**Status values**:
- `processing` — Core is still working on the file
- `preview_ready` — Core finished, preview data available (Float shows edit screen)
- `finalized` — User accepted, invoice submitted to FIRS
- `error` — Processing failed (show error to user)

**Why HeartBeat, not Core directly**:
- HeartBeat is the monitoring layer, already tracks blob and queue state
- Single entry point for Float (Float only talks to Relay and HeartBeat)
- HeartBeat can enrich status with blob metadata + queue status combined
- Keeps Core focused on processing, not serving polling requests

---

## 5. Trace ID Convention (Platform-Wide)

All Helium services must propagate `X-Trace-ID` header for distributed request tracing.

**Convention**:
- Format: UUID v4 (e.g., `550e8400-e29b-41d4-a716-446655440000`)
- Generated by: First service to receive the request (usually Relay)
- Propagated via: `X-Trace-ID` header on all inter-service HTTP calls
- Stored in: All audit log entries, all error responses, all log lines

**Relay's implementation** (already done):
- `TraceIDMiddleware` in `src/api/middleware.py` generates/propagates trace IDs
- `BaseClient.get_trace_headers()` returns `{"X-Trace-ID": ..., "X-Request-ID": ...}`
- All log entries include `trace_id` in `extra` dict

**HeartBeat must**:
- Accept `X-Trace-ID` header on all incoming requests
- If absent, generate a new UUID v4
- Include `trace_id` in all audit log entries and database records
- Propagate `X-Trace-ID` to Core when making cross-service calls
- Return `X-Trace-ID` in all response headers

---

## Summary: Relay's Current Dependencies on HeartBeat

| Relay Step | HeartBeat Call | Status |
|---|---|---|
| Step 2: Rate limit | `check_daily_limit()` | **Replaced by Redis** — HeartBeat fallback only |
| Step 3: Dedup | `check_duplicate()` | Stub — needs real impl with SETNX |
| Step 4: Blob write | `write_blob()` | Stub — needs MinIO integration |
| Step 5: Core enqueue | (calls Core directly) | N/A |
| Step 6: Register blob | `register_blob()` | Stub — needs real impl |
| Step 7: Audit log | `audit_log()` | Stub — needs real impl |
| Startup | `get_transforma_config()` | Stub — needs module+key store |
| Key refresh | `POST /internal/refresh-cache` | Relay endpoint exists, HeartBeat needs to call it |
| Queue polling | `GET /core_queue/{id}/status` | Not implemented — Float needs this |
