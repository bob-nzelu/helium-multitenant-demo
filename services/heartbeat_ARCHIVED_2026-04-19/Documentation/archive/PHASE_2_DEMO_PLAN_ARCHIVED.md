# HeartBeat Phase 2 — Demo-Driven Implementation Plan

**Date:** 2026-02-18
**Goal:** Build features that directly answer the customer's 6 demo questions
**Approach:** Demo-Driven — every feature maps to a customer question

---

## Customer Questions → Feature Mapping

| # | Customer Question | What We Build | Where |
|---|---|---|---|
| Q1 | Idempotency & replay for failed FIRS submissions | Idempotent blob registration (already done) + submission tracking table + replay API. **HeartBeat tracks submission state, it does NOT submit to FIRS.** Edge/Core owns FIRS HTTP calls. HeartBeat owns the queue, retry policy, and audit trail. | HeartBeat |
| Q2 | Queuing during FIRS downtime | Submission queue with status lifecycle. When FIRS is down, submissions stay `queued`. When FIRS returns, the submitting service (Edge) drains the queue via HeartBeat API. HeartBeat provides visibility (queue depth, oldest age, dead-letter count). | HeartBeat |
| Q3 | Ownership: validation vs transport | Architecture metadata endpoint — static JSON describing service boundaries. Already clean in code, just needs to be queryable. | HeartBeat |
| Q4 | Audit log immutability | SQLite triggers preventing UPDATE/DELETE on audit tables + SHA-256 checksum chain for tamper detection + Wazuh security event logging (P2-B) | HeartBeat |
| Q5 | Configurable vs hard-coded | config.db (3rd database) with CRUD API + tier-based limits + feature flags + DB catalog (tenant DB registry) | HeartBeat |
| Q6 | Deployment: on-prem vs hybrid | Primary/Satellite implementation. Primary = full HeartBeat. Satellite = pure proxy that forwards to Primary. | HeartBeat |

---

## Critical Correction: FIRS Submission Ownership

HeartBeat is **transport + storage + audit**. It does NOT call FIRS.

The submission flow:
```
Core processes blob → Core calls Edge "submit to FIRS"
                    → Edge calls FIRS BIS 3.0 API
                    → Edge reports result back
                    → Core updates HeartBeat submission_queue status

HeartBeat's role:
  - Owns the submission_queue table (persistent record)
  - Provides enqueue/dequeue/status/replay APIs
  - Tracks retry count, backoff schedule, dead-letter
  - Does NOT make the FIRS HTTP call
  - Does NOT know FIRS payload structure (opaque JSON)
```

This preserves the ownership boundary (Q3): HeartBeat = transport, Core = validation, Edge = external delivery.

---

## Implementation Order

### Layer 0: Foundation (blocks everything)
**P2-C: SQL Migration Framework**
- `schema_migrations` table in both blob.db and registry.db
- `DatabaseMigrator` class: apply, skip, drift-detect, rollback
- Wire into startup lifespan
- All subsequent schema changes go through migrations
- ~12 tests

### Layer 1: Audit Immutability (Q4) + Prometheus (P2-A)
**Q4: Audit Immutability**
- SQLite triggers: prevent UPDATE/DELETE on `audit_events`, `key_rotation_log`, `blob_cleanup_history`
- Checksum chain: SHA-256(event_data + prev_checksum) on new audit rows
- Verify endpoint: `GET /api/audit/verify` — validates chain integrity
- ~10 tests

**P2-A: Prometheus Metrics**
- Counters, histograms, gauges for all blob/registry/credential operations
- `GET /metrics` endpoint (unauthenticated, Prometheus scrape format)
- FastAPI middleware for automatic request duration
- ~10 tests

### Layer 2: Security + Extensibility
**P2-B: Wazuh Security Integration**
- `security_events` table (via migration from Layer 0)
- `WazuhEventEmitter`: OCSF-format JSONL log writer
- Instrument: auth failures, brute-force detection, credential lifecycle, uploads
- ~10 tests

**Q5: config.db + Extensibility + DB Catalog**
- New 3rd database: `config.db`
- Tables: `config_entries`, `tier_limits`, `feature_flags`, `database_catalog`
- `database_catalog` table: registers every tenant DB in the platform
  - `db_name` (logical: "sync", "invoices", "his_reference")
  - `db_physical_name` (actual file: "sync-898776.db", "invoices_pikwik.db")
  - `db_path` (location: "c:\Helium\Sync\db\test\...")
  - `db_engine` ("sqlite" | "postgresql")
  - `owner_service` ("float", "core", "his", "heartbeat")
  - `tenant_id` (which tenant installation)
  - `credential_id` → FK to `api_credentials` in registry.db (who can access)
  - `status` ("active" | "migrating" | "archived")
  - `created_at`, `updated_at`
- CRUD API: 7 config endpoints + 4 DB catalog endpoints
- Seed: default tier limits (Standard/Pro/Enterprise)
- ~14 tests

### Layer 3: SSE (submission queue removed — HeartBeat owns no queue)
**Q1+Q2: Answered without a HeartBeat queue**
- Q1 (Idempotency): Blob registration already idempotent (409). FIRS replay is Edge's responsibility.
- Q2 (Queuing during downtime): Edge owns edge_queue with retry/backoff. HeartBeat contributes: blob status tracking (preview_pending persists through outages), audit trail, reconciliation detects stuck items.
- HeartBeat's reconciliation (P2-E) queries Core's queue via `GET /api/v1/core_queue/status` to cross-verify blob processing.
- No new tables for Q1/Q2.

**P2-D: SSE Event Streaming**
- `EventBus` in-process pub/sub
- `GET /api/v1/events/blobs` SSE endpoint
- Events: blob.registered, blob.status_changed, blob.finalized, blob.error
- Keepalive every 30s
- ~8 tests

### Layer 4: Primary/Satellite + Reconciliation
**Q6: Primary/Satellite Implementation**
- `satellite_registrations` table in registry.db (via migration)
- **Primary endpoints** (Hub exposes these):
  - `POST /primary/satellites/register` — Satellite self-registration
  - `GET /primary/satellites` — List satellites + health
  - `POST /primary/satellites/{id}/revoke` — Revoke a satellite
- **Satellite endpoints** (proxy to Primary):
  - `POST /satellite/blobs/write` → forwards to Primary
  - `POST /satellite/blobs/register` → forwards to Primary
  - `GET /satellite/health` — local health + Primary connectivity
  - `GET /satellite/config/{key}` — cached config from Primary
- `PrimaryClient` (httpx): Satellite→Primary communication
- Satellite heartbeat: periodic ping to Primary (APScheduler)
- Pure proxy: Satellite stores nothing locally, forwards everything
- ~18 tests

**P2-E: Reconciliation Engine**
- 5-phase job: orphans, missing files, stuck processing, expired retention, batch integrity
- APScheduler hourly job
- 3 endpoints: trigger, history, notifications
- ~12 tests

### Layer 5: Ancillary + Architecture
**P2-F: Ancillary APIs**
- `blob_outputs` CRUD (table exists, no API yet)
- `/internal/refresh-cache` push to Relay instances
- ~8 tests

**Q3: Architecture Metadata**
- `GET /api/architecture/services` — service boundary definitions
- `GET /api/architecture/data-flows` — blob lifecycle state machine
- Static JSON responses (no DB) — purely for demo/documentation
- ~4 tests

---

## Database Catalog Design (Q5 — New Concept)

### The Problem Today

Services access their DBs via hardcoded file paths or env vars. There is:
- No central registry of what databases exist
- No mapping from logical name → physical file → tenant
- No credential check for SQLite access (filesystem permissions only)
- No way for HeartBeat to know what DBs Float or Core are using

### The Solution: `database_catalog` table in config.db

```sql
CREATE TABLE IF NOT EXISTS database_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Logical identity
    db_logical_name TEXT NOT NULL,           -- "sync", "invoices", "his_reference", "blob"
    db_category TEXT NOT NULL,               -- "operational", "reference", "audit", "config"
    tenant_id TEXT NOT NULL,                 -- "pikwik-tenant-1", "global"
    owner_service TEXT NOT NULL,             -- "float", "core", "his", "heartbeat", "relay"

    -- Physical location
    db_physical_name TEXT NOT NULL,          -- "sync-898776.db", "invoices.db"
    db_path TEXT NOT NULL,                   -- Full path or relative path
    db_engine TEXT NOT NULL DEFAULT 'sqlite', -- "sqlite" | "postgresql"

    -- Access control
    credential_id TEXT,                      -- FK concept → api_credentials.id in registry.db
    connection_string TEXT,                  -- For PostgreSQL: full conn string (encrypted in config.db)
    is_encrypted BOOLEAN DEFAULT 0,          -- SQLCipher encryption flag (future)

    -- State
    status TEXT NOT NULL DEFAULT 'active',   -- "active" | "migrating" | "archived" | "error"
    schema_version TEXT,                     -- Current migration version applied
    size_bytes INTEGER,                      -- Last known size

    -- Metadata
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(db_logical_name, tenant_id),
    CONSTRAINT engine_values CHECK (db_engine IN ('sqlite', 'postgresql')),
    CONSTRAINT status_values CHECK (status IN ('active', 'migrating', 'archived', 'error'))
);

CREATE INDEX IF NOT EXISTS idx_database_catalog_tenant
    ON database_catalog(tenant_id, owner_service);

CREATE INDEX IF NOT EXISTS idx_database_catalog_service
    ON database_catalog(owner_service, status);
```

### API Endpoints for DB Catalog

```
GET  /api/databases                              — List all registered databases
GET  /api/databases/{tenant_id}                  — List databases for a tenant
POST /api/databases/register                     — Register a new database
PUT  /api/databases/{id}/status                  — Update DB status (migrating, archived)
```

### How Services Use It

On startup, each service:
1. Calls `POST /api/registry/register` (existing — registers itself)
2. Calls `POST /api/databases/register` (new — registers its databases)
3. HeartBeat now knows every DB in the platform

For your example:
- Float instance 898776 starts up
- Registers `sync.db` as `{"db_logical_name": "sync", "db_physical_name": "sync-898776.db", "db_path": "c:\\Helium\\Sync\\db\\test\\sync-898776.db", "tenant_id": "898776", "owner_service": "float"}`
- HeartBeat records this in `database_catalog`
- Credential for access is linked via `credential_id` → the API key in registry.db that Float uses

---

## New Files Summary

| File | Layer | Purpose |
|---|---|---|
| `src/database/migrator.py` | 0 | SQL migration runner |
| `src/database/audit_guard.py` | 1 | Immutability triggers + checksum chain |
| `src/api/internal/audit_verify.py` | 1 | Audit verification endpoint |
| `src/observability/__init__.py` | 1 | Package init |
| `src/observability/metrics.py` | 1 | Prometheus metric definitions |
| `src/api/observability/__init__.py` | 1 | Package init |
| `src/api/observability/prometheus.py` | 1 | GET /metrics endpoint |
| `src/observability/wazuh.py` | 2 | Wazuh JSONL event emitter |
| `src/database/config_db.py` | 2 | ConfigDatabase (3rd DB) |
| `databases/config_schema.sql` | 2 | config.db schema + seed |
| `src/api/internal/config_api.py` | 2 | Config + tier + feature flag + DB catalog endpoints |
| `src/database/queue_operations.py` | 3 | Submission queue DB operations |
| `src/handlers/submission_handler.py` | 3 | Submission enqueue/status/replay logic |
| `src/api/internal/submissions.py` | 3 | Submission queue API endpoints |
| `src/events/__init__.py` | 3 | Package init |
| `src/events/bus.py` | 3 | In-process pub/sub for SSE |
| `src/api/streaming/__init__.py` | 3 | Package init |
| `src/api/streaming/sse.py` | 3 | SSE event stream endpoint |
| `src/clients/primary_client.py` | 4 | Satellite→Primary HTTP client |
| `src/handlers/satellite_handler.py` | 4 | Satellite registration + management |
| `src/api/satellite/endpoints.py` | 4 | Satellite proxy endpoints |
| `src/api/primary/endpoints.py` | 4 | Primary satellite-management endpoints |
| `src/handlers/reconciliation_handler.py` | 4 | 5-phase reconciliation engine |
| `src/api/internal/reconciliation.py` | 4 | Reconciliation API endpoints |
| `src/api/internal/blob_outputs.py` | 5 | blob_outputs CRUD |
| `src/api/internal/refresh_cache.py` | 5 | Push credential updates to Relays |
| `src/api/internal/architecture.py` | 5 | Service boundary metadata |
| `config/prometheus.yml` | 1 | Reference Prometheus config |
| `config/wazuh_rules.xml` | 2 | Reference Wazuh rules |

## Migration Files

```
databases/migrations/blob/
  001_add_schema_migrations.sql
  002_add_security_events.sql         (P2-B)
  003_add_submission_queue.sql        (Q1+Q2)
  004_add_audit_checksums.sql         (Q4)

databases/migrations/registry/
  001_add_schema_migrations.sql
  002_add_satellite_registrations.sql (Q6)
```

## Modified Files

| File | Changes |
|---|---|
| `requirements.txt` | Add: prometheus_client, apscheduler, sse-starlette |
| `src/config.py` | Add: config_db_path, metrics/wazuh/sse/reconciliation config fields |
| `src/main.py` | Add: migration runner, APScheduler, new routers, audit triggers |
| `src/handlers/blob_handler.py` | Instrument: Prometheus + Wazuh + EventBus |
| `src/handlers/status_handler.py` | Instrument: Prometheus + EventBus |
| `src/handlers/credential_handler.py` | Instrument: Wazuh security events |
| `src/api/internal/registry.py` | Instrument: Wazuh registration events |
| `src/database/connection.py` | Add: submission queue operations, migrator hook |
| `src/database/registry.py` | Add: satellite CRUD, migrator hook |
| `tests/conftest.py` | Add: config_db, submission_queue, satellite fixtures |

## Test Estimates

| Area | Tests | Total |
|---|---|---|
| P2-C Migration Framework | 12 | 12 |
| Q4 Audit Immutability | 10 | 22 |
| P2-A Prometheus | 10 | 32 |
| P2-B Wazuh | 10 | 42 |
| Q5 config.db + DB Catalog | 14 | 56 |
| Q1+Q2 Submission Queue | 15 | 71 |
| P2-D SSE | 8 | 79 |
| Q6 Primary/Satellite | 18 | 97 |
| P2-E Reconciliation | 12 | 109 |
| P2-F Ancillary | 8 | 117 |
| Q3 Architecture | 4 | 121 |
| **Total new** | **121** | |

**Projected total: 242 (existing) + 121 (new) = ~363 tests, 90%+ coverage**

---

## Demo Script

### Q1: Idempotency & Replay
```bash
# Enqueue same submission twice — second is a no-op (idempotent)
POST /api/submissions/enqueue  {"submission_id": "sub-001", "blob_uuid": "..."}
POST /api/submissions/enqueue  {"submission_id": "sub-001", "blob_uuid": "..."}
# → Both return 200 with identical response

# Replay a rejected submission
POST /api/submissions/sub-001/replay
# → Re-queues with attempt_count incremented
```

### Q2: Queuing During Downtime
```bash
# Queue 3 submissions (FIRS is down — nobody is draining)
POST /api/submissions/enqueue × 3

# See queue depth
GET /api/submissions/queue/stats
# → {pending: 3, oldest_queued_seconds: 120, dead_letter: 0}

# Edge comes back, drains: GET /api/submissions/queue/pending
# Edge submits to FIRS, then: POST /api/submissions/{id}/status {"status": "accepted"}
```

### Q3: Ownership Boundaries
```bash
GET /api/architecture/services
# → JSON: HeartBeat=transport, Core=validation, Relay=ingestion, Edge=delivery
```

### Q4: Audit Immutability
```bash
# Attempt DELETE on audit row → blocked by SQLite trigger
# Show checksum chain verification
GET /api/audit/verify?from_id=1&to_id=50
# → {verified: true, tampered_rows: [], chain_length: 50}
```

### Q5: Extensibility
```bash
GET /api/config/heartbeat                    # All configurable params
PUT /api/config/heartbeat/max_retries        # Change live, no restart
GET /api/tiers/enterprise/limits             # Tier-based limits
GET /api/databases/pikwik-tenant-1           # All DBs for this tenant
```

### Q6: Deployment Model
```bash
# Primary running on port 9000
# Satellite starts on port 9001 with HEARTBEAT_MODE=satellite, HEARTBEAT_PRIMARY_URL=http://localhost:9000

# Satellite auto-registers with Primary
GET /primary/satellites  # → [{id: "branch-1", status: "active", last_seen: "..."}]

# Satellite forwards blob write to Primary
POST http://localhost:9001/satellite/blobs/write  # → proxied to Primary
```
