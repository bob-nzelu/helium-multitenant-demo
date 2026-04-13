# CORE SERVICE — ARCHITECTURAL DECISIONS v2.0

**Version:** 2.0
**Date:** 2026-03-18
**Status:** BINDING — All implementations must follow these decisions
**Supersedes:** DECISIONS_V1.md (2026-01-31, archived to `_archived/`)

---

## CHANGELOG (V1 → V2)

| Decision | V1 (Jan 2026) | V2 (Mar 2026) | Reason |
|----------|---------------|---------------|--------|
| Database | SQLite for all tiers | **PostgreSQL** (single instance, 5 schemas) | Standing order: PostgreSQL for multi-tenant, SQLite only for embedded |
| Real-time | WebSocket with DB triggers | **SSE only** (`GET /sse/stream`) | SDK already targets `core_sse_url`; simpler, one-directional |
| Schemas | Hand-written per-service | **Canonical schemas** as source of truth | 4 canonical SQL files finalized (blob v1.4.0, invoice v2.1.1.0, customer v1.2.0, inventory v1.0.0) |
| Workstream structure | Phase-based (Phase 0-8) | **Functional workstreams** (WS0-WS6) | Enables true parallelism; phases are sequential and block each other |
| Endpoint count | 18 endpoints | **24 endpoints** | 3 new (enqueue, process_preview, finalize) + 3 notification endpoints |
| Cleanup | Immediate delete | **24-hour delayed cleanup** via APScheduler | HeartBeat reconciliation window |
| SQLite client | Dual client (PG + SQLite) | **PostgreSQL only** (rebuild from scratch) | SQLiteClient archived; PostgreSQL is sole target |

---

## DECISION 1: Database — PostgreSQL

**Decision:** Single PostgreSQL 15+ instance with 5 logical schemas.

**Schemas:**
| Schema | Domain | Canonical Source | Tables |
|--------|--------|-----------------|--------|
| `invoices` | Invoice records | `Documentation/Schema/invoice/06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` | 9 tables, 191 fields |
| `customers` | Customer master data | `Documentation/Schema/customer/02_CUSTOMER_DB_CANONICAL_SCHEMA_V1.sql` | 7 tables, 115 fields |
| `inventory` | Product/service master data | `Documentation/Schema/inventory/02_INVENTORY_DB_CANONICAL_SCHEMA_V1.sql` | 6 tables, 80 fields |
| `core` | Queue management, dedup, config | Core-internal (no canonical — defined in WS0) | ~4 tables |
| `notifications` | Alerts, approvals | Core-internal (no canonical — defined in WS6) | ~3 tables |

**Connection:**
- Library: `psycopg[binary]` v3 (async via `AsyncConnectionPool`)
- Pool size: min=5, max=20 (configurable via `CORE_DB_POOL_MIN` / `CORE_DB_POOL_MAX`)
- All queries use parameterized `$N` placeholders (native PostgreSQL)
- Each schema has its own `search_path` set per connection/transaction

**Rationale:** PostgreSQL is the standing order for multi-tenant services. Core manages 4 canonical domains + internal state. Connection pooling handles concurrency. Docker Compose provides PostgreSQL alongside Core.

**Migration from V1:** SQLiteClient and all SQLite-specific code archived to `src/_archive/`. New implementation uses psycopg3 exclusively.

---

## DECISION 2: Real-time — SSE Only

**Decision:** Server-Sent Events (SSE) at `GET /sse/stream`. No WebSocket.

**Two distinct stream types:**

| Stream | Event Types | Persistence | Consumer |
|--------|-------------|-------------|----------|
| **ENUMed Statuses** | `invoice.status_changed`, `invoice.created`, `customer.created`, `customer.updated`, `inventory.created`, `inventory.updated` | SDK writes to sync.db | SWDB re-renders |
| **Processing Logs** | `processing.log`, `processing.progress` | **Ephemeral** (display only) | Float ProgressFeed + ResultPage live counter |

**SSE Event Format:**
```
event: {event_type}
data: {"data_uuid": "...", ...payload...}
id: {monotonic_sequence}
retry: 5000

```

**Connection Management:**
- Clients connect with `Authorization: Bearer {jwt}` (optional for local trust)
- Server maintains per-client event queue
- Keep-alive: `:heartbeat\n\n` every 15 seconds
- Reconnection: Client sends `Last-Event-ID` header; server replays missed events from in-memory buffer (last 1000 events)
- No authentication filtering in v1 (single-tenant); permission scoping is future

**Rationale:** SSE is simpler than WebSocket for one-directional server→client push. SDK already has `core_sse_url` configured. WebSocket's bidirectional capability is unnecessary — SDK communicates with Core via HTTP endpoints, not via the event stream.

---

## DECISION 3: Framework — FastAPI Async

**Decision:** FastAPI with uvicorn ASGI server.

**Stack:**
- `fastapi` — HTTP framework
- `uvicorn[standard]` — ASGI server
- `psycopg[binary]` — PostgreSQL async driver
- `sse-starlette` — SSE response helper
- `apscheduler` — Delayed job scheduling
- `pydantic` v2 — Request/response validation
- `structlog` — Structured JSON logging
- `prometheus-client` — Metrics

**App lifecycle:**
- `lifespan` context manager handles startup/shutdown
- Startup: PostgreSQL pool, APScheduler, SSE manager, queue scanner
- Shutdown: Drain SSE connections, close pool, shutdown scheduler

**Port:** 8080 (default, configurable via `CORE_PORT`)

---

## DECISION 4: Canonical Schemas as Source of Truth

**Decision:** All table definitions derive from the 4 canonical SQL files in `Helium/Documentation/Schema/`.

**Rules:**
1. Core MUST NOT define its own table structures that contradict canonical schemas
2. Core's PostgreSQL DDL is a **direct translation** of canonical SQLite DDL (type mapping: `TEXT` → `TEXT`, `INTEGER` → `INTEGER`/`BIGINT`, `REAL` → `NUMERIC`, `AUTOINCREMENT` → `SERIAL`)
3. Core MAY add PostgreSQL-specific features (schemas, `pg_notify`, `tsvector`, partial indexes, `JSONB` instead of `TEXT` for JSON fields)
4. Any field additions require updating the canonical SQL first via Schema Governance Playbook

**Core-internal tables** (not in canonical schemas):
- `core.core_queue` — processing queue
- `core.processed_files` — deduplication tracking
- `core.transformation_scripts` — customer-specific scripts
- `core.config` — runtime configuration
- `notifications.notifications` — alerts and approvals
- `notifications.notification_reads` — read tracking

---

## DECISION 5: Functional Workstream Structure

**Decision:** 7 functional workstreams (WS0-WS6) replace the 8 phase-based structure.

**Dependency graph:**
```
WS0: FOUNDATION ──┬── WS1: INGESTION ──────┐
                  ├── WS2: PROCESSING ──────┼── WS3: ORCHESTRATOR ── WS5: FINALIZE
                  ├── WS4: ENTITY CRUD      │
                  └── WS6: OBSERVABILITY ───┘
```

**Parallelism:** After WS0, workstreams WS1, WS2, WS4, WS6 run in parallel with zero cross-dependencies.

**Each WS produces:** `MENTAL_MODEL.md`, `API_CONTRACTS.md`, `DECISIONS.md`, `DEPENDENCIES.md`, `DELIVERABLES.md`, `STATUS.md`

---

## DECISION 6: Processing Pipeline — Two-Stage Flow

**Decision:** Preview → Finalize. Unchanged from V1.

**Stage 1: Preview** (`POST /api/v1/process_preview`)
- Phases 1-7: Fetch → Parse → Transform → Enrich → Resolve → Porto Bello → Branch
- Output: 6 preview files stored in HeartBeat blob (7-day retention)
- Does NOT create database records
- Blocking call with 300-second timeout (returns 202 if exceeded)

**Stage 2: Finalize** (`POST /api/v1/finalize`)
- Phase 8: Apply user edits → Create DB records → Queue to Edge → Broadcast SSE
- Non-blocking (data already processed)
- Triggers 24-hour delayed cleanup of core_queue entry

---

## DECISION 7: Worker Deployment Model

**Decision:** Tier-specific workers. Unchanged from V1 but clarified.

| Tier | Backend | Workers | Database |
|------|---------|---------|----------|
| Test/Standard | `ThreadPoolExecutor` | 5-10 concurrent | PostgreSQL (Docker) |
| Pro/Enterprise | Celery + Redis | 10-50 distributed | PostgreSQL (managed) |

**Interface:** All workers inherit from `BaseWorker` ABC. Code is identical across tiers; only the executor changes.

**Batch size:** 100 invoices per task (configurable via `CORE_BATCH_SIZE`).

---

## DECISION 8: Error Handling

**Decision:** Structured error responses with classified error codes.

**Error Response Format:**
```json
{
    "error": "PROCESSING_FAILED",
    "message": "Human-readable description",
    "details": [
        {"field": "invoice_number", "error": "Duplicate detected"}
    ]
}
```

**Error Codes:**
| Code | HTTP | When |
|------|------|------|
| `CORE_ENQUEUE_FAILED` | 500 | Queue write failed |
| `BLOB_NOT_FOUND` | 404 | HeartBeat blob fetch failed |
| `INVALID_FILE_FORMAT` | 400 | Unsupported file type |
| `PROCESSING_FAILED` | 500 | Pipeline error |
| `INVALID_EDITS` | 400 | User edits fail validation |
| `QUEUE_NOT_FOUND` | 404 | queue_id not in core_queue |
| `INVOICE_NOT_FOUND` | 404 | invoice_id not found |
| `CUSTOMER_NOT_FOUND` | 404 | customer_id not found |
| `INVENTORY_NOT_FOUND` | 404 | product_id not found |
| `UNAUTHORIZED` | 401 | Missing/invalid auth |
| `FORBIDDEN` | 403 | Insufficient permissions |
| `SERVICE_UNAVAILABLE` | 503 | Core not ready |
| `TIMEOUT` | 504 | Processing exceeded 300s |

**Graceful Degradation:**
- Prodeus API down → Continue without enrichment, flag as `MANUAL`
- HeartBeat audit down → Continue processing, skip audit log
- Edge down → Keep in queue, auto-retry later
- Circuit breaker: After 5 consecutive failures, open for 60 seconds

---

## DECISION 9: IRN Generation

**Decision:** Core generates IRN (not FIRS). Unchanged from V1.

**Format:** `{YYYYMMDD}-{SHA256(seller_tin:buyer_tin:amount:date)[:12]}-{sequence}`
- Example: `20260318-5a3b9f2c1e4d-001`
- Deterministic: same input data = same IRN (idempotent)
- Unique: compound of date + hash + sequence
- FIRS returns its own IRN stored in `firs_confirmation` field

---

## DECISION 10: QR Code Generation

**Decision:** Core generates QR code at finalization. Unchanged from V1.

**Content:** JSON-encoded `{irn, invoice_number, total_amount, issue_date, seller_tin}`
**Format:** PNG, 200×200px, Base64-encoded in `qr_code_data` field
**Fixed PDF:** Original PDF + IRN text + QR image overlay. Placement from EIC config (tenant-specific) or default intelligence.

---

## DECISION 11: 24-Hour Delayed Cleanup

**Decision:** Core queue entries are NOT deleted immediately after processing.

**Lifecycle:**
```
PENDING → PROCESSING → PROCESSED (processed_at=now)
                                    ↓
                          APScheduler: delete in 24h
                                    ↓
                          DELETED (removed from table)
```

**Failed entries:** Retained 30 days for debugging.
**Preview data:** Expires after 7 days (blob retention).
**Job persistence:** APScheduler with PostgreSQL job store (survives restarts).

---

## DECISION 12: Deduplication Strategy

**Decision:** Three-layer dedup across services.

| Layer | Service | Method | Scope |
|-------|---------|--------|-------|
| 1 | Relay | SHA256 hash check | Current request batch |
| 2 | HeartBeat | SHA256 against `processed_files` | Historical (all processed) |
| 3 | Core | IRN uniqueness check | Invoice-level (same data = same IRN) |

Core maintains `core.processed_files` table: `(file_hash TEXT UNIQUE, queue_id, processed_at)`.

---

## DECISION 13: Access Control — RBAC

**Decision:** Role-Based Access Control via permissions table. Unchanged from V1.

**Permissions:**
```
invoice.create, invoice.update, invoice.update_status, invoice.mark_as_paid,
invoice.delete, invoice.accept_inbound
customer.create, customer.update, customer.delete
inventory.create, inventory.update, inventory.delete
system.admin (grants all), system.view_audit_logs
```

**Implementation:** Middleware checks `user_permissions` table before each CRUD operation. `system.admin` bypasses all checks.

---

## DECISION 14: Transformation Scripts

**Decision:** Customer-specific scripts stored in `core.transformation_scripts` table. Unchanged from V1.

**Execution:** Dynamic import + `exec()` in isolated namespace with AST validation.
**Safety:** Block `os`, `subprocess`, `eval`, `exec` (recursive), `open`, `__import__`.
**Modules:** `extract_module`, `validate_module`, `format_module`, `enrich_module`.

---

## DECISION 15: Caching Strategy

**Decision:** In-memory cache for customer/inventory master data. Unchanged from V1.

**TTL:** 1 hour (configurable).
**Invalidation:** On UPDATE/DELETE via pg_notify.
**Implementation:** Simple dict with timestamp-based expiry. No Redis for Test/Standard.

---

## DECISION 16: Monitoring — Prometheus Metrics

**Decision:** Standard Prometheus metrics. Unchanged from V1.

**Metrics:**
- `helium_core_files_processed_total{status}` — Counter
- `helium_core_processing_duration_seconds` — Histogram (buckets: 1s, 5s, 30s, 60s, 300s)
- `helium_core_queue_depth{status}` — Gauge
- `helium_core_workers_active` — Gauge
- `helium_core_invoices_total` — Counter
- `helium_core_errors_total{error_code}` — Counter
- `helium_core_sse_connections_active` — Gauge

---

## DECISION 17: Logging — Structured JSON

**Decision:** Structured JSON logs via `structlog`. Unchanged from V1.

**Fields:** `timestamp`, `level`, `service=core`, `worker`, `event_type`, `queue_id`, `data_uuid`, `x_trace_id`, `duration_ms`

---

## DECISION 18: Docker Deployment

**Decision:** Docker Compose for development. Unchanged from V1 but updated for PostgreSQL.

**Services:**
```yaml
services:
  core:
    build: .
    ports: ["8080:8080"]
    depends_on: [postgres]
  postgres:
    image: postgres:15
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
```

**Environment variables:**
- `CORE_DB_HOST`, `CORE_DB_PORT`, `CORE_DB_USER`, `CORE_DB_PASSWORD`, `CORE_DB_NAME`
- `CORE_PORT`, `CORE_LOG_LEVEL`, `CORE_BATCH_SIZE`, `CORE_WORKER_TYPE`
- `CORE_HEARTBEAT_URL`, `CORE_EDGE_URL`

---

## DECISION 19: Testing Strategy

**Decision:** TDD with 90%+ coverage. Unchanged from V1.

**Additions for V2:**
- Use `pytest-asyncio` for async tests
- Use `testcontainers-python` for PostgreSQL integration tests (real database, not mocks)
- Fixtures create/teardown schemas per test
- No mocking of database layer — real PostgreSQL in Docker

---

## DECISION 20: Porto Bello — Architecture Ready, Deferred

**Decision:** Architecture present, implementation deferred. Unchanged from V1.

**When enabled:** Invoice signed but NOT transmitted. Waits for customer details to complete. When complete, queues TRANSMIT to Edge.

---

## DECISION HIERARCHY (Unchanged from V1)

1. **DECISIONS_V2.md** (this file) — highest priority
2. **Canonical Schema SQL files** — field definitions are gospel
3. **WS-specific DECISIONS.md** — workstream-level decisions
4. **API_CONTRACTS.md** — endpoint specifications
5. **Your judgment** — only for implementation details not covered above

---

**Last Updated:** 2026-03-18
**Version:** 2.0 — BINDING
