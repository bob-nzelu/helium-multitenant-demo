# CORE SERVICE - ARCHITECTURAL DECISIONS

**Version:** 1.0
**Last Updated:** 2026-01-31
**Status:** BINDING - All Claude variants must follow these decisions

---

## OVERVIEW

This document records all core architectural decisions made for the Helium Core Service. These decisions are **binding** - no Claude variant may override them without explicit user approval.

---

## DECISION: Database Technology

**Decision:** Use SQLite for all tiers (Test/Standard/Pro/Enterprise)

**Option Selected:** SQLite with environment-specific configuration

**Details:**
- **Test/Standard**: SQLite file-based (single process, threading)
- **Pro/Enterprise**: SQLite with WAL mode + distributed architecture support (via Celery workers)

**Rationale:**
- SQLite is sufficient for invoice processing loads (Test: 5K/day, Standard: 50K/day)
- Simplifies deployment (no external database dependencies)
- Single file makes backup/migration trivial
- WAL mode enables decent concurrency
- PostgreSQL migration possible later if needed

**Trade-offs:**
- ❌ Not designed for 1M+ invoices/day (would need PostgreSQL)
- ❌ Limited concurrent writes (single writer at a time)
- ✅ Much simpler deployment + operational complexity

**Reversibility:** Medium - Would need schema migration to PostgreSQL, but application logic stays same

**Decision Hierarchy:** This decision APPLIES TO:
- OPUS Infrastructure phase (database setup)
- All phases (all depend on this database choice)

---

## DECISION: Worker Deployment Model

**Decision:** Tier-specific worker deployment

**Option Selected:** Abstracted worker interface with environment-specific backends

**Details:**

### Test/Standard Environments
- In-process Python threading (ThreadPoolExecutor)
- Shared SQLite database
- Single Core process with multiple worker threads
- Configuration: Max 5-10 concurrent workers

### Pro/Enterprise Environments
- Celery + RabbitMQ/Redis
- Separate worker processes (horizontal scaling)
- Distributed task queue
- Configuration: 10-50 concurrent workers across multiple machines

**Rationale:**
- Abstraction allows same codebase to run on all tiers
- Threading sufficient for Test/Standard
- Celery provides horizontal scaling for Pro/Enterprise without application changes

**Trade-offs:**
- ❌ More complex abstraction layer (but worth it for unified codebase)
- ✅ Single codebase for all tiers
- ✅ Easy to scale up without rewriting

**Reversibility:** High - Worker interface is abstracted

**Implementation:** Workers must inherit from `BaseWorker` abstract class
```python
class BaseWorker:
    def run(self, task_data): pass
    def get_status(self): pass
    def cancel(self): pass
```

---

## DECISION: Transformation Script Storage & Execution

**Decision:** Store transformation scripts as Python code in config.db

**Option Selected:** Dynamic import + exec() in isolated namespace

**Details:**
- Scripts stored in `config.db` table `transformation_scripts`
- Loaded at runtime and executed dynamically
- Scripts can be modular (extract_module, validate_module, enrich_module, finalize_module)
- Validation: Scripts validated before storage (no arbitrary code)

**Rationale:**
- Allows per-customer transformation logic without database changes
- Scripts version-controlled in config.db
- Easy to enable/disable customer scripts without restarts

**Trade-offs:**
- ❌ Dynamic code execution (security risk if scripts not validated)
- ✅ Highly flexible per-customer customization
- ✅ No need for separate script files on disk

**Security Measure:** Scripts must be validated before storage
```python
def validate_transformation_script(script_code: str) -> bool:
    # Parse script as AST to detect dangerous operations
    # Block: os.system, subprocess, eval, exec, open, etc.
    # Allow: Only transformation logic
```

**Reversibility:** Medium - Could move to file-based scripts if needed

---

## DECISION: Error Handling & Graceful Degradation

**Decision:** Graceful degradation for Prodeus API failures

**Option Selected:** Continue without enrichment if API unavailable, flag as MANUAL

**Details:**
- **Parsing Errors** → Mark queue entry failed, skip invoice
- **Transformation Errors** → Mark queue entry failed, skip invoice
- **Enrichment Errors** → Continue without enrichment, flag fields as MANUAL (user must fill)
- **Database Errors** → Retry with exponential backoff (max 3 times)
- **Edge API Errors** → Keep in queue, auto-retry later

**Rationale:**
- Partial success is better than complete failure
- Prodeus APIs are external, subject to outages (graceful degradation expected)
- User can fill in missing data manually in Float UI

**Trade-offs:**
- ❌ Some invoices may be incomplete (Prodeus unavailable)
- ✅ System keeps processing even with external API failures
- ✅ Data is not lost, just marked as MANUAL

**Circuit Breaker:** If Prodeus API fails 5 times in a row, open circuit for 60s

---

## DECISION: Preview Mode & Finalization

**Decision:** Two-stage processing for bulk uploads

**Option Selected:** Preview → User edits → Finalization

**Details:**
- **Preview Stage** (immediate_processing=false):
  - Generate preview data (firs_invoices.json, report.json, etc.)
  - Append to blob (7-day retention)
  - Return preview URLs to Relay
  - DO NOT create database records yet
  - Mark core_queue as "preview_ready"

- **Finalization Stage** (user confirms + edits applied):
  - Apply user edits to preview data
  - Re-validate edited data
  - Generate IRN and QR code
  - Create database records
  - Queue to Edge
  - Broadcast WebSocket events

**Rationale:**
- Allows user to review and correct errors before committing
- Reduces duplicate/rejected invoices
- User edits applied before FIRS submission

**Trade-offs:**
- ❌ More complex flow (two stages)
- ✅ Better user experience and data quality
- ✅ Reduced FIRS rejections

**Cleanup:** Preview data expires after 24 hours (cleanup job runs every 6 hours)

---

## DECISION: IRN Generation Algorithm

**Decision:** Core generates IRN (not FIRS)

**Option Selected:** Deterministic hash-based IRN generation

**Details:**
- IRN format: `[timestamp]-[hash(invoice_data)]-[sequence]`
- Example: `2026013110000-5a3b9f2c-001`
- Hash: SHA256 of invoice data (supplier TIN + customer TIN + amount + date)
- Sequence: Counter per day (resets at midnight)
- Uniqueness: Guaranteed within same day, very unlikely across days

**Rationale:**
- Deterministic (same data = same IRN)
- Allows idempotent processing
- FIRS returns its own IRN (stored in `firs_irn` field)

**Storage:**
- `invoices.irn` = Core-generated IRN (unique, indexed)
- `invoices.firs_irn` = FIRS-returned IRN (from Edge, may be null if not yet signed)

---

## DECISION: QR Code Generation

**Decision:** Core generates QR code, Edge does not

**Option Selected:** Generate QR code in Phase 8 (Finalize), encode as Base64

**Details:**
- QR code contains: [IRN + invoice_number + amount + date]
- Format: PNG image, 200x200 pixels
- Encoding: Base64 string, stored in `invoices.qr_code_base64`
- Used by: Float UI to display QR code to user

**Rationale:**
- QR code is static (doesn't change)
- Edge doesn't need it, Float UI does
- Generate once, store once

---

## DECISION: WebSocket Architecture

**Decision:** Database triggers auto-broadcast events

**Option Selected:** SQL triggers call custom broadcast function

**Details:**
```sql
CREATE TRIGGER invoice_created_trigger
AFTER INSERT ON invoices
BEGIN
    SELECT broadcast_event('invoice.created', NEW.invoice_id, json_object(...));
END;
```

- Triggers fire on INSERT/UPDATE/DELETE
- Triggers call `broadcast_event()` function
- `broadcast_event()` function queues event to WebSocket server
- WebSocket server broadcasts to all connected Float SDK clients

**Rationale:**
- Automatic (no manual event emission required)
- Guaranteed (happens whenever data changes)
- Decoupled (application code doesn't know about WebSocket)

**Trade-offs:**
- ❌ Triggers add slight write overhead
- ✅ Guaranteed event delivery (no missed events)
- ✅ Clean separation of concerns

---

## DECISION: Porto Bello Workflow (Future, Architecture Ready)

**Decision:** Architecture ready, implementation deferred

**Option Selected:** Ready to implement, not in MVP scope

**Details:**
- **When enabled** (per customer config):
  - Invoice signed (generates FIRS IRN)
  - Invoice NOT yet transmitted
  - System waits for customer details to complete
  - When customer details complete, automatically transmit

- **Implementation** (deferred):
  - PortoBelloWorker monitors customer_details_complete events
  - When triggered, queues TRANSMIT task to Edge
  - Edge executes /exchange endpoint (skip /sign)

- **Status**: Architecture present, logic deferred to Phase 6

**Rationale:**
- Complex feature, not in MVP
- Can be implemented later without architecture changes
- Foundation laid in Phase 6 (PORTO BELLO business logic gate)

**Reversibility:** High - Can implement or skip without affecting other phases

---

## DECISION: Access Control Model

**Decision:** Role-Based Access Control (RBAC) via permissions table

**Option Selected:** user_permissions table with granular permissions

**Details:**
- Permissions stored in `user_permissions` table
- Each user has set of permissions (invoice.create, invoice.update, etc.)
- Admin permission (system.admin) grants all access
- Permission check before each CRUD operation

**Permission List:**
```
Invoice Permissions:
- invoice.create
- invoice.update
- invoice.update_status
- invoice.mark_as_paid
- invoice.delete
- invoice.accept_inbound (B2B)

Customer Permissions:
- customer.create
- customer.update
- customer.delete

Inventory Permissions:
- inventory.create
- inventory.update
- inventory.delete

System Permissions:
- system.admin (full access)
- system.view_audit_logs
```

**Implementation:**
```python
def has_permission(user_id: str, permission: str) -> bool:
    if has_admin(user_id):
        return True
    return db.query("SELECT 1 FROM user_permissions WHERE user_id=? AND permission=?")
```

---

## DECISION: Retry Strategy for Failed Submissions

**Decision:** Distinguish between /retry and /retransmit endpoints

**Option Selected:** Two separate endpoints with different task types

**Details:**

### POST /api/v1/retry
- Scenario: Invoice in ERROR state (both /sign and /exchange failed)
- Task to Edge: SIGN_AND_TRANSMIT
- Edge executes: /sign endpoint → /exchange endpoint
- Use case: Complete failure recovery

### POST /api/v1/retransmit
- Scenario: Invoice SIGNED (has FIRS IRN) but NOT transmitted (Porto Bello case)
- Task to Edge: TRANSMIT only
- Edge executes: /exchange endpoint ONLY (skip /sign)
- Use case: Counterparty details now complete, transmit already-signed invoice

**Rationale:**
- Different workflows require different Edge tasks
- Retransmit is more efficient (skip signing, just exchange)
- Both endpoints necessary for complete FIRS workflow

**Storage:**
- Both queue tasks to `edge_queue` table
- Task type determines Edge behavior

---

## DECISION: Idempotence Strategy

**Decision:** Use IRN as idempotence key

**Option Selected:** Check if invoice with same IRN exists before creating

**Details:**
```python
def create_invoice(invoice_data):
    # Check if already exists
    existing = db.query("SELECT invoice_id FROM invoices WHERE irn = ?", invoice_data["irn"])
    if existing:
        return existing["invoice_id"]  # Idempotent return

    # Create new
    db.execute("INSERT INTO invoices ...")
    return invoice_data["invoice_id"]
```

**Rationale:**
- IRN is deterministic (same data = same IRN)
- Prevents duplicate invoices from queue retries
- API is idempotent (same request = same result)

---

## DECISION: Caching Strategy

**Decision:** In-memory cache for frequently accessed data

**Option Selected:** Simple in-memory dict with TTL

**Details:**
- Cache: Customer master data (name, TIN, address)
- Cache: Inventory master data (SKU, product name, HSN)
- TTL: 1 hour (configurable)
- Invalidation: Auto-invalidate on UPDATE/DELETE triggers

**Rationale:**
- Reduces database queries during enrichment
- Simple implementation (no Redis needed for Test/Standard)
- Customer/inventory data changes infrequently

**Trade-offs:**
- ❌ Stale data if updates happen (but TTL limits staleness)
- ✅ Significant performance improvement
- ✅ Works without external dependencies

---

## DECISION: Batch Processing Size

**Decision:** Process invoices in batches of 100 per task

**Option Selected:** 100 invoices per Celery/thread task

**Details:**
- Bulk uploads split into batches of 100
- Each batch processed by separate worker
- Parallel execution: 10 workers × 100 invoices = 1000 invoices in parallel
- Performance: 30K invoices in ~1 minute (vs ~10 minutes single-threaded)

**Rationale:**
- Balance between parallelization and overhead
- 100 invoices = ~1-5 seconds per batch
- Granular enough for good parallelization
- Not too small (overhead of task creation)

**Configurable:** Via `BATCH_SIZE` config parameter

---

## DECISION: Data Retention & Cleanup

**Decision:** Delayed deletion pattern for audit trail

**Option Selected:** Mark as processed, cleanup after 24 hours

**Details:**
- **core_queue entries:**
  - Update status to "processed"
  - DO NOT delete immediately
  - HeartBeat cleanup job deletes after 24 hours
  - Keeps audit trail for reconciliation

- **Preview data (blob appendages):**
  - Expire after 24 hours
  - Cleanup job runs every 6 hours
  - User must finalize within 24 hours or re-upload

- **Failed entries:**
  - Retained for 30 days for debugging
  - Cleanup job deletes after 30 days

**Rationale:**
- HeartBeat needs 24-hour window to reconcile
- Provides recovery window if Core crashes
- Minimal storage cost (queue entries are tiny)

---

## DECISION: Monitoring & Metrics

**Decision:** Prometheus metrics for all workers and operations

**Option Selected:** Standard Prometheus metrics

**Metrics:**
- `helium_core_files_processed_total{status="success"}` - Files processed
- `helium_core_processing_time_seconds{quantile="0.95"}` - P95 latency
- `helium_core_queue_depth{status="pending"}` - Queue depth
- `helium_core_workers_active{worker_type="..."}` - Active workers
- `helium_core_invoices_total` - Total invoices in database
- `helium_core_errors_total{error_type="..."}` - Error counts

**Rationale:**
- Observability into Core's health and performance
- Detect queue backlog, slow processing, high error rates
- Integration with monitoring systems (Grafana, AlertManager)

---

## DECISION: Logging Level & Format

**Decision:** Structured logging with JSON output

**Option Selected:** JSON logs for machine parsing

**Details:**
```json
{
  "timestamp": "2026-01-31T10:00:00Z",
  "level": "INFO",
  "service": "core",
  "worker": "transformation-worker-3",
  "event_type": "transformation.completed",
  "user_id": "user_123",
  "queue_id": "queue_123",
  "invoices_processed": 995,
  "processing_time_seconds": 45.2
}
```

**Rationale:**
- Machine-parseable (easier for log aggregation)
- Rich context (worker, user, timing, result)
- Searchable (can filter by level, worker, event_type, etc.)

**Log Levels:**
- DEBUG: Detailed diagnostic info (rare in production)
- INFO: Major milestones (file started, phase completed)
- WARNING: Recoverable issues (Prodeus API degraded)
- ERROR: Failed operations (invoice processing failed)

---

## DECISION: Environment Configuration

**Decision:** Environment-specific config files

**Option Selected:** Config files per tier (test.json, standard.json, pro.json, enterprise.json)

**Details:**
```
Core/config/
├─ test.json          (SQLite, threading, 100 invoices/second)
├─ standard.json      (SQLite WAL, threading, 1000 invoices/second)
├─ pro.json           (SQLite, Celery, 5000 invoices/second)
└─ enterprise.json    (SQLite, Celery distributed, unlimited)
```

**Rationale:**
- Different worker counts per tier
- Different database settings per tier
- Easy to switch environments via config

**Reversibility:** High - Config files can be changed without code changes

---

## DECISION: Integration with Relay & Edge

**Decision:** Parallel Development (defined contracts, independent implementation)

**Option Selected:** API contracts defined, services developed independently

**Details:**
- **Relay → Core:** Defined contract for POST /api/v1/process request/response
- **Core → Edge:** Defined contract for edge_queue table and Edge API calls
- **Edge → Core:** Defined contract for POST /api/v1/update responses
- Each service implements independently using contracts
- Integration testing happens after all services ready

**Rationale:**
- Allows parallel development of 3 services
- Clear boundaries prevent dependencies
- Contract-first approach (define before implement)

**Contracts Location:**
- Core/Documentation/PHASE_1_FETCH/API_CONTRACTS.md (Relay → Core interface)
- Core/Documentation/PHASE_8_FINALIZE/API_CONTRACTS.md (Core → Edge, Core ← Edge)

---

## DECISION: Testing Strategy

**Decision:** Test-Driven Development (TDD) for Core

**Option Selected:** Write tests BEFORE implementation

**Details:**
- Unit tests: Test individual functions/classes
- Integration tests: Test between phases
- Error handling tests: Test all error codes
- Edge case tests: Boundary conditions, empty inputs
- Performance tests: Verify latency/throughput targets
- Coverage target: 90%+ per phase (measured, not estimated)

**Rationale:**
- Catches bugs early
- Forces clear API design (tests write against API)
- Provides executable documentation
- Safer refactoring later

**Tools:** pytest + pytest-cov for measurement

---

## DECISION: Deployment & Scaling

**Decision:** Docker-first deployment for all tiers

**Option Selected:** Dockerfile + docker-compose for all environments

**Details:**
- All tiers run in Docker containers
- docker-compose for local development (Test tier)
- Kubernetes manifests for Pro/Enterprise (future)
- Environment variables for config (tier, log level, etc.)

**Rationale:**
- Consistent deployment across all tiers
- Easy to scale (add more container replicas)
- Works with standard DevOps tooling

---

## DECISION: Backward Compatibility

**Decision:** Maintain backward compatibility for 1 major version

**Option Selected:** Support N and N-1 API versions

**Details:**
- Current version: 1.0
- Supported versions: 1.0 (current), 0.9 (previous)
- Deprecated endpoints kept for 1 full release cycle
- Breaking changes announced 1 release in advance

**Rationale:**
- Prevents breaking Float SDK in field
- Allows gradual upgrade path
- Standard practice for backend services

---

## DECISION: Documentation & Specifications

**Decision:** Specification-first development

**Option Selected:** API contracts + architecture docs written FIRST, then implementation

**Details:**
- CORE_CLAUDE.md (binding protocol)
- DECISIONS.md (this file)
- CORE_ARCHITECTURE.md (overall architecture)
- PHASE_X_DECISIONS.md (phase-specific)
- API_CONTRACTS.md (endpoint specs)
- Then: Implementation follows specs exactly

**Rationale:**
- Clear expectations before coding
- Specs catch design issues early
- No ambiguity about what to implement

---

## SUMMARY: All Decisions by Category

### Database & Persistence
- ✅ Database: SQLite (all tiers)
- ✅ IRN Generation: Deterministic hash-based
- ✅ Data Retention: 24-hour delayed deletion + 30-day failed retention
- ✅ Caching: In-memory with 1-hour TTL

### Processing & Workers
- ✅ Workers: Tier-specific (threading/Celery)
- ✅ Batch Size: 100 invoices per task
- ✅ Error Handling: Graceful degradation for Prodeus APIs
- ✅ Transformation Scripts: Stored in config.db, dynamically executed

### API & Integration
- ✅ Retry Strategy: /retry (full cycle) vs /retransmit (exchange only)
- ✅ Idempotence: IRN-based
- ✅ Integration: Parallel development with defined contracts
- ✅ Backward Compatibility: Support N-1 versions

### Features & Workflows
- ✅ Preview Mode: Two-stage processing (preview → finalize)
- ✅ Porto Bello: Architecture ready, implementation deferred
- ✅ B2B Invoices: Accept/reject endpoints included
- ✅ WebSocket: Database triggers auto-broadcast

### Operations & Monitoring
- ✅ Access Control: RBAC via permissions table
- ✅ Logging: Structured JSON logs
- ✅ Metrics: Prometheus for all operations
- ✅ Monitoring: Metrics + logs + health check

### Quality & Deployment
- ✅ Testing: TDD approach, 90%+ coverage mandatory
- ✅ Deployment: Docker-first for all tiers
- ✅ Documentation: Spec-first development
- ✅ Scaling: Horizontal scaling via Celery workers

---

## When to Revisit These Decisions

Revisit if:
- ❌ A decision is blocking implementation (call user, don't override)
- ❌ A decision conflicts with new requirement (call user, get approval for change)
- ✅ Implementation reveals flaw in decision (document & propose change to user)

**Process for Changing a Decision:**
1. Document the issue clearly
2. Propose 2-3 alternatives
3. Get user approval
4. Update DECISIONS.md with new decision + rationale
5. Implement new approach

---

**Last Updated:** 2026-01-31
**Version:** 1.0 - BINDING
**Status:** READY FOR PHASE-SPECIFIC IMPLEMENTATION
