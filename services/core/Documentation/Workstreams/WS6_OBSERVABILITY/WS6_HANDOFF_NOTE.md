# WS6 (OBSERVABILITY) — Handoff Note

**Date:** 2026-03-25
**From:** Architecture session (Bob + Opus)
**To:** WS6 Implementation Team (Sonnet 4.5 recommended)
**Prereqs:** WS0 COMPLETE, WS4 COMPLETE, WS5 COMPLETE
**Scope:** Audit logging (comprehensive — retrofit into WS1-WS5), Prometheus metrics, notifications

> **REMOVED from WS6:**
> - **RBAC** — Moved to dedicated cross-service RBAC team. RBAC is a gateway concern (WS0-level), not observability. See `RBAC_CROSS_SERVICE_NOTE.md`.
> - **Fixed PDF (FIRS IRN+QR stamping)** — Moved to WS5 supplementary task. It's a business deliverable, not observability. See `WS5_SUPPLEMENTARY_FIXED_PDF.md`.

---

## WHAT WS6 DOES

WS6 is the cross-cutting observability layer. It provides 3 subsystems:

```
┌─────────────────────────────────────────────────────┐
│  WS6: OBSERVABILITY                                  │
│                                                      │
│  1. AUDIT LOGGER — who did what when (ALL phases)    │
│  2. PROMETHEUS METRICS — /metrics endpoint            │
│  3. NOTIFICATIONS — alerts, approvals, system events  │
└─────────────────────────────────────────────────────┘
```

**WS6 has NO business logic.** It observes and records what other workstreams do. It does NOT generate .hlx files, process invoices, or manage entities. It watches and logs.

**CRITICAL: Audit logging is COMPREHENSIVE.** You are not just logging WS4 mutations. You are retrofitting audit calls into WS1, WS2, WS3, WS4, and WS5 code. The entire pipeline must be auditable end-to-end.

---

## MANDATORY READING — READ ALL OF THESE BEFORE ASKING ANY QUESTIONS

You are retrofitting audit hooks into 5 existing workstreams. You MUST understand every file you will touch. Read these in order.

### Architecture & Protocol
1. `Core/Documentation/CORE_CLAUDE.md` — **Master protocol. Rule #0: ALWAYS RECOMMEND WHEN ASKING. Read this FIRST.**
2. `Core/Documentation/DECISIONS_V2.md` — 25 binding architectural decisions
3. `Core/Documentation/VALIDATION_CHECKS.md` — 73 validation check IDs (audit events reference these)
4. `Core/Documentation/HLX_FORMAT.md` — .hlx envelope format (WS3 generates these, you audit that generation)
5. `Core/Documentation/HLM_FORMAT.md` — .hlm data format (understand what's being transformed)

### WS0 Foundation (you import from these)
6. `Core/src/app.py` — App factory. You register your router + middleware here.
7. `Core/src/config.py` — CoreConfig. Add any WS6-specific env vars here.
8. `Core/src/errors.py` — 13 error codes. Your audit logger catches these. Read the full hierarchy.
9. `Core/src/database/pool.py` — `create_pool()`, `get_connection()`. You use these for audit_log INSERTs.
10. `Core/src/sse/manager.py` — `SSEConnectionManager.publish()`. Notifications push through this.
11. `Core/src/database/schemas/core.sql` — Existing tables. You add `audit_log` here.

### WS1 Ingestion (you add 8 audit hooks here)
12. `Core/src/ingestion/router.py` — POST /enqueue endpoint. Add `file.received`, `queue.enqueued` events.
13. `Core/src/ingestion/queue_scanner.py` — Scanner tick. Add `queue.processing`, `queue.retry`, `queue.stale_recovered`.
14. `Core/src/ingestion/file_detector.py` — File type detection. Add `file.type_detected`.
15. `Core/src/ingestion/dedup.py` — SHA256 checker. Add `file.dedup_checked`.
16. `Core/src/ingestion/parsers/` — All parsers. Add `file.parsed` after each parser returns.

### WS2 Processing (you add 12 audit hooks here)
17. `Core/src/processing/transformer.py` — Phase 3. Add `transform.started/completed/failed`, `transform.script_loaded`.
18. `Core/src/processing/enricher.py` — Phase 4. Add `enrich.started/completed`, `enrich.his_called`.
19. `Core/src/processing/resolver.py` — Phase 5. Add `resolve.started/completed`, `resolve.customer_matched`.
20. `Core/src/processing/circuit_breaker.py` — Fault tolerance. Add `circuit_breaker.opened/closed`.
21. `Core/src/processing/his_client.py` — HIS stub. Understand what enrichment calls look like.
22. `Core/src/processing/models.py` — PipelineContext, TransformResult, etc. Understand the data flow.

### WS3 Orchestrator (you add 10 audit hooks here — IN THE WORKTREE)
**NOTE:** WS3 code is in worktree `eager-almeida`, NOT in the main repo path.
23. `.claude/worktrees/eager-almeida/Helium/Services/Core/src/orchestrator/pipeline.py` — Main pipeline. Add `pipeline.started/phase_completed/completed/failed/hlm_detected`.
24. `.claude/worktrees/eager-almeida/Helium/Services/Core/src/orchestrator/preview_generator.py` — HLX generation. Add `hlx.generated/stored/encrypted`, `pipeline.branching`.
25. `.claude/worktrees/eager-almeida/Helium/Services/Core/src/orchestrator/worker_manager.py` — Worker pool. Add `pipeline.timeout`.
26. `.claude/worktrees/eager-almeida/Helium/Services/Core/src/orchestrator/router.py` — Endpoint registration.
27. `.claude/worktrees/eager-almeida/Helium/Services/Core/src/orchestrator/models.py` — Orchestrator models.

### WS4 Entity CRUD (you add 10 audit hooks here)
28. `Core/src/data/invoice_repository.py` — Invoice CRUD. Add `invoice.created/updated/deleted`.
29. `Core/src/data/customer_repository.py` — Customer CRUD. Add `customer.created/updated/deleted`.
30. `Core/src/data/inventory_repository.py` — Inventory CRUD. Add `inventory.created/updated/deleted`.
31. `Core/src/data/search_repository.py` — Cross-entity search. Add `search.executed`.
32. `Core/src/data/entity_router.py` — PUT/DELETE endpoints. Understand the request flow.

### WS5 Finalize (you add 8 audit hooks here)
33. `Core/src/finalize/pipeline.py` — Finalize flow. Add `finalize.started/irn_generated/qr_generated/edge_queued/db_committed/hlx_versioned/completed/failed`.
34. `Core/src/finalize/` — All finalize source files. Read to understand the complete flow.

### Existing Tests (understand test patterns)
35. `Core/tests/conftest.py` — Shared fixtures. Add audit-related fixtures here.
36. `Core/tests/` — Browse ALL test directories to understand patterns used by WS1-WS5.

---

## QUESTIONS PROTOCOL

When you encounter ambiguity or need a decision:
1. **ALWAYS recommend an answer and explain WHY**
2. Present your analysis, your pick, your reasoning
3. The user confirms or overrides
4. **Never ask a naked question** — see CORE_CLAUDE.md Rule #0

---

## WHAT'S ALREADY BUILT (your foundation)

```python
# WS0 — App, config, pool, SSE, scheduler, errors
from src.app import create_app
from src.config import CoreConfig
from src.errors import CoreError, PermissionDeniedError
from src.database.pool import create_pool, get_connection
from src.sse.manager import SSEConnectionManager
from src.health import health_router

# WS4 — Entity repositories (you'll audit mutations from these)
from src.data.invoice_repository import InvoiceRepository
from src.data.customer_repository import CustomerRepository
from src.data.inventory_repository import InventoryRepository

# WS5 — Finalize (you'll audit finalize events)
from src.finalize.pipeline import FinalizePipeline
```

---

## SUBSYSTEM 1: AUDIT LOGGER

### What It Does

Records every significant action in Core for compliance and traceability. Every CREATE, UPDATE, DELETE, FINALIZE, and TRANSMIT event is logged with who, what, when, and the before/after state.

### Audit Table

```sql
CREATE TABLE core.audit_log (
    audit_id        TEXT PRIMARY KEY,           -- UUIDv7
    event_type      TEXT NOT NULL,              -- 'invoice.created', 'customer.updated', 'finalize.completed', etc.
    entity_type     TEXT NOT NULL,              -- 'invoice', 'customer', 'inventory', 'queue', 'system'
    entity_id       TEXT,                       -- The ID of the affected entity
    action          TEXT NOT NULL,              -- 'CREATE', 'UPDATE', 'DELETE', 'FINALIZE', 'TRANSMIT'
    actor_id        TEXT,                       -- helium_user_id (or 'system' for automated actions)
    actor_type      TEXT DEFAULT 'user',        -- 'user', 'system', 'scheduler'
    company_id      TEXT NOT NULL,
    x_trace_id      TEXT,                       -- Correlation with Relay/HeartBeat
    before_state    JSONB,                      -- Snapshot before change (null for CREATE)
    after_state     JSONB,                      -- Snapshot after change (null for DELETE)
    changed_fields  TEXT[],                     -- Array of field names that changed
    metadata        JSONB,                      -- Extra context (IP, user-agent, etc.)
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_audit_entity ON core.audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_actor ON core.audit_log(actor_id);
CREATE INDEX idx_audit_company ON core.audit_log(company_id);
CREATE INDEX idx_audit_created ON core.audit_log(created_at DESC);
CREATE INDEX idx_audit_trace ON core.audit_log(x_trace_id);
```

### Audit Logger Class

```python
class AuditLogger:
    """
    Records audit events. Called by other workstreams after mutations.
    Fire-and-forget — audit failures MUST NOT block business operations.
    """

    def __init__(self, pool):
        self.pool = pool

    async def log(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        action: str,
        actor_id: str,
        company_id: str,
        before_state: dict | None = None,
        after_state: dict | None = None,
        changed_fields: list[str] | None = None,
        x_trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """
        Insert audit record. Catches all exceptions internally —
        audit failures are logged but NEVER propagated.
        """
        try:
            async with get_connection(self.pool, "core") as conn:
                await conn.execute(...)
        except Exception as e:
            logger.error("audit_write_failed", error=str(e), event_type=event_type)
            # DO NOT re-raise — audit must never block business operations

    async def log_batch(self, events: list[AuditEvent]) -> None:
        """Batch insert for bulk operations (finalize, bulk delete)."""
```

**CRITICAL:** Audit logger is fire-and-forget. If the audit INSERT fails (disk full, connection lost), log a warning and continue. Per DECISIONS_V2: "HeartBeat audit down → Continue processing, skip audit log."

### Audit Query Endpoint

```
GET /api/v1/audit
    ?entity_type=invoice
    ?entity_id=INV-001
    ?action=UPDATE
    ?actor_id=user_123
    ?date_from=2026-01-01
    ?date_to=2026-03-25
    ?limit=50
    ?offset=0
```

Response: Paginated list of audit entries. This feeds Float's Audit mApp → Logs tab.

### COMPREHENSIVE Event Types — ALL Workstreams

**You MUST retrofit audit calls into WS1-WS5 code.** This is your primary deliverable beyond the logger class itself.

#### WS1: INGESTION Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `file.received` | `ingestion/router.py` — after enqueue success | blob_uuid, filename, size_bytes, company_id |
| `file.parsed` | `ingestion/queue_scanner.py` — after parser returns | file_type, row_count, sheet_count, parse_duration_ms |
| `file.dedup_checked` | `ingestion/dedup.py` — after hash check | file_hash, is_duplicate, matched_queue_id (if dup) |
| `file.type_detected` | `ingestion/file_detector.py` — after detection | detected_type, is_hlm, content_type |
| `queue.enqueued` | `ingestion/router.py` — after DB insert | queue_id, data_uuid, priority |
| `queue.processing` | `ingestion/queue_scanner.py` — before processing | queue_id, attempt_number |
| `queue.retry` | `ingestion/queue_scanner.py` — on retry | queue_id, attempt_number, error_code |
| `queue.stale_recovered` | `ingestion/queue_scanner.py` — stale entry reset | queue_id, stuck_duration_seconds |

#### WS2: PROCESSING Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `transform.started` | `processing/transformer.py` — at method entry | company_id, script_id, is_hlm_passthrough |
| `transform.script_loaded` | `processing/transformer.py` — after script load | script_id, script_version, is_default |
| `transform.completed` | `processing/transformer.py` — after Transforma returns | invoice_count, customer_count, product_count, duration_ms |
| `transform.failed` | `processing/transformer.py` — on exception | error_code, error_message |
| `enrich.started` | `processing/enricher.py` — at method entry | product_count, his_endpoint |
| `enrich.his_called` | `processing/enricher.py` — per HIS batch | batch_size, avg_confidence |
| `enrich.completed` | `processing/enricher.py` — after enrichment | enriched_count, low_confidence_count, duration_ms |
| `resolve.started` | `processing/resolver.py` — at method entry | customer_count, product_count |
| `resolve.customer_matched` | `processing/resolver.py` — per match | match_type (tin_exact, rc_exact, fuzzy), confidence |
| `resolve.completed` | `processing/resolver.py` — after resolution | matched_customers, new_customers, matched_products, new_products |
| `circuit_breaker.opened` | `processing/circuit_breaker.py` — on open | service_name, failure_count |
| `circuit_breaker.closed` | `processing/circuit_breaker.py` — on recovery | service_name, recovery_time_seconds |

#### WS3: ORCHESTRATOR Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `pipeline.started` | `orchestrator/pipeline.py` — at entry | data_uuid, queue_id, company_id |
| `pipeline.phase_completed` | `orchestrator/pipeline.py` — after each phase | phase_name, phase_number, duration_ms |
| `pipeline.hlm_detected` | `orchestrator/pipeline.py` — on .hlm detection | skipping_transform=true |
| `pipeline.branching` | `orchestrator/preview_generator.py` — after branch | sheet_counts: {submission: N, failed: N, ...} |
| `hlx.generated` | `orchestrator/preview_generator.py` — after pack | hlx_id, version, sheet_count, size_bytes |
| `hlx.stored` | `orchestrator/preview_generator.py` — after blob write | blob_uuid, hlx_id |
| `hlx.encrypted` | `orchestrator/preview_generator.py` — after encrypt | hlx_id, encryption_method |
| `pipeline.completed` | `orchestrator/pipeline.py` — at success | total_duration_ms, invoice_count |
| `pipeline.failed` | `orchestrator/pipeline.py` — at error | error_code, error_message, phase_at_failure |
| `pipeline.timeout` | `orchestrator/worker_manager.py` — on timeout | queue_id, elapsed_ms, timeout_ms |

#### WS4: ENTITY CRUD Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `invoice.created` | `data/invoice_repository.py` — after INSERT | invoice_id, irn, total_amount |
| `invoice.updated` | `data/invoice_repository.py` — after UPDATE | invoice_id, changed_fields, before_state, after_state |
| `invoice.deleted` | `data/invoice_repository.py` — after soft DELETE | invoice_id, deleted_by |
| `customer.created` | `data/customer_repository.py` — after INSERT | customer_id, tin, company_name |
| `customer.updated` | `data/customer_repository.py` — after UPDATE | customer_id, changed_fields |
| `customer.deleted` | `data/customer_repository.py` — after soft DELETE | customer_id, deleted_by |
| `inventory.created` | `data/inventory_repository.py` — after INSERT | product_id, product_name, hsn_code |
| `inventory.updated` | `data/inventory_repository.py` — after UPDATE | product_id, changed_fields |
| `inventory.deleted` | `data/inventory_repository.py` — after soft DELETE | product_id, deleted_by |
| `search.executed` | `data/search_repository.py` — after search | query, result_count, duration_ms |

#### WS5: FINALIZE Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `finalize.started` | `finalize/pipeline.py` — at entry | queue_id, data_uuid, invoice_count |
| `finalize.irn_generated` | `finalize/pipeline.py` — per invoice | invoice_id, irn |
| `finalize.qr_generated` | `finalize/pipeline.py` — per invoice | invoice_id, qr_size_bytes |
| `finalize.edge_queued` | `finalize/pipeline.py` — after Edge queue | invoice_count, edge_queue_id |
| `finalize.db_committed` | `finalize/pipeline.py` — after DB writes | invoice_count, customer_count, product_count |
| `finalize.hlx_versioned` | `finalize/pipeline.py` — after re-finalize | hlx_id, new_version, change_reason |
| `finalize.completed` | `finalize/pipeline.py` — at success | submitted_count, duration_ms |
| `finalize.failed` | `finalize/pipeline.py` — at error | error_code, failed_at_step |

#### System Events

| Event Type | Where to Add | Metadata |
|---|---|---|
| `system.config_changed` | Config update handler | key, old_value, new_value |
| `system.script_updated` | Transformer script cache | company_id, script_id, old_hash, new_hash |
| `system.startup` | `app.py` lifespan — at startup | core_version, schema_version |
| `system.shutdown` | `app.py` lifespan — at shutdown | uptime_seconds |

---

## SUBSYSTEM 2: PROMETHEUS METRICS

### What It Does

Exposes a `/metrics` endpoint in Prometheus text format. Float's Admin mApp → Health tab reads this. External monitoring tools (Grafana, etc.) can scrape it.

### Endpoint

```
GET /metrics

Response: text/plain (Prometheus exposition format)
```

### Metrics to Expose

```python
from prometheus_client import Counter, Histogram, Gauge, Info

# Request metrics
http_requests_total = Counter(
    "core_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "core_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
)

# Pipeline metrics
pipeline_runs_total = Counter(
    "core_pipeline_runs_total",
    "Total pipeline executions",
    ["status"],  # success, failed, timeout
)

pipeline_duration_seconds = Histogram(
    "core_pipeline_duration_seconds",
    "Pipeline execution duration",
    ["phase"],  # parse, transform, enrich, resolve, branch, preview
)

invoices_processed_total = Counter(
    "core_invoices_processed_total",
    "Total invoices processed",
    ["direction", "transaction_type", "status"],
)

# Queue metrics
queue_depth = Gauge(
    "core_queue_depth",
    "Current queue depth",
    ["status"],  # PENDING, PROCESSING
)

queue_processing_duration_seconds = Histogram(
    "core_queue_processing_duration_seconds",
    "Time from enqueue to completion",
)

# External service metrics
external_service_requests_total = Counter(
    "core_external_service_requests_total",
    "Requests to external services",
    ["service", "status"],  # service: heartbeat, his, edge
)

external_service_duration_seconds = Histogram(
    "core_external_service_duration_seconds",
    "External service call duration",
    ["service"],
)

circuit_breaker_state = Gauge(
    "core_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["service"],
)

# SSE metrics
sse_connections_active = Gauge(
    "core_sse_connections_active",
    "Active SSE connections",
)

# Entity metrics (gauges updated periodically from views)
entity_count = Gauge(
    "core_entity_count",
    "Total entity count",
    ["entity_type"],  # invoice, customer, inventory
)

# System
core_info = Info("core", "Core service metadata")
core_info.info({"version": "1.0.0", "schema_version": "2.1.1.0"})
```

### Middleware Integration

Add Prometheus middleware to FastAPI app:

```python
class PrometheusMiddleware:
    """Record request count and duration for every HTTP request."""

    async def __call__(self, request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        http_requests_total.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code,
        ).inc()

        http_request_duration_seconds.labels(
            method=request.method,
            endpoint=request.url.path,
        ).observe(duration)

        return response
```

### Periodic Gauge Updates

Queue depth and entity counts need a background job (every 30s):

```python
class MetricsCollector:
    """Background job: update Prometheus gauges from DB."""
    interval = 30  # seconds

    async def tick(self):
        # Queue depth
        pending = await count_queue_by_status("PENDING")
        processing = await count_queue_by_status("PROCESSING")
        queue_depth.labels(status="PENDING").set(pending)
        queue_depth.labels(status="PROCESSING").set(processing)

        # Entity counts
        inv_count = await count_invoices()
        cust_count = await count_customers()
        inv_count_gauge = entity_count.labels(entity_type="invoice").set(inv_count)
        # etc.
```

---

## SUBSYSTEM 3: NOTIFICATIONS

### What It Does

Core generates notifications for system events, business events, and approval requests. Notifications are stored in a `notifications` schema and pushed to Float via SSE.

### Notification Tables

```sql
CREATE SCHEMA IF NOT EXISTS notifications;

CREATE TABLE notifications.notifications (
    notification_id     TEXT PRIMARY KEY,        -- UUIDv7
    company_id          TEXT NOT NULL,
    recipient_id        TEXT,                    -- helium_user_id (null = broadcast to tenancy)
    notification_type   TEXT NOT NULL,           -- 'system', 'business', 'approval', 'report'
    category            TEXT NOT NULL,           -- 'upload_complete', 'finalize_complete', 'error', 'report_ready', 'approval_needed'
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    priority            TEXT DEFAULT 'normal',   -- 'low', 'normal', 'high', 'urgent'
    data                JSONB,                   -- Arbitrary payload (entity_id, report_id, etc.)
    created_at          TIMESTAMPTZ DEFAULT now(),
    expires_at          TIMESTAMPTZ              -- Null = never expires
);

CREATE TABLE notifications.notification_reads (
    read_id             TEXT PRIMARY KEY,
    notification_id     TEXT NOT NULL REFERENCES notifications.notifications(notification_id),
    read_by             TEXT NOT NULL,           -- helium_user_id
    read_at             TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_notif_company ON notifications.notifications(company_id);
CREATE INDEX idx_notif_recipient ON notifications.notifications(recipient_id);
CREATE INDEX idx_notif_created ON notifications.notifications(created_at DESC);
CREATE INDEX idx_notif_type ON notifications.notifications(notification_type);
```

### Notification Service

```python
class NotificationService:
    """Create and deliver notifications."""

    def __init__(self, pool, sse_manager: SSEConnectionManager):
        self.pool = pool
        self.sse_manager = sse_manager

    async def send(
        self,
        company_id: str,
        notification_type: str,
        category: str,
        title: str,
        body: str,
        recipient_id: str | None = None,
        priority: str = "normal",
        data: dict | None = None,
    ) -> str:
        """
        1. Insert into notifications.notifications
        2. Push via SSE: event_type='notification.created'
        3. Return notification_id
        """

    async def mark_read(self, notification_id: str, user_id: str) -> None:
        """Insert into notification_reads."""

    async def list_for_user(
        self, company_id: str, user_id: str,
        unread_only: bool = False,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        """Paginated notification list for Float's Notifications tab."""
```

### Notification Endpoints

```
GET  /api/v1/notifications
     ?unread_only=true
     ?limit=50
     ?offset=0

POST /api/v1/notifications/{id}/read

GET  /api/v1/notifications/unread-count
```

### When Notifications Fire

| Event | Category | Title | Priority |
|---|---|---|---|
| Pipeline complete (success) | `upload_complete` | "Upload processed: {filename}" | normal |
| Pipeline complete (with failures) | `upload_complete` | "Upload processed with {n} failures" | high |
| Pipeline error | `error` | "Processing failed: {filename}" | urgent |
| Finalize complete | `finalize_complete` | "{n} invoices submitted to FIRS" | normal |
| Report ready | `report_ready` | "{report_type} report is ready" | low |
| Scheduled report | `report_ready` | "Daily/Weekly/Monthly report" | normal |
| Script updated | `system` | "Transformation script updated" | normal |

---

## WS6 FILE TREE

```
src/
├── observability/
│   ├── __init__.py
│   ├── router.py              # GET /audit, GET /notifications, POST /notifications/{id}/read, GET /metrics
│   ├── models.py              # AuditEvent, Notification, NotificationRead
│   ├── audit_logger.py        # AuditLogger (fire-and-forget)
│   ├── metrics.py             # Prometheus counters, histograms, gauges
│   ├── metrics_collector.py   # Background job: periodic gauge updates
│   ├── metrics_middleware.py   # PrometheusMiddleware for FastAPI
│   └── notification_service.py # NotificationService (create, read, list)

tests/
├── test_audit_logger.py       # Fire-and-forget, batch, query, WS1-WS5 event coverage
├── test_metrics.py            # Counter increments, histogram observations
├── test_metrics_collector.py  # Gauge updates from DB
├── test_notification_service.py # Create, read, list, SSE push
├── test_router.py             # All endpoint tests
```

---

## ENDPOINTS SUMMARY

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/audit` | Paginated audit log (feeds Float Audit mApp) |
| GET | `/api/v1/notifications` | User's notifications (feeds Float Notifications tab) |
| POST | `/api/v1/notifications/{id}/read` | Mark notification read |
| GET | `/api/v1/notifications/unread-count` | Unread badge count |
| GET | `/metrics` | Prometheus text format (no auth — scrape endpoint) |

**NOTE:** RBAC enforcement on these endpoints will be added by the cross-service RBAC team later. For now, no auth required.

---

## DATABASE CHANGES

### New Tables (WS6 creates these)

1. `core.audit_log` — Audit trail
2. `notifications.notifications` — All notification types
3. `notifications.notification_reads` — Read tracking

### Migration

Add to `src/database/schemas/`:
- `audit.sql` — audit_log table + indexes
- `notifications.sql` — notifications schema + tables + indexes

Register in WS0's schema migration runner.

---

## INTEGRATION POINTS

### With WS4 (Entity CRUD)

WS4's repository methods should call `AuditLogger.log()` after mutations. Since WS4 is already built, you need to **add audit hooks** to the existing repositories:

```python
# In invoice_repository.py (add after successful UPDATE):
await self.audit_logger.log(
    event_type="invoice.updated",
    entity_type="invoice",
    entity_id=invoice_id,
    action="UPDATE",
    actor_id=context.helium_user_id,
    company_id=context.company_id,
    before_state=before_snapshot,
    after_state=after_snapshot,
    changed_fields=changed_fields,
)
```

**Approach:** Create an `AuditableRepository` mixin or wrapper that WS4 repositories can use. Do NOT rewrite WS4's repositories — add the audit calls as a thin layer.

### With WS5 (Finalize)

WS5's finalize pipeline should:
- Log `finalize.started` at start
- Log `finalize.completed` / `finalize.failed` at end
- Send `finalize_complete` notification

### With WS3 (Orchestrator)

WS3's pipeline should:
- Log `queue.processing` when pipeline starts
- Log `queue.completed` / `queue.failed` when done
- Send `upload_complete` notification

### With SSE (WS0)

Notifications push via SSE:
```python
await sse_manager.publish("notification.created", notification_data, company_id=company_id)
```

---

## DELIVERABLES

| # | Deliverable | Priority |
|---|---|---|
| 1 | `audit_logger.py` — fire-and-forget audit logging | P0 |
| 2 | `audit.sql` — audit_log table + indexes | P0 |
| 3 | `notification_service.py` — create, read, list, SSE push | P0 |
| 4 | `notifications.sql` — notifications schema + tables | P0 |
| 5 | `router.py` — audit + notification + metrics endpoints | P0 |
| 6 | `metrics.py` — Prometheus counter/histogram/gauge definitions | P0 |
| 7 | `metrics_middleware.py` — HTTP request instrumentation | P0 |
| 8 | `metrics_collector.py` — background gauge updates (30s) | P0 |
| 9 | `models.py` — AuditEvent, Notification models | P0 |
| 10 | **Audit hooks in WS1** (ingestion — 8 event types) | P0 |
| 11 | **Audit hooks in WS2** (processing — 12 event types) | P0 |
| 12 | **Audit hooks in WS3** (orchestrator — 10 event types) | P0 |
| 13 | **Audit hooks in WS4** (entity CRUD — 10 event types) | P0 |
| 14 | **Audit hooks in WS5** (finalize — 8 event types) | P0 |
| 15 | All tests — 90%+ coverage | P0 |
| 16 | Register router + middleware in app.py | P0 |

---

## WHAT SUCCESS LOOKS LIKE

After WS6:

1. **Every action across the entire pipeline** is logged in `core.audit_log` — from file receipt (WS1) through parsing, transformation (WS2), orchestration (WS3), CRUD (WS4), to finalize (WS5)
2. Float's Audit mApp → Logs tab shows paginated audit entries with full trace correlation
3. Float's Notifications tab shows system, business, and report notifications
4. `/metrics` returns Prometheus text with request counts, pipeline durations, queue depth
5. All subsystems are fire-and-forget — observability failures never block business operations

---

## KEY CONSTRAINT: NEVER BLOCK BUSINESS OPERATIONS

This is the most important design rule for WS6:

- Audit INSERT fails → log warning, continue
- Notification INSERT fails → log warning, continue
- Prometheus metric increment fails → ignore, continue

**Observability is a safety net, not a gate.** Nothing in WS6 should be able to prevent an invoice from being processed, finalized, or transmitted.

---

**Build order: audit logger + WS1-WS5 hooks first (highest value — makes the entire pipeline traceable). Then notifications. Then Prometheus last.**
