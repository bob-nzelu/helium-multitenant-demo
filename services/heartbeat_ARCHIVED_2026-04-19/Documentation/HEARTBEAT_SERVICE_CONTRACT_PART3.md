# HeartBeat Service Contract — Part 3: Observability, Health, Updates & Lifecycle

**Version:** 3.1
**Date:** 2026-02-19
**Status:** AUTHORITATIVE
**Audience:** All service teams, DevOps, Security
**Changelog:** v3.1 — Updated known gaps (§10) to reflect actual implementation status, aligned audit event types with Relay Service Contract, clarified queue ownership (§8), added user_id to audit events

---

## 1. Audit Logging

### 1.1 How Services Log Audit Events

Every service logs significant events to HeartBeat's immutable audit trail:

```
POST /api/audit/log
```

```json
{
    "service": "relay",
    "event_type": "file.ingested",
    "details": {
        "data_uuid": "abc-123",
        "filenames": ["WN42752.pdf"],
        "file_size_bytes": 245760,
        "source": "bulk",
        "company_id": "pikwik-001",
        "user_id": "usr-456",
        "trace_id": "trc-9f8e7d6c"
    },
    "trace_id": "trc-9f8e7d6c"
}
```

**This is fire-and-forget.** Callers do NOT wait for the response. HeartBeat writes to `audit_events` (INSERT-only, immutable) and returns 202 Accepted.

**User identity**: When `X-User-ID` is present in the original request, the calling service includes `user_id` in the audit event details. This allows audit queries like "who uploaded this file?" HeartBeat does not validate the user_id — that's Core/Float's responsibility (see Part 1 §3.8).

### 1.2 Standard Event Types

| Service | Event Type | When |
|---|---|---|
| Relay | `file.ingested` | File received and committed to blob storage |
| Relay | `authentication.failed` | HMAC verification failure |
| Relay | `rate_limit.exceeded` | Daily upload limit hit |
| Relay | `duplicate.detected` | Dedup check caught duplicate |
| Relay | `core.unavailable` | Core enqueue or process failed |
| Relay | `cache.refreshed` | Module cache refresh completed |
| Core | `blob.processing_started` | Core begins extraction |
| Core | `blob.processing_completed` | Core finishes successfully |
| Core | `blob.processing_failed` | Core processing error |
| Core | `invoice.validated` | Invoice passed validation |
| Core | `invoice.rejected` | Invoice failed validation |
| Edge | `submission.sent_to_firs` | FIRS submission attempted |
| Edge | `submission.accepted` | FIRS accepted |
| Edge | `submission.rejected` | FIRS rejected |
| Float SDK | `user.file_selected` | User picked files for upload |
| Float SDK | `user.preview_accepted` | User finalized preview |
| HeartBeat | `credential.created` | New API key generated |
| HeartBeat | `credential.rotated` | Key rotation |
| HeartBeat | `credential.revoked` | Key revoked |
| HeartBeat | `service.registered` | New service instance joined |
| HeartBeat | `satellite.registered` | Satellite connected to Primary |

### 1.3 Audit Immutability (Q4 — Customer Demo Point)

HeartBeat enforces immutability via:

**1. SQLite Triggers** — Prevent UPDATE/DELETE on audit tables:
```sql
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is immutable'); END;

CREATE TRIGGER audit_events_no_delete
    BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is immutable'); END;
```

Applied to: `audit_events`, `key_rotation_log`, `blob_cleanup_history`.

**2. Checksum Chain** — Each audit row includes SHA-256 hash of previous row:
```
row_checksum = SHA256(event_type + timestamp_unix + details_json + prev_checksum)
```

This creates a tamper-evident chain. If any row is modified externally (bypassing triggers), the chain breaks.

**3. Verification Endpoint:**
```
GET /api/audit/verify?from_id=1&to_id=500
```
Response:
```json
{
    "verified": true,
    "chain_length": 500,
    "tampered_rows": [],
    "first_checked": 1,
    "last_checked": 500
}
```

---

## 2. Metrics Reporting

### 2.1 How Services Report Metrics

Every service pushes operational metrics to HeartBeat:

```
POST /api/metrics/report
```

```json
{
    "service": "relay",
    "metric_type": "upload",
    "values": {
        "files_processed": 5,
        "total_bytes": 1245760,
        "processing_time_ms": 2300
    },
    "timestamp": "2026-02-18T10:30:00Z"
}
```

**This is fire-and-forget.** Callers do NOT wait. HeartBeat stores in `metrics_events`.

### 2.2 Prometheus Export

HeartBeat exposes all metrics in Prometheus format:

```
GET /metrics
```

**No authentication required** (Prometheus scraper needs unauthenticated access).

Returns `text/plain` in Prometheus exposition format:
```
# HELP heartbeat_blobs_uploaded_total Total blobs uploaded
# TYPE heartbeat_blobs_uploaded_total counter
heartbeat_blobs_uploaded_total{source="relay",source_type="bulk"} 42

# HELP heartbeat_api_request_duration_seconds API request latency
# TYPE heartbeat_api_request_duration_seconds histogram
heartbeat_api_request_duration_seconds_bucket{method="POST",path="/api/blobs/write",le="0.1"} 38
```

**Metrics exported:**

| Metric | Type | Labels |
|---|---|---|
| `heartbeat_blobs_uploaded_total` | Counter | source, source_type, content_type |
| `heartbeat_blobs_registered_total` | Counter | source |
| `heartbeat_blobs_status_changed_total` | Counter | from_status, to_status |
| `heartbeat_dedup_checks_total` | Counter | result (duplicate/unique) |
| `heartbeat_daily_limit_checks_total` | Counter | result (allowed/blocked) |
| `heartbeat_audit_events_total` | Counter | service, event_type |
| `heartbeat_credentials_operations_total` | Counter | action |
| `heartbeat_api_errors_total` | Counter | error_code, endpoint |
| `heartbeat_blob_write_duration_seconds` | Histogram | content_type |
| `heartbeat_api_request_duration_seconds` | Histogram | method, path, status_code |
| `heartbeat_blobs_by_status` | Gauge | status |
| `heartbeat_active_services` | Gauge | service_name |
| `heartbeat_storage_health` | Gauge | — (1=healthy, 0=unhealthy) |
| `heartbeat_database_health` | Gauge | — |

**Note on Relay's /metrics**: Relay also exposes its own `GET /metrics` endpoint with Relay-specific gauges (`helium_relay_up`, `helium_relay_module_cache_loaded`, etc.). Both endpoints are independent — Prometheus scrapes each service separately.

---

## 3. Health Checks

### 3.1 How Services Expose Health

Every Helium service **must** expose:

```
GET /health
```

Response:
```json
{
    "status": "healthy",
    "service": "relay",
    "instance_id": "relay-bulk-001",
    "version": "2.0.0",
    "uptime_seconds": 3600,
    "checks": {
        "database": "healthy",
        "storage": "healthy",
        "dependencies": {
            "heartbeat": "reachable",
            "core": "reachable"
        }
    }
}
```

**No authentication.** Health endpoints are always public (for load balancers, Prometheus, orchestrators).

**Note on Relay's /health**: Relay's health response includes additional fields specific to its role: `relay_type`, `services` (heartbeat/module_cache/redis status). See Relay Service Contract §2.2 for details.

### 3.2 Periodic Health Reporting to HeartBeat

Services should call HeartBeat every 30 seconds:

```
POST /api/registry/health/{instance_id}
```

```json
{
    "status": "healthy",
    "uptime_seconds": 3600,
    "details": {
        "db_connected": true,
        "queue_depth": 42,
        "memory_mb": 128
    }
}
```

HeartBeat stamps `last_health_at` on the `service_instances` row.

### 3.3 HeartBeat Active Health Polling

If a service **stops reporting** health (no `POST /api/registry/health` for >60 seconds):

1. HeartBeat calls the service's `/health` endpoint directly (URL from registry)
2. If reachable → mark as `healthy` (service just forgot to report)
3. If unreachable → mark as `degraded` after 1 failure, `unhealthy` after 3 consecutive failures
4. If unhealthy for >5 minutes → generate notification in `notifications` table
5. Service is NOT auto-deregistered — it stays in registry with `unhealthy` status

**This is lenient by design.** A missed heartbeat is not an emergency. HeartBeat actively checks before declaring anything unhealthy.

### 3.4 Health Status Values

| Status | Meaning |
|---|---|
| `healthy` | Service is running and responsive |
| `degraded` | Service is running but reporting issues (1 failed poll) |
| `unhealthy` | Service is not responding (3+ consecutive failed polls) |
| `unknown` | Never reported health, recently registered |

---

## 4. Security Event Logging (Wazuh Integration)

### 4.1 What Gets Logged

HeartBeat logs security-relevant events in OCSF (Open Cybersecurity Schema Framework) format:

| Event | Category | Severity | Trigger |
|---|---|---|---|
| Auth success | authentication | low | Any valid API call |
| Auth failure | authentication | medium | Invalid key or secret |
| Brute force | authentication | high | 3+ auth failures from same key prefix in 5 min |
| Credential created | authorization | low | `POST /api/registry/credentials/generate` |
| Credential rotated | authorization | low | `POST /api/registry/credentials/{id}/rotate` |
| Credential revoked | authorization | medium | `POST /api/registry/credentials/{id}/revoke` |
| File uploaded | file_integrity | low | `POST /api/blobs/write` |
| Unknown service | registration | high | Unrecognized service_name in registration |
| Service URL changed | registration | medium | Same instance_id, different base_url |
| Daily limit exceeded | authorization | medium | `GET /api/limits/daily` returns blocked |
| Encryption violation | security | medium | Request without E2EE when required |

### 4.2 Where Events Go

**Local:** Written to `security_events` table in blob.db + JSONL log file at `{WAZUH_LOG_PATH}/heartbeat_security.log`

**Wazuh Agent** reads the JSONL log and forwards to Wazuh Manager for:
- SIEM correlation
- Intrusion detection
- Compliance reporting
- Alerting (PagerDuty, Slack, email)

### 4.3 External SIEM/Metrics/Logs Endpoints

| Endpoint | Format | Purpose | Auth |
|---|---|---|---|
| `GET /metrics` | Prometheus text | Operational metrics scrape | None |
| `GET /api/v1/events/blobs` | SSE (text/event-stream) | Real-time blob lifecycle events | Bearer |
| `GET /api/audit/verify` | JSON | Audit chain integrity verification | Bearer |
| JSONL log file | OCSF JSON (one per line) | Security events for Wazuh/SIEM | File access |
| `GET /health` | JSON | Service health for monitoring | None |

HeartBeat does **not** expose a direct API for querying security events or raw application logs. Those go through Wazuh (security) and the JSONL log files (application). For network-level tracing, see Section 5.

---

## 5. Tracing

### 5.1 What We Trace

Every request through the platform carries a **trace_id** header:

```
X-Trace-ID: trc-9f8e7d6c-5b4a-3e2d-1f0a
```

**Trace flow for a file upload:**

```
Float BulkContainer
  → [trc-abc123] POST Relay /api/ingest
    → [trc-abc123] GET HeartBeat /api/dedup/check
    → [trc-abc123] POST HeartBeat /api/blobs/write
    → [trc-abc123] POST HeartBeat /api/blobs/register
    → [trc-abc123] POST HeartBeat /api/audit/log
    → [trc-abc123] POST Core /api/enqueue
      → [trc-abc123] POST HeartBeat /api/v1/heartbeat/blob/{uuid}/status (extracting)
      → [trc-abc123] POST HeartBeat /api/v1/heartbeat/blob/{uuid}/status (validating)
      → [trc-abc123] POST HeartBeat /api/v1/heartbeat/blob/{uuid}/status (finalized)
      → [trc-abc123] POST HeartBeat /api/audit/log
```

**Every HeartBeat response includes `X-Trace-ID`** in the response headers, so callers can correlate.

### 5.2 Trace ID Generation

- If incoming request has `X-Trace-ID` → propagate it (same trace across all services)
- If no header → HeartBeat generates one (`trc-{uuid4}`)
- Trace ID is included in: all audit events, all metrics events, all error responses, all SSE events

### 5.3 What Gets Traced

| Span | Service | What's Recorded |
|---|---|---|
| Upload received | Relay | file_count, total_bytes, company_id |
| Dedup check | HeartBeat | hash, result (duplicate/unique) |
| Blob write | HeartBeat | data_uuid, file_size, write_duration_ms |
| Blob register | HeartBeat | data_uuid, status, retention_until |
| Processing started | Core | data_uuid, processing_stage |
| Processing completed | Core | data_uuid, duration_ms, output_count |
| Status update | HeartBeat | data_uuid, from_status, to_status |
| FIRS submission | Edge | submission_id, firs_response_code |

---

## 6. Application Updates & Schema Migration

### 6.1 How HeartBeat Coordinates Updates

HeartBeat serves as the **coordination point** for platform updates. The update lifecycle:

```
BEFORE UPDATE:
1. Admin sets config flag: PUT /api/config/heartbeat/update_mode {"value": "maintenance"}
2. HeartBeat broadcasts to all services via their /health endpoints
3. Services enter graceful drain mode (finish current work, reject new work)

DURING UPDATE:
4. Installer/CI deploys new service binaries
5. HeartBeat runs schema migrations on startup (auto-migrate)
6. HeartBeat pushes new configs to services via /internal/refresh-cache

AFTER UPDATE:
7. Services restart, re-register with HeartBeat
8. HeartBeat verifies all services healthy via /health polling
9. Admin clears maintenance: PUT /api/config/heartbeat/update_mode {"value": "normal"}
```

### 6.2 Schema Migration Framework (P2-C)

HeartBeat uses a forward-only SQL migration framework:

**Migration files:**
```
databases/migrations/blob/
  001_add_schema_migrations.sql
  002_add_security_events.sql
  003_add_submission_queue.sql

databases/migrations/registry/
  001_add_schema_migrations.sql
  002_add_satellite_registrations.sql
```

**On startup:**
1. Read `schema_migrations` table → get applied versions
2. Scan migration directory → find unapplied files
3. Apply in order (001, 002, 003...)
4. Record in `schema_migrations` with checksum + execution time
5. On failure → mark as `failed`, stop (don't apply later migrations)

**Checksum drift detection:** If a migration file's SHA-256 changes after being applied, HeartBeat logs a warning. This catches accidental edits to already-applied migrations.

**Rollback:** Each migration can include a `-- ROLLBACK` section. `DatabaseMigrator.rollback_last()` executes it.

### 6.3 Service-Owned Database Migrations

HeartBeat tracks schema versions for ALL databases via `database_catalog.schema_version`. But each service owns its own migrations:

| Service | Its Databases | Who Runs Migrations |
|---|---|---|
| HeartBeat | blob.db, registry.db, config.db | HeartBeat (on its own startup) |
| Core | invoices.db, customers.db, etc. | Core (on its own startup) |
| HIS | his_reference.db | HIS (on its own startup) |
| Float SDK | sync.db, core_queue.db | Float SDK (on its own startup) |

After running migrations, each service should update HeartBeat:
```
PUT /api/databases/{id}/status
{
    "schema_version": "003",
    "status": "active"
}
```

### 6.4 Float App Updates

Float (the PySide6 desktop app) has a unique update challenge — it's a desktop application, not a server.

**Update check flow:**
```
Float startup
  → GET /api/registry/config/_shared
  → Check "latest_float_version" key
  → Compare to current installed version
  → If newer: show "Update available" notification
  → User clicks "Update Now"
  → Float downloads update package from blob storage
  → Float exits, installer applies update, Float restarts
```

HeartBeat provides the version metadata and the update binary (stored as a blob). The actual update mechanism is the Installer's responsibility.

### 6.5 Database Backup

HeartBeat does NOT own backup execution — that's an ops/infrastructure concern. But HeartBeat knows **what** to back up:

```
GET /api/databases
```

Returns every database with path, size, owner. Backup scripts use this as the definitive list.

For SQLite databases, backup is a file copy (with WAL checkpoint first). For PostgreSQL, it's `pg_dump`. HeartBeat's reconciliation engine (P2-E) can verify backup integrity by comparing file hashes.

---

## 7. Primary/Satellite Deployment

### 7.1 Topology

```
Enterprise Deployment:

┌─────────────────────────────────┐
│ Client HQ (Primary)            │
│                                 │
│  HeartBeat Primary (port 9000)  │
│  ├── blob.db (all blobs)        │
│  ├── registry.db (all services) │
│  ├── config.db (all config)     │
│  └── /files_blob/ (storage)     │
│                                 │
│  Core, Relay (full services)    │
└─────────┬───────────────────────┘
          │
    ┌─────┼──────────────┐
    │     │              │
    ▼     ▼              ▼
┌───────────┐  ┌───────────┐  ┌───────────┐
│ Branch A  │  │ Branch B  │  │ Branch C  │
│ Satellite │  │ Satellite │  │ Satellite │
│ port 9001 │  │ port 9001 │  │ port 9001 │
│           │  │           │  │           │
│ Float App │  │ Float App │  │ Float App │
│ Relay     │  │ Relay     │  │ Relay     │
└───────────┘  └───────────┘  └───────────┘
```

### 7.2 Satellite Mode (Pure Proxy)

Satellite stores **nothing locally**. All operations forward to Primary:

| Satellite Endpoint | Forwards To (Primary) |
|---|---|
| `POST /satellite/blobs/write` | `POST /api/blobs/write` |
| `POST /satellite/blobs/register` | `POST /api/blobs/register` |
| `GET /satellite/config/{key}` | Cached from Primary (refreshed every 30s) |
| `GET /satellite/health` | Local health + Primary connectivity check |

### 7.3 Satellite Registration

On startup, Satellite calls Primary:

```
POST http://{primary_url}/primary/satellites/register
{
    "satellite_id": "branch-nairobi-001",
    "base_url": "http://10.0.2.5:9001",
    "capabilities": ["blob.forward", "config.cache"],
    "token": "{registration_token}"
}
```

Primary validates the token (bcrypt), registers the satellite, returns config.

### 7.4 Satellite Heartbeat

Every 30 seconds, Satellite pings Primary:
- Sends: health status, uptime, queue depth
- Receives: config updates, credential changes

If Primary is unreachable:
- Satellite continues serving cached config
- Blob forwards fail (clients get 503 "Primary unreachable")
- Satellite logs event, retries on next heartbeat cycle
- **No data loss** — uploads queue at the Float/Relay level until Primary returns

### 7.5 Primary Satellite Management

```
GET  /primary/satellites              — List all satellites + health
POST /primary/satellites/{id}/revoke  — Revoke a satellite
GET  /primary/satellites/{id}/health  — Individual satellite status
```

---

## 8. Queue Architecture — Who Owns What

### 8.1 HeartBeat Owns NO Queue

HeartBeat does not have a queue table. Queue ownership is:

| Queue | Owner | Written By | Processed By | HeartBeat Role |
|---|---|---|---|---|
| `core_queue` (blob processing) | Core | Relay (after upload) | Core (extraction pipeline) | Reads via `GET /api/v1/core_queue/status` for reconciliation |
| `core_queue.db` (SDK user events) | Core | Float SDK (user actions) | Core | None — HeartBeat does not access this |
| `edge_queue` (FIRS submission) | Edge | Core (after processing) | Edge (calls FIRS) | None — Edge manages its own lifecycle |

**Queue status polling**: Core owns queue status. Callers who need to check processing status should poll Core directly:

```
GET {core_url}/api/queue/status/{queue_id}
```

This is NOT a Relay endpoint and NOT a HeartBeat endpoint. Relay's role ends after enqueuing the file and returning `queue_id` to the caller.

### 8.2 HeartBeat's Reconciliation Access to core_queue

HeartBeat's hourly reconciliation job (P2-E) calls Core's HTTP API:

```
GET {CORE_API_URL}/api/v1/core_queue/status
Authorization: Bearer {heartbeat_api_key}:{heartbeat_api_secret}
```

Response:
```json
[
    {
        "queue_id": 1,
        "data_uuid": "abc-123",
        "status": "processed",
        "created_at": "2026-02-18T10:00:00Z",
        "processed_at": "2026-02-18T10:05:00Z"
    }
]
```

HeartBeat uses this to:
1. If `status == "processed"` and `blob_entries.status != "finalized"` → update blob status
2. If `status == "processing"` for >1 hour → create "stale_processing" notification
3. If `data_uuid` in core_queue but not in `blob_entries` → create "missing_blob_entry" notification

**HeartBeat NEVER opens Core's database directly.** All access is via HTTP API.

### 8.3 Dependency: Core Must Implement Delayed Cleanup

Core currently deletes `core_queue` entries immediately after processing. For reconciliation to work, Core must:
1. Keep processed entries for 24 hours before deletion
2. Expose `GET /api/v1/core_queue/status` endpoint
3. Use `status = "processed"` (not "COMPLETED") — status string alignment needed

This is documented in `Core/Documentation/CORE_QUEUE_DELAYED_CLEANUP_SPEC.md` and is **blocking HeartBeat reconciliation**.

### 8.4 How Customer Demo Questions Are Answered

**Q1 (Idempotency):** Blob registration is already idempotent (409 on duplicate). The FIRS submission replay mechanism lives in Edge, not HeartBeat. HeartBeat's contribution: immutable audit trail proves what was submitted and when.

**Q2 (Queuing during FIRS downtime):** Edge owns the `edge_queue` with its own retry/backoff logic. HeartBeat's contribution: blob status tracking (`preview_pending` state persists through outages), audit trail, reconciliation detects stuck items.

---

## 9. What registry.db Contains (Complete Reference)

| Table | Purpose | Key Fields |
|---|---|---|
| `service_instances` | Running services | instance_id, service_name, base_url, health_url, tier, status, last_health_at |
| `service_endpoint_catalog` | API endpoints per service | service_instance_id, path, method, description |
| `api_credentials` | Inter-service auth keys | api_key, api_secret_hash (bcrypt), service_name, status, permissions, expires_at |
| `key_rotation_log` | Credential lifecycle audit | credential_id, action (created/rotated/revoked), performed_by, old_key_prefix |
| `service_config` | Per-service key-value config | service_name, config_key, value, is_encrypted |
| `satellite_registrations` | Satellite instances | satellite_id, token_hash, base_url, status, last_seen_at |
| `schema_migrations` | Migration tracking | version, name, checksum, applied_at, status |

---

## 10. Implementation Status (Known Gaps)

For transparency, here is the current implementation status of all designed features:

### Already Built (Phase 1 Complete)

| Feature | Status | Notes |
|---|---|---|
| Service registry (register, discover, health) | **BUILT** | Full CRUD + health polling |
| API credentials (generate, rotate, revoke, validate) | **BUILT** | bcrypt-hashed, expiry, permissions |
| Blob storage (write, register, status) | **BUILT** | Filesystem-backed, SHA256 dedup |
| Audit logging (fire-and-forget, INSERT-only) | **BUILT** | Immutability triggers in place |
| Metrics reporting (fire-and-forget) | **BUILT** | Push from services → HeartBeat |
| Daily limit checking | **BUILT** | Per-company, configurable |
| Dedup checking | **BUILT** | SHA256 hash-based |
| Service config (key-value per service) | **BUILT** | Resolution chain: service → _shared → env |
| Primary/Satellite scaffolding | **BUILT** | Package structure, registration endpoint exists |

### Not Yet Built (Phase 2 Planned)

| Gap | Status | When |
|---|---|---|
| config.db (3rd database) | Designed, not built | Phase 2 Layer 2 |
| database_catalog table | Designed, not built | Phase 2 Layer 2 |
| SQLCipher encryption | Designed concept only | Phase 3 |
| Prometheus `/metrics` endpoint | Designed, not built | Phase 2 Layer 1 |
| Wazuh security events | Designed, not built | Phase 2 Layer 2 |
| SSE event streaming | Designed, not built | Phase 2 Layer 3 |
| Submission queue | Designed, not built | Phase 2 Layer 3 |
| Primary/Satellite full proxy | Scaffolding only | Phase 2 Layer 4 |
| Reconciliation engine | Designed, not built | Phase 2 Layer 4 |
| Audit checksum chain verification | Designed, not built | Phase 2 Layer 1 |
| E2EE key distribution via config | Designed, not built | Phase 2 Layer 1 |
| Float app update mechanism | Concept only | Phase 3 |
| Database backup coordination | Concept only | Phase 3 |

### Built by Other Services (Not HeartBeat's Responsibility)

| Feature | Owner | Status |
|---|---|---|
| Relay E2EE decryption | Relay | **BUILT** (`src/crypto/envelope.py`) |
| Relay `/metrics` endpoint | Relay | **BUILT** (Phase 1 stub gauges) |
| Relay `/health` endpoint | Relay | **BUILT** (checks HeartBeat, module cache, Redis) |
| Relay HMAC authentication | Relay | **BUILT** (canonical scheme from Part 1 §3.5) |
| Relay Redis rate limiting | Relay | **BUILT** (atomic INCR + EXPIRE, graceful degradation) |
| Float SDK E2EE encryption | Float SDK | **NOT BUILT** — must be implemented |
| Float SDK HMAC signing (correct scheme) | Float SDK | **NOT BUILT** — current scheme is wrong, must update |
| Core queue status endpoint | Core | **NOT BUILT** — Core Phase 0 only has database layer |
| Core finalize endpoint | Core | **NOT BUILT** — Core Phase 0 only |

---

## 11. Superseded Documentation Index

These documents are now superseded or partially replaced:

| Document | Location | Status |
|---|---|---|
| `HEARTBEAT_INTEGRATION.md` v2.0 | `Float/App/SDK/` | **API endpoints still valid.** Registry/credential sections replaced by Part 1 Sections 2-4. |
| `REVAMP_HANDOVER.md` | `HeartBeat/Documentation/` | **Superseded.** MinIO replaced by filesystem. "Parent-Client" terminology is "Primary/Satellite" in code. Priority order replaced by Part 3 Phase 2 plan. |
| `REVAMP_PHASE_2.md` | `HeartBeat/Documentation/` | **Still valid for P2-A through P2-F implementation details.** This contract adds database catalog + submission queue (not in Phase 2 spec). |
| `HEARTBEAT_REGISTRY_NOTE.md` | `Float/App/SDK/Documentation/` | **Addressed.** Part 1 Section 4-5 answers all SDK team questions. |
| `FLOAT_INTEGRATION_GUIDE.md` | `Relay/Documentation/` | **Still valid for BulkContainer→Relay flow.** Part 2 Section 2 provides the authoritative version. |
| `DATABOX_SDK_CONTRACT.md` | `Float/App/SDK/Documentation/` | **Still valid for data field mappings.** Part 2 Section 5 is consistent with it. |
| `RECONCILIATION_KICKSTART.md` | `HeartBeat/Documentation/` | **Still valid for implementation reference.** Part 3 Section 6.2 is the authoritative spec. |
| `DATABASE_LANDSCAPE.md` | `Float/App/Documentation/` | **Partially superseded.** Database catalog (Part 1 Section 5) is the new source of truth for DB inventory. |

**Rule for teams:** When this 3-part contract conflicts with any other document, this contract wins.

---

*End of Part 3. This completes the HeartBeat Service Contract (Parts 1-3).*

*Summary:*
- **Part 1:** Registry, Credentials, Database Catalog, E2EE Protocol, User Auth — what services need to integrate
- **Part 2:** DataBox, BulkContainer, SDK — how Float components connect, response shapes, status definitions
- **Part 3:** Audit, Metrics, Health, Tracing, Updates, Deployment — operational concerns, implementation status
