# CORE SERVICE - INFRASTRUCTURE OVERVIEW

**Version:** 1.0
**Date:** 2026-02-05
**Status:** CANONICAL SPECIFICATION
**Phase:** Phase 0a - Infrastructure Documentation

---

## DOCUMENT PURPOSE

This document captures the COMPLETE infrastructure architecture for Helium Core Service, synthesizing all architectural decisions, system components, data flows, and implementation strategies discussed during Phase 0a planning.

**Target Audience:** Infrastructure implementers (Phase 0b-0f), system architects, DevOps engineers

**Scope:** Core Service Infrastructure only (Phases 0a-0f)

---

## TABLE OF CONTENTS

1. [Architecture Principles](#architecture-principles)
2. [System Components](#system-components)
3. [Queue Architecture](#queue-architecture)
4. [Database Architecture](#database-architecture)
5. [Access Pattern Architecture](#access-pattern-architecture)
6. [Processing Pipeline Overview](#processing-pipeline-overview)
7. [Phase Breakdown](#phase-breakdown)
8. [Test vs Production Isolation](#test-vs-production-isolation)
9. [Audit Logging Strategy](#audit-logging-strategy)
10. [Performance Considerations](#performance-considerations)
11. [Deployment Architecture](#deployment-architecture)
12. [Implementation Roadmap](#implementation-roadmap)

---

## ARCHITECTURE PRINCIPLES

### Principle 1: Universal Access Pattern
**All resources (databases, queues, blob storage) accessible through unified interface:**
- External consumers: ALWAYS via service API
- Internal workers: Auto-detect (direct if local, API if remote)
- Same abstraction across all resource types

### Principle 2: RabbitMQ for Speed, audit.db for Durability
**Dual-system approach:**
- RabbitMQ: Fast message passing (1-5ms latency)
- audit.db: Permanent audit trail (HeartBeat reconciliation)
- NO intermediate tracking tables (simplified architecture)

### Principle 3: Resolution Before Write
**All data cleaning happens in-memory before database commit:**
- Phase 5 (RESOLVE): Deduplication, entity matching (in-memory)
- Phase 8 (FINALIZE): Clean data written to databases
- Idempotent operations (IRN prevents duplicates)

### Principle 4: Test Isolation with Shared Master Data
**Hybrid isolation strategy:**
- invoices: Separate databases (invoices_test.db SQLite, invoices.db Postgres)
- customers/inventory: Shared Postgres (test + production, environment flag)
- Rationale: Master data is company-wide, invoices are environment-specific

### Principle 5: Same Codebase, All Tiers
**Configuration-driven deployment:**
- Test/Standard/Pro/Enterprise: Same code
- Different configs: Database hosts, worker counts, API endpoints
- Auto-detect connection type (direct vs API)

---

## SYSTEM COMPONENTS

### Core Manages Four Databases

```
Core Databases:
├─ invoices_test.db (SQLite, local file, test environment only)
├─ invoices.db (PostgreSQL, remote, production only)
├─ customers.db (PostgreSQL, remote, shared test+prod)
└─ inventory.db (PostgreSQL, remote, shared test+prod)

External Databases (not owned by Core):
└─ audit.db (HeartBeat-owned, Core logs to it)
```

### Core Uses Two Message Queues

```
Message Queues:
├─ core_queue (RabbitMQ, owned by Core)
│  └─ Purpose: Relay → Core message passing
│
└─ edge_queue (RabbitMQ, owned by Edge)
   └─ Purpose: Core → Edge task handoff
```

### Core Exposes 18 API Endpoints

```
API Endpoints (FastAPI):
├─ POST /api/v1/process (main processing endpoint)
├─ POST /api/v1/retry (retry failed FIRS submissions)
├─ POST /api/v1/retransmit (transmit already-signed invoices)
├─ PUT /api/v1/entity/{type}/{id} (update invoice/customer/inventory)
├─ DELETE /api/v1/entity/{type}/{id} (delete invoice/customer/inventory)
├─ POST /api/v1/update (generic update from Edge/SDK)
├─ POST /api/v1/invoice/{id}/accept (accept B2B invoice)
├─ POST /api/v1/invoice/{id}/reject (reject B2B invoice)
├─ GET /api/v1/invoice/{id} (fetch single invoice)
├─ GET /api/v1/invoices (list invoices)
├─ GET /api/v1/customer/{id} (fetch single customer)
├─ GET /api/v1/customers (list customers)
├─ GET /api/v1/inventory/{id} (fetch single inventory)
├─ GET /api/v1/inventories (list inventory)
├─ POST /api/v1/search (full-text search)
├─ WS /api/v1/events (WebSocket for real-time sync)
├─ GET /api/v1/core_queue/status (for HeartBeat reconciliation)
└─ GET /api/v1/health (health check)
```

---

## QUEUE ARCHITECTURE

### Design Decision: RabbitMQ Only (No Tracking Tables)

**Architecture:**
```
Relay → RabbitMQ (core_queue) → Core Workers → audit.db
                                              ↓
                                         invoices.db
```

**Key Points:**
- ✅ RabbitMQ for fast message passing (10,000+ messages/sec)
- ✅ audit.db for audit trail (permanent log)
- ❌ NO core_processing_status table (eliminated for simplicity)
- ✅ HeartBeat queries audit.db for reconciliation (not Core API)

### Message Flow

**Step 1: Relay Enqueues**
```python
# Relay publishes to RabbitMQ
rabbitmq_client.publish(
    queue="core_queue",
    message={
        "queue_id": "queue_123",
        "blob_uuid": "550e8400-...",
        "blob_path": "/files_blob/550e8400-...-invoice.pdf",
        "environment": "production",
        "immediate_processing": false,
        "customer_id": "execujet-ng"
    }
)
# ✅ Published in 2ms

# Relay logs to audit.db (async, non-blocking)
audit_db.log({
    "event_type": "relay.file_queued",
    "queue_id": "queue_123",
    "blob_uuid": "550e8400-...",
    "timestamp": "2026-02-05T10:00:00.000Z"
})
```

**Step 2: Core Worker Consumes**
```python
# Core worker consumes from RabbitMQ (push-based)
message = rabbitmq_client.consume(queue="core_queue")
# ✅ Consumed in 1ms

# Log start to audit.db
audit_db.log({
    "event_type": "core.processing_started",
    "queue_id": message["queue_id"],
    "timestamp": NOW()
})

# Process invoice (Phases 1-8)
process_invoice(message)

# Log completion to audit.db
audit_db.log({
    "event_type": "core.processing_completed",
    "queue_id": message["queue_id"],
    "invoices_created": 995,
    "processing_time_seconds": 2.5
})

# ACK message (remove from RabbitMQ)
rabbitmq_client.ack(message)
```

**Step 3: HeartBeat Reconciliation**
```python
# HeartBeat queries audit.db (not Core API!)
stuck_entries = audit_db.query("""
    SELECT queue_id, blob_uuid, created_at
    FROM audit_log
    WHERE event_type = 'core.processing_started'
    AND created_at < NOW() - INTERVAL '1 hour'
    AND queue_id NOT IN (
        SELECT queue_id
        FROM audit_log
        WHERE event_type IN ('core.processing_completed', 'core.processing_failed')
    )
""")

# Alert on stuck entries
for entry in stuck_entries:
    alert_admin(f"Stuck entry: {entry['queue_id']} (started {entry['created_at']})")
```

### Performance Comparison

| Approach | Latency | Throughput | Queryable | Durable |
|----------|---------|------------|-----------|---------|
| **RabbitMQ only** | 1-5ms | 10K+ msg/sec | ❌ No | ⚠️ Needs persistence |
| **Postgres queue** | 50-100ms | 500 query/sec | ✅ Yes | ✅ Yes |
| **RabbitMQ + audit.db** | 1-5ms | 10K+ msg/sec | ✅ Yes (audit) | ✅ Yes |

**DECISION: RabbitMQ + audit.db (best of both worlds)** ✅

---

## DATABASE ARCHITECTURE

### Database Technology Decisions

**Production:**
- **invoices.db:** PostgreSQL (remote server, port 5432)
- **customers.db:** PostgreSQL (remote server, port 5433)
- **inventory.db:** PostgreSQL (remote server, port 5434)

**Test:**
- **invoices_test.db:** SQLite (local file, `./databases/invoices_test.db`)
- **customers.db:** PostgreSQL (SHARED with production, environment flag)
- **inventory.db:** PostgreSQL (SHARED with production, environment flag)

**Rationale:**
- SQLite for test invoices: Fast local development, complete isolation
- Postgres for production invoices: Better concurrency, scalability
- Shared customers/inventory: Master data is company-wide, not environment-specific

### Database Schemas (High-Level)

**invoices Table** (50+ fields)
```sql
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY,
    irn TEXT UNIQUE NOT NULL,  -- Core-generated IRN
    firs_irn TEXT UNIQUE,      -- FIRS-issued IRN
    invoice_number TEXT NOT NULL,
    customer_id TEXT REFERENCES customers(customer_id),
    environment TEXT CHECK(environment IN ('test', 'production')),
    firs_status TEXT CHECK(firs_status IN ('DRAFT', 'SIGNED', 'TRANSMITTED', 'VALIDATED', 'REJECTED', 'ERROR')),
    -- ... 40+ more fields
);
```

**customers Table** (B2B only, TIN/RC required)
```sql
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    party_name TEXT NOT NULL,
    canonical_name TEXT NOT NULL,  -- Best name (scoring algorithm)
    tin TEXT,                      -- Format: 12345678-001
    rc_number TEXT,                -- Format: RC123456
    environment TEXT,              -- 'test' or 'production' (shared DB)
    -- TIN or RC required (validation)
    CHECK (tin IS NOT NULL OR rc_number IS NOT NULL)
);
```

**customer_name_variants Table** (Multi-name tracking)
```sql
CREATE TABLE customer_name_variants (
    customer_id TEXT REFERENCES customers(customer_id),
    name_variant TEXT NOT NULL,
    weight INTEGER DEFAULT 1,      -- Frequency/confidence
    source TEXT,                   -- Where this name came from
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    PRIMARY KEY (customer_id, name_variant)
);
```

**inventory Table** (Products/items)
```sql
CREATE TABLE inventory (
    inventory_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sellers_item_identification TEXT,  -- SKU
    hsn_code TEXT,
    hsn_source TEXT CHECK(hsn_source IN ('AUTO', 'MANUAL', 'AI')),
    environment TEXT,
    -- ...
);
```

**invoice_pdfs Table** (fixed.pdf tracking)
```sql
CREATE TABLE invoice_pdfs (
    pdf_id TEXT PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices(invoice_id),
    pdf_type TEXT CHECK(pdf_type IN ('original', 'fixed', 'signed')),
    minio_path TEXT NOT NULL,      -- Path in MinIO
    blob_uuid TEXT,                -- Link to blob_entries
    file_size_bytes INTEGER,
    environment TEXT,
    created_at TIMESTAMP
);
```

### TIN and RC Number Validation

**TIN Format:** `12345678-001` (8 digits + hyphen + 3 digits)
```python
import re

def validate_tin(tin: str) -> bool:
    pattern = r'^\d{8}-\d{3}$'
    return re.match(pattern, tin) is not None
```

**RC Number Format:** `RC123456` (RC prefix + 6-7 digits)
```python
def validate_rc_number(rc: str) -> bool:
    pattern = r'^RC\d{6,7}$'
    return re.match(pattern, rc) is not None
```

**Customer Validation:**
```python
def is_valid_customer_data(customer_data):
    """Customer must have TIN or RC Number (not both required, either is sufficient)"""
    tin = customer_data.get("tin")
    rc = customer_data.get("rc_number")

    has_valid_tin = tin and validate_tin(tin)
    has_valid_rc = rc and validate_rc_number(rc)

    # TIN takes precedence if both present
    if has_valid_tin:
        customer_data["primary_identifier"] = "tin"
        customer_data["primary_value"] = tin
    elif has_valid_rc:
        customer_data["primary_identifier"] = "rc_number"
        customer_data["primary_value"] = rc
    else:
        return False  # Neither valid - reject silently

    return True
```

---

## ACCESS PATTERN ARCHITECTURE

### Universal Resource Access

**Design Principle:** Same abstraction for databases, queues, blob storage

**Architecture:**
```
External Consumers (Float SDK, HeartBeat, Edge)
└─ Core API (public endpoints)
   └─ ResourceClient (internal, auto-detect)
      ├─ Direct: Local connection (same machine, fast)
      └─ API: Remote connection (different machine, flexible)
```

### Auto-Detect Logic

```python
class ResourceClient:
    """Universal client for any resource (database, queue, blob)"""

    def __init__(self, resource_name: str, config: dict):
        self.resource_name = resource_name
        self.connection_type = self._auto_detect(config)

        if self.connection_type == "direct":
            self.client = DirectClient(config)
        else:
            self.client = APIClient(config)

    def _auto_detect(self, config: dict) -> str:
        """Auto-detect: local or remote?"""
        # Check for localhost
        if config.get("host") in ["localhost", "127.0.0.1", "::1"]:
            return "direct"

        # Check for API endpoint
        if config.get("api_endpoint"):
            return "api"

        # Default: direct
        return "direct"

    def query(self, operation: str, params: dict):
        """Unified interface (works for both direct and API)"""
        return self.client.execute(operation, params)
```

### Configuration Examples

**Test/Standard (Local):**
```json
{
  "databases": {
    "customers": {
      "type": "postgresql",
      "host": "localhost",
      "port": 5433,
      "database": "customers"
    }
  }
}
```
→ Auto-detect: **Direct connection** (fast)

**Pro/Enterprise (Remote):**
```json
{
  "databases": {
    "customers": {
      "type": "postgresql",
      "api_endpoint": "http://customers-db-api.internal:8090/api/v1/customers",
      "fallback_host": "customers-db-server.internal",
      "fallback_port": 5433
    }
  }
}
```
→ Auto-detect: **API connection** (distributed)

### Same Code, All Tiers

```python
# Works for Test, Standard, Pro, Enterprise (no code changes!)
customers_client = ResourceClient("customers", config)
customer = customers_client.query("SELECT * FROM customers WHERE tin = ?", {"tin": "12345678-001"})

# Auto-detects:
# - Test/Standard: Direct Postgres connection
# - Pro/Enterprise: API call to customers-db-api
```

---

## PROCESSING PIPELINE OVERVIEW

### 8-Phase Pipeline (Happy Path)

```
Phase 1: FETCH
├─ Consume from RabbitMQ (core_queue)
├─ Fetch file from MinIO (blob_path)
└─ Output: Raw file data

Phase 2: PARSE
├─ Detect file type (PDF, Excel, CSV, XML, JSON)
├─ Parse file structure
└─ Output: Raw structured data

Phase 3: TRANSFORM
├─ Load customer transformation script
├─ Extract customers (TIN/RC validated, silently skip if invalid)
├─ Extract inventory (SKU, product names)
├─ Extract invoices (link to customers/inventory)
└─ Output: In-memory data (customers_raw, inventory_raw, invoices_raw)

Phase 4: ENRICH
├─ Call Prodeus APIs (HSN, category, postal, AI)
├─ Enrich customers (postal validation)
├─ Enrich inventory (HSN codes)
├─ Graceful degradation (continue if API fails, flag as MANUAL)
└─ Output: Enriched data (in-memory)

Phase 5: RESOLVE (In-Memory Deduplication)
├─ Query existing customers.db (READ only)
├─ Merge in-memory customers with existing records
├─ Deduplicate (fuzzy matching on TIN/RC)
├─ Choose canonical names (scoring algorithm)
├─ Query existing inventory.db (READ only)
├─ Merge in-memory inventory with existing records
├─ Deduplicate (exact match on SKU)
├─ Link invoices to customer_id, inventory_ids
└─ Output: Clean, resolved data (in-memory, ready to write)

Phase 6: PORTO BELLO (Business Logic Gate)
├─ Check customer.portoBello_enabled
├─ IF true:
│  ├─ Generate IRN and QR code (early)
│  ├─ Set invoice.status = 'pending_counterparty_details'
│  └─ Mark for Edge: SIGN only (not TRANSMIT)
└─ IF false: Continue to Phase 7

Phase 7: BRANCH (Preview vs Immediate)
├─ Check immediate_processing flag
├─ IF false (PREVIEW MODE):
│  ├─ Generate preview outputs (firs_invoices.json, report.json, etc.)
│  ├─ Generate fixed.pdf (if applicable: single PDF invoice only)
│  ├─ Append to MinIO
│  └─ STOP (do NOT write to database, wait for finalization)
└─ IF true (IMMEDIATE MODE): Continue to Phase 8

Phase 8: FINALIZE (Database Write)
├─ Apply user edits (if preview mode finalization)
├─ Generate IRN and QR code (if not done in Porto Bello)
├─ Write to databases:
│  ├─ customers.db (upsert, merge with existing)
│  ├─ inventory.db (upsert, merge with existing)
│  └─ invoices.db or invoices_test.db (environment-based)
├─ Write to edge_queue (RabbitMQ, task for Edge)
├─ Call Edge API (optional, for immediate processing)
├─ Trigger WebSocket broadcasts (invoice.created, customer.updated, etc.)
├─ Log to audit.db (core.processing_completed)
└─ ACK RabbitMQ message (remove from core_queue)
```

### Phase Validation: Resolution → Porto Bello → Branch → Finalize

**Question:** Do Porto Bello, Branch, and Preview happen between RESOLVE and FINALIZE?

**Answer:** YES ✅

```
Phase 5: RESOLVE → Data is clean (in-memory)
Phase 6: PORTO BELLO → Business logic checks (may generate IRN early)
Phase 7: BRANCH → Preview or immediate? (may STOP here, no DB write)
Phase 8: FINALIZE → Database writes (only phase that commits data)
```

**Key Insight:** Resolution produces clean data, but database writes happen ONLY in Finalize.

---

## PHASE BREAKDOWN

### Phase 0: Infrastructure (6 Sub-Phases)

**Purpose:** Build foundational infrastructure for all processing phases

#### **Phase 0a: Database Schemas (THIS DOCUMENT)**
- **Variant:** Sonnet
- **Effort:** 6-8 hours
- **Deliverables:**
  - INFRASTRUCTURE_OVERVIEW.md (this document)
  - DATABASE_SCHEMAS.md
  - DATABASE_DECISIONS.md
  - DATABASE_IMPLEMENTATION_CHECKLIST.md
  - UNIVERSAL_ACCESS_PATTERN.md

#### **Phase 0b: Database Implementation**
- **Variant:** Haiku
- **Effort:** 8-12 hours
- **Dependencies:** Phase 0a complete
- **Deliverables:**
  - Postgres schema creation scripts
  - SQLite schema (invoices_test.db)
  - Connection pooling
  - Migration system
  - 90%+ test coverage

#### **Phase 0c: API Framework**
- **Variant:** Sonnet
- **Effort:** 6-10 hours
- **Dependencies:** Can run concurrently with 0b
- **Deliverables:**
  - FastAPI application setup
  - 18 endpoint stubs
  - Request/response models (Pydantic)
  - Authentication middleware
  - Error handling framework

#### **Phase 0d: Database Access Layer**
- **Variant:** Sonnet
- **Effort:** 4-6 hours
- **Dependencies:** Phase 0b complete (needs database)
- **Deliverables:**
  - ResourceClient with auto-detect
  - Direct Postgres connection
  - REST API client (for remote DBs)
  - Unified query interface
  - 90%+ test coverage

#### **Phase 0e: WebSocket + Access Control**
- **Variant:** Opus
- **Effort:** 8-10 hours
- **Dependencies:** Can run concurrently with 0c, 0d
- **Deliverables:**
  - Async WebSocket server
  - Event broadcasting system
  - Database triggers (auto-broadcast)
  - RBAC permission checking
  - Integration with config.db (user_permissions)

#### **Phase 0f: Support Modules + Integration**
- **Variant:** Opus
- **Effort:** 6-8 hours
- **Dependencies:** All above phases complete
- **Deliverables:**
  - Error classes (all error codes)
  - Structured JSON logging
  - Configuration loader (tier-specific)
  - BlobClient wrapper (MinIO)
  - Prometheus metrics
  - Integration tests
  - 90%+ coverage across Infrastructure

### Timeline (With Concurrency)

```
Week 1:
├─ Phase 0a (Sonnet) - Days 1-2
├─ Phase 0b (Haiku) - Days 2-4 (starts after 0a)
└─ Phase 0c (Sonnet) - Days 2-4 (concurrent with 0b)

Week 2:
├─ Phase 0d (Sonnet) - Days 5-6 (after 0b)
└─ Phase 0e (Opus) - Days 5-7 (concurrent with 0d)

Week 3:
└─ Phase 0f (Opus) - Days 8-9 (integration)

Total: ~9 days with concurrency (vs ~15 days sequential)
```

---

## TEST VS PRODUCTION ISOLATION

### Isolation Strategy

**Separate Databases for Invoices:**
- **Test:** `invoices_test.db` (SQLite, local file)
- **Production:** `invoices.db` (PostgreSQL, remote server)

**Shared Databases for Master Data:**
- **customers.db:** Postgres (shared, environment flag in data)
- **inventory.db:** Postgres (shared, environment flag in data)

### Rationale

**Why separate invoices databases?**
1. ✅ Complete isolation (test never touches production invoices)
2. ✅ SQLite for fast local development (no remote DB needed)
3. ✅ Safe testing (can delete all test data without affecting production)

**Why shared customers/inventory databases?**
1. ✅ Master data is company-wide (not environment-specific)
2. ✅ Consistent customer names across test and production
3. ✅ Single source of truth for B2B customers
4. ✅ Inventory catalog shared (same products in test and prod)

### Environment Routing

**RabbitMQ Message with Environment Flag:**
```json
{
  "queue_id": "queue_123",
  "environment": "test",  // or "production"
  "blob_path": "/files_blob/550e8400-...-invoice.pdf",
  // ...
}
```

**Core Worker Routing Logic:**
```python
def process_invoice(message):
    environment = message["environment"]

    # Route to correct invoices database
    if environment == "test":
        invoices_db = sqlite_client("invoices_test.db")
    else:
        invoices_db = postgres_client("invoices.db")

    # Shared databases (customers, inventory)
    customers_db = postgres_client("customers.db")  # Same for both
    inventory_db = postgres_client("inventory.db")  # Same for both

    # Process...
    customers = extract_customers(raw_data)
    for customer in customers:
        customer["environment"] = environment  # Tag with environment
        customers_db.upsert(customer)

    # Write invoice to environment-specific database
    invoices_db.insert(invoice)
```

**Edge Routing (FIRS Sandbox vs Production):**
```python
def submit_to_firs(invoice_id, environment):
    # Route to FIRS endpoint based on environment
    if environment == "test":
        firs_endpoint = "https://sandbox.firs.gov.ng/api/v1/submit"
    else:
        firs_endpoint = "https://api.firs.gov.ng/api/v1/submit"

    response = requests.post(firs_endpoint, json=invoice_data)
```

---

## AUDIT LOGGING STRATEGY

### Centralized Logging: audit.db

**All Helium services log to HeartBeat's audit.db:**
- Core → audit.db
- Relay → audit.db
- Edge → audit.db
- HeartBeat → audit.db (own logs)

**audit.db Schema:**
```sql
CREATE TABLE audit_log (
    log_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,  -- 'relay.file_queued', 'core.processing_started', etc.
    service TEXT NOT NULL,     -- 'core', 'relay', 'edge', 'heartbeat'
    queue_id TEXT,             -- Link to processing queue
    blob_uuid TEXT,            -- Link to blob storage
    invoice_id TEXT,           -- Link to invoice (if applicable)
    user_id TEXT,              -- User who triggered action (if applicable)
    environment TEXT,          -- 'test' or 'production'
    details JSONB,             -- Event-specific data
    created_at TIMESTAMP NOT NULL,
    INDEX idx_event_type ON audit_log(event_type),
    INDEX idx_queue_id ON audit_log(queue_id),
    INDEX idx_created_at ON audit_log(created_at)
);
```

### Core Service Audit Events

**Processing Events:**
```
- core.processing_started
- core.processing_completed
- core.processing_failed
- core.phase_1_fetch_completed
- core.phase_2_parse_completed
- core.phase_3_transform_completed
- core.phase_4_enrich_completed
- core.phase_5_resolve_completed
- core.phase_6_porto_bello_completed
- core.phase_7_branch_completed
- core.phase_8_finalize_completed
```

**Database Events:**
```
- core.invoice_created
- core.customer_created
- core.customer_updated
- core.inventory_created
- core.inventory_updated
```

**Edge Integration Events:**
```
- core.edge_queue_write
- core.edge_api_called
- core.edge_api_failed
```

### HeartBeat Reconciliation Using audit.db

**Query for Stuck Entries:**
```sql
-- Find entries that started but never completed
SELECT
    queue_id,
    blob_uuid,
    created_at,
    EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600 AS hours_stuck
FROM audit_log
WHERE event_type = 'core.processing_started'
AND created_at < NOW() - INTERVAL '1 hour'
AND queue_id NOT IN (
    SELECT queue_id
    FROM audit_log
    WHERE event_type IN ('core.processing_completed', 'core.processing_failed')
)
ORDER BY created_at ASC;
```

**HeartBeat Reconciliation Flow:**
```python
# HeartBeat hourly reconciliation job
def reconcile_core_processing():
    # Query audit.db for stuck entries
    stuck_entries = audit_db.query(STUCK_ENTRIES_SQL)

    if stuck_entries:
        for entry in stuck_entries:
            # Alert admin
            alert_admin(f"Stuck Core entry: {entry['queue_id']} ({entry['hours_stuck']}h)")

            # Log reconciliation event
            audit_db.log({
                "event_type": "heartbeat.stuck_entry_detected",
                "service": "heartbeat",
                "queue_id": entry["queue_id"],
                "details": {
                    "hours_stuck": entry["hours_stuck"],
                    "action": "admin_alerted"
                }
            })

    # No automatic retry (requires manual investigation)
    # Core workers will auto-retry via RabbitMQ redelivery
```

### 7-Year Retention (FIRS Compliance)

**audit.db Retention Policy:**
- ✅ Never delete (permanent audit trail)
- ✅ Partition by year (audit_log_2026, audit_log_2027, etc.)
- ✅ Archive old partitions (after 7 years, move to cold storage)
- ✅ Full-text search indexes (for compliance queries)

**Note:** This architecture is documented in **HELIUM_OVERVIEW.md** (top-level Helium document)

---

## PERFORMANCE CONSIDERATIONS

### RabbitMQ vs Postgres Queue

| Metric | RabbitMQ | Postgres Queue |
|--------|----------|----------------|
| **Latency** | 1-5ms | 50-100ms |
| **Throughput** | 10,000+ msg/sec | 500 queries/sec |
| **Queryable** | ❌ No (consume only) | ✅ Yes (SQL queries) |
| **Durable** | ⚠️ Needs persistence | ✅ Yes (ACID) |
| **Reconciliation** | ❌ Can't query messages | ✅ Easy to query |

**DECISION:** RabbitMQ for speed + audit.db for queryability ✅

### Database Connection Pooling

**Postgres Connection Pools:**
```python
# Test/Standard: 5-10 connections
postgres_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=5,
    maxconn=10,
    host="localhost",
    database="customers"
)

# Pro/Enterprise: 50-100 connections
postgres_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=20,
    maxconn=100,
    host="customers-db-server.internal",
    database="customers"
)
```

### Celery Worker Scaling

**Test/Standard:**
- 5 Celery workers (single machine)
- Threading-based (Python ThreadPoolExecutor)
- Batch size: 100 invoices per task

**Pro/Enterprise:**
- 50+ Celery workers (distributed across machines)
- Process-based (separate Python processes)
- Batch size: 100 invoices per task
- Horizontal scaling (add more machines)

### Batch Processing Performance

**Single Invoice:**
- Processing time: ~2-5 seconds
- Throughput: 12-30 invoices/minute (single worker)

**Bulk Upload (30,000 invoices):**
- Split into 300 batches (100 invoices each)
- 10 Celery workers (parallel processing)
- Processing time: ~1-2 minutes
- Throughput: 15,000-30,000 invoices/minute

---

## DEPLOYMENT ARCHITECTURE

### Test/Standard Deployment

```
Single Machine:
├─ Core API (FastAPI, port 8080)
├─ Core Workers (5 Celery workers, threading)
├─ RabbitMQ (localhost:5672)
├─ PostgreSQL (localhost:5432, 5433, 5434)
├─ SQLite (invoices_test.db, local file)
└─ MinIO (localhost:9000)
```

**Configuration:**
```json
{
  "environment": "test",
  "databases": {
    "invoices_test": {
      "type": "sqlite",
      "path": "./databases/invoices_test.db"
    },
    "customers": {
      "type": "postgresql",
      "host": "localhost",
      "port": 5433
    },
    "inventory": {
      "type": "postgresql",
      "host": "localhost",
      "port": 5434
    }
  },
  "rabbitmq": {
    "host": "localhost",
    "port": 5672
  },
  "workers": {
    "celery_workers": 5,
    "batch_size": 100
  }
}
```

### Pro/Enterprise Deployment

```
Distributed Architecture:
├─ Core API (3 instances, load balanced)
├─ Core Workers (50 Celery workers, distributed)
├─ RabbitMQ Cluster (3 nodes, replicated)
├─ PostgreSQL Cluster (3 databases, separate hosts)
├─ MinIO Cluster (distributed erasure coding)
└─ HeartBeat Service (separate machine, owns audit.db)
```

**Configuration:**
```json
{
  "environment": "production",
  "databases": {
    "invoices": {
      "type": "postgresql",
      "api_endpoint": "http://invoices-db-api:8092/api/v1/invoices",
      "fallback_host": "invoices-db-server.internal",
      "fallback_port": 5434
    },
    "customers": {
      "type": "postgresql",
      "api_endpoint": "http://customers-db-api:8090/api/v1/customers",
      "fallback_host": "customers-db-server.internal",
      "fallback_port": 5433
    },
    "inventory": {
      "type": "postgresql",
      "api_endpoint": "http://inventory-db-api:8091/api/v1/inventory",
      "fallback_host": "inventory-db-server.internal",
      "fallback_port": 5432
    }
  },
  "rabbitmq": {
    "nodes": [
      "rabbitmq-1.internal:5672",
      "rabbitmq-2.internal:5672",
      "rabbitmq-3.internal:5672"
    ],
    "cluster_enabled": true
  },
  "workers": {
    "celery_workers": 50,
    "batch_size": 100,
    "autoscale": true,
    "max_workers": 100
  }
}
```

---

## IMPLEMENTATION ROADMAP

### Phase 0a (THIS PHASE)
- ✅ INFRASTRUCTURE_OVERVIEW.md (this document)
- ⏳ DATABASE_SCHEMAS.md (next)
- ⏳ DATABASE_DECISIONS.md
- ⏳ DATABASE_IMPLEMENTATION_CHECKLIST.md
- ⏳ UNIVERSAL_ACCESS_PATTERN.md

### Phase 0b (Next Chat - Haiku)
- Database schema implementation
- Connection pooling
- Migration system
- 90%+ test coverage

### Phase 0c (Concurrent - Sonnet)
- FastAPI application
- 18 endpoint stubs
- Authentication middleware

### Phase 0d (After 0b - Sonnet)
- ResourceClient with auto-detect
- Database access layer
- Unified query interface

### Phase 0e (Concurrent - Opus)
- WebSocket server
- Event broadcasting
- Database triggers
- RBAC access control

### Phase 0f (Final - Opus)
- Error classes
- Logging
- Configuration loader
- BlobClient wrapper
- Prometheus metrics
- Integration testing

### Phases 1-8 (After Infrastructure)
- Phase 1: FETCH (Haiku)
- Phase 2: PARSE (Haiku)
- Phase 3: TRANSFORM (Sonnet)
- Phase 4: ENRICH (Sonnet)
- Phase 5: RESOLVE (Sonnet)
- Phase 6: PORTO BELLO (Opus)
- Phase 7: BRANCH (Opus)
- Phase 8: FINALIZE (Opus)

---

## SUMMARY

**Key Architectural Decisions:**
1. ✅ RabbitMQ for queues (fast, 1-5ms latency)
2. ✅ audit.db for audit trail (permanent, queryable)
3. ✅ No intermediate tracking tables (simplified)
4. ✅ Universal access pattern (auto-detect direct vs API)
5. ✅ Resolution before write (in-memory deduplication)
6. ✅ Test isolation with shared master data
7. ✅ Same codebase for all tiers (config-driven)
8. ✅ HeartBeat reconciliation via audit.db

**Next Steps:**
1. Create DATABASE_SCHEMAS.md (complete SQL)
2. Create DATABASE_DECISIONS.md (rationale)
3. Create DATABASE_IMPLEMENTATION_CHECKLIST.md (for Haiku)
4. Create UNIVERSAL_ACCESS_PATTERN.md (shared across services)
5. Begin Phase 0b implementation (next chat)

---

**Document Status:** ✅ COMPLETE
**Last Updated:** 2026-02-05
**Next Document:** DATABASE_SCHEMAS.md
**Estimated Time:** 2 hours
