# DATABASE DECISIONS - ARCHITECTURAL RATIONALE

**Version:** 1.0
**Date:** 2026-02-05
**Phase:** Phase 0a (Infrastructure Architecture)
**Status:** BINDING DOCUMENT - All decisions are mandatory unless explicitly revised by user

---

## OVERVIEW

This document captures the architectural rationale for all database design decisions in the Helium Core Infrastructure. Each decision includes the problem context, alternatives considered, chosen solution, and reasoning.

**Authority**: This document consolidates decisions from the 2+ hour Phase 0a conversation and supersedes any conflicting information in other documents.

---

## DECISION 1: PostgreSQL vs SQLite

### Problem

Need to choose database technology for Core service that supports:
- Concurrent access from multiple workers
- Production scalability (10K+ invoices/day)
- Test environment isolation
- Minimal operational complexity

### Alternatives Considered

**Option A: SQLite for Everything**
- Pros: Zero configuration, embedded, fast for single-user
- Cons: Limited concurrency, no network access, harder to scale

**Option B: PostgreSQL for Everything**
- Pros: Production-grade, excellent concurrency, scalable
- Cons: More complex setup, overkill for test environments

**Option C: Hybrid (PostgreSQL + SQLite)**
- Pros: Best of both worlds - production power, test simplicity
- Cons: Dual codebase maintenance, abstraction layer needed

### Decision

**Chosen: Option C - Hybrid Approach**

**PostgreSQL for Production**:
- `invoices.db` (production invoices)
- `customers.db` (shared master data)
- `inventory.db` (shared master data)
- `notifications.db`
- `core.db` (core_queue)

**SQLite for Test**:
- `invoices_test.db` (test invoices only)
- `core.db` (core_queue for test)

### Rationale

1. **Cost/Resource Balance**: User asked "if cost, resource implication and complexity are negligible" - PostgreSQL meets this bar
2. **Concurrency**: Production workloads need multiple workers processing simultaneously
3. **Scalability**: Pro/Enterprise tiers need distributed database access
4. **Test Isolation**: SQLite for test invoices provides complete environment isolation
5. **Shared Master Data**: Customers/inventory are company-wide resources, not environment-specific

**User Quote**: "PostGres, if cost, resource implication and complexity are negligible"

---

## DECISION 2: Separate Databases vs Single Database

### Problem

Should Core use one monolithic database or separate databases for different concerns?

### Alternatives Considered

**Option A: Single Database (core.db)**
- All tables in one database
- Pros: Simpler cross-table joins, single connection
- Cons: Tight coupling, harder to scale individual components

**Option B: Separate Databases by Concern**
- `invoices.db`, `customers.db`, `inventory.db`, etc.
- Pros: Separation of concerns, independent scaling
- Cons: No cross-database foreign keys, more connections

### Decision

**Chosen: Option B - Separate Databases**

- `invoices.db` / `invoices_test.db` - Invoice lifecycle
- `customers.db` - Customer master data
- `inventory.db` - Product/inventory master data
- `notifications.db` - System notifications
- `core.db` - Core queue (separate from business data)

### Rationale

1. **Separation of Concerns**: Each database has single responsibility
2. **Independent Scaling**: Can scale invoice writes separately from master data reads
3. **Access Control**: Different services can have different permissions
4. **Backup Strategy**: Can backup invoices more frequently than master data
5. **Test Isolation**: Can use SQLite for invoices_test without affecting master data

**Trade-off**: No foreign key constraints across databases (enforced at application layer)

---

## DECISION 3: Test/Production Isolation Strategy

### Problem

How to isolate test invoices from production while sharing master data (customers/inventory)?

### Alternatives Considered

**Option A: Duplicate Everything**
- Separate databases: `invoices_test.db`, `customers_test.db`, `inventory_test.db`
- Pros: Complete isolation
- Cons: Master data sync nightmare, doubled storage

**Option B: Environment Flags in Shared Tables**
- Single database with `environment` column ('test' vs 'production')
- Pros: Shared master data, single source of truth
- Cons: Risk of accidental cross-environment queries

**Option C: Separate Invoice DB + Shared Master Data**
- `invoices_test.db` (SQLite) + `invoices.db` (PostgreSQL)
- `customers.db` and `inventory.db` shared with environment flags
- Pros: Complete invoice isolation, shared master data
- Cons: Mixed database technologies

### Decision

**Chosen: Option C - Hybrid Isolation**

**Separate Invoice Databases**:
- `invoices.db` (PostgreSQL, production only)
- `invoices_test.db` (SQLite, test only)

**Shared Master Data with Environment Flags**:
- `customers.db` has `environment` field ('test', 'production')
- `inventory.db` has `environment` field ('test', 'production')

### Rationale

1. **Invoice Isolation**: Test invoices NEVER mix with production (separate DBs)
2. **Master Data Sharing**: Customer "Acme Corp" is same entity in test and production
3. **User Confirmation**: User explicitly stated "Inventory and customers do not have test and production, only invoices"
4. **Reality Modeling**: Test invoices are sandbox; test customers are real customers being tested
5. **Simplified Workflow**: No need to sync customer/product data between environments

**User Quote**: "Approach B: Separate Databases (Inventory and customers do not have test and production, only invoices)"

---

## DECISION 4: Queue Storage (RabbitMQ vs Database)

### Problem

How to implement `core_queue` and `edge_queue` for message passing between services?

### Alternatives Considered

**Option A: Database Tables Only**
- Use PostgreSQL/SQLite tables as queues
- Pros: Queryable, durable, simple
- Cons: Slow (100ms+ latency), poor scalability

**Option B: RabbitMQ Only**
- Pure message queue, no database tracking
- Pros: Fast (1-5ms latency), scalable
- Cons: Not queryable, lost on restart

**Option C: RabbitMQ + Database Tracking**
- RabbitMQ for messages + separate tracking table
- Pros: Fast messaging, queryable status
- Cons: Complexity, data duplication

**Option D: RabbitMQ + audit.db**
- RabbitMQ for messages + audit.db for tracking
- Pros: Fast messaging, single audit trail
- Cons: No separate tracking table

### Decision

**Chosen: Option D - RabbitMQ + audit.db (No Tracking Tables)**

**Queue Implementation**:
- **core_queue**: Database table for durable work tracking
- **edge_queue**: Database table for Edge work tracking (optional, may use invoices.db firs_status field)
- **RabbitMQ**: NOT used for Core queue (user clarification)

**Audit Logging**:
- All queue operations logged to `audit.db` (HeartBeat-owned, PostgreSQL)
- No separate `core_processing_status` or `edge_processing_status` tables
- HeartBeat performs all tracking and reconciliation using audit logs

### Rationale

1. **User Clarification**: "We already have an audit.db that is supposed to have all these logs. I think we should use that and leave the RabbitMQ."
2. **Single Source of Truth**: audit.db provides complete audit trail
3. **No Duplication**: "With audit.db, we no longer require the extra postgres for tracking"
4. **HeartBeat Reconciliation**: "I think tracking and reconciliation should now be left entirely to HeartBeat using audit logs to track and reprocess critical orphaned requests"
5. **Simpler Architecture**: Fewer tables to maintain

**Edge Tracking**: Edge uses `invoices.db` with `firs_status` field for durable tracking, not separate edge_queue table. HeartBeat reconciliation uses audit.db to detect stuck submissions.

**audit.db Technology**: PostgreSQL (standardized across all tiers)

**Registration Timing**: audit.db registration happens atomically with Relay operations (same transaction as blob write + core_queue write), guaranteeing speed and consistency.

---

## DECISION 5: IRN Generation Location

### Problem

Where should Invoice Reference Number (IRN) be generated?

### Alternatives Considered

**Option A: FIRS Generates IRN**
- Submit invoice to FIRS, receive IRN back
- Pros: FIRS-authoritative
- Cons: Can't generate QR code before submission

**Option B: Core Generates IRN**
- Hash-based IRN generation before FIRS submission
- Pros: Can generate QR code immediately, faster processing
- Cons: Must ensure no collisions

### Decision

**Chosen: Option B - Core Generates IRN**

Core service generates IRN using hash-based algorithm before FIRS submission.

### Rationale

1. **QR Code Dependency**: QR code needs IRN, must be generated before submission
2. **Preview Mode**: Can show IRN in preview before user confirms
3. **Faster Processing**: No round-trip to FIRS for IRN
4. **Architecture Document**: CORE_ARCHITECTURE.md explicitly states Core generates IRN
5. **Hash Collision**: Negligible risk with SHA-256-based IRN

**FIRS Role**: Validates submitted invoices, returns confirmation number (not IRN)

---

## DECISION 6: Preview Data Storage

### Problem

Where to store semi-processed invoice data during preview mode (before user confirmation)?

### Alternatives Considered

**Option A: Create Draft Invoice in invoices.db**
- Write partial invoice record with `status = 'PREVIEW'`
- Pros: Standard database record
- Cons: Database bloat, cleanup complexity

**Option B: Store in Blob Metadata**
- Append semi-processed JSON to blob storage
- Pros: Stateless Core, automatic cleanup
- Cons: Not queryable in database

**Option C: Separate preview_data Table**
- Dedicated table for preview state
- Pros: Queryable, separate from invoices
- Cons: Extra table, manual cleanup

### Decision

**Chosen: Option B - Blob Metadata Storage**

Semi-processed preview data stored as JSON in blob storage (appended to original file).

### Rationale

1. **Stateless Core**: Core doesn't track pending previews in database
2. **Automatic Cleanup**: Blob cleanup jobs remove stale preview data (24-hour expiry)
3. **No Database Bloat**: invoices.db only contains finalized invoices
4. **Fast Access**: Blob storage optimized for file operations
5. **Architecture Decision**: Confirmed in INFRASTRUCTURE_OVERVIEW.md

**Preview Flow**:
1. Core extracts and enriches data
2. Appends semi-processed JSON to blob
3. Returns preview to user
4. On user confirmation, Core re-reads blob and finalizes

**User Concern Addressed**: "I cant really make sense of what resolve and branch do" - Preview storage keeps resolution in-memory until finalization

---

## DECISION 7: Entity Resolution Timing

### Problem

When should customer/product deduplication and matching happen?

### Alternatives Considered

**Option A: After Database Write**
- Write raw data to invoices.db, then resolve duplicates
- Pros: Simple write flow
- Cons: Dirty data in database, cleanup complexity

**Option B: Before Database Write (In-Memory)**
- Resolve entities in Phase 5 (RESOLVE), write clean data in Phase 8 (FINALIZE)
- Pros: Clean database, no post-write cleanup
- Cons: Requires in-memory state management

### Decision

**Chosen: Option B - Resolution Before Write**

Phase 5 (RESOLVE) performs all entity resolution in-memory before Phase 8 (FINALIZE) commits to database.

### Rationale

1. **Clean Database**: Only resolved, deduplicated data written to invoices.db
2. **User Confirmation**: "i think Resolve should run before writes to database! (THIS)"
3. **No Cleanup Jobs**: No need to fix data after write
4. **Better Data Quality**: Database reflects business reality from the start
5. **Idempotent Writes**: Same input always produces same database state

**Processing Pipeline**:
```
Phase 4: ENRICH → Phase 5: RESOLVE → Phase 6: PORTO BELLO → Phase 7: BRANCH → Phase 8: FINALIZE (DB write)
```

**User Quote**: "i think Resolve should run before writes to database! (THIS)"

---

## DECISION 8: Customer Validation Rules

### Problem

What validation rules should apply for customer data extracted from invoices?

### Alternatives Considered

**Option A: Accept Any Customer Data**
- Create customer record from any name in invoice
- Pros: Never reject invoices
- Cons: Dirty master data, compliance risk

**Option B: Require TIN or RC Number**
- Customer must have valid Nigerian tax identifier
- Pros: FIRS compliance, clean master data
- Cons: Some invoices may be rejected

**Option C: Require TIN AND RC Number**
- Customer must have both identifiers
- Pros: Maximum data quality
- Cons: Too restrictive, many valid customers excluded

### Decision

**Chosen: Option B - Require TIN OR RC Number**

Customer data must have valid TIN (Tax Identification Number) or RC Number (Company Registration Number).

**Validation Rules**:
- **TIN Format**: `12345678-001` (8 digits + hyphen + 3 digits)
- **RC Number Format**: `RC123456` or `RC1234567` (RC prefix + 6-7 digits)
- **Precedence**: If customer has BOTH TIN and RC, use TIN as primary identifier
- **Silent Rejection**: Invoices without valid identifier are silently skipped (no error to user)

### Rationale

1. **FIRS Compliance**: Nigerian tax authorities require valid identifiers
2. **User Confirmation**: "NO. TIN or RC number required"
3. **Flexible**: Accepts either TIN or RC (not both required)
4. **Nigerian Context**: User confirmed "Always starts with 'RC'. You can check the internet for what it is in Nigeria"
5. **Precedence Rule**: "If customer has BOTH TIN and RC, which do we use? (TIN)"

**Silent Operation**: "these are silent operations. no rejection to user. Just silently skip"

---

## DECISION 9: Customer Name Deduplication Strategy

### Problem

How to handle multiple name variations for same customer (e.g., "Acme Corp", "ACME CORPORATION PLC")?

### Alternatives Considered

**Option A: First Name Wins**
- Use first name encountered, ignore variations
- Pros: Simple
- Cons: Loses business intelligence

**Option B: Canonical Name with Variants Table**
- Track all name variations, choose canonical name by algorithm
- Pros: Preserves all data, robust matching
- Cons: More complex

**Option C: Manual Review for Each Variant**
- Prompt user to confirm every name variant
- Pros: Maximum accuracy
- Cons: User fatigue, slow processing

### Decision

**Chosen: Option B - Canonical Name with Variants**

**Implementation**:
- `customers.company_name` - Current canonical name
- `customers.company_name_normalized` - Lowercase, no punctuation (for matching)
- `customer_name_variants` table - All historical name variations

**Canonical Selection Algorithm**:
1. Track occurrence count for each variant
2. Assign weights based on source (user input = 3, invoice extraction = 2, automated = 1)
3. Canonical name = variant with highest (weight × occurrence_count)

### Rationale

1. **Fuzzy Matching**: Normalized names enable matching "ACME CORP" to "Acme Corporation"
2. **Audit Trail**: Never lose information about name variations
3. **Business Intelligence**: Understand how customers are referenced across documents
4. **User Question**: "What of capitalized alternatives?" - Variants table handles this

**Example**:
```
customers.company_name = "Acme Corporation PLC"  (canonical)
customer_name_variants:
  - "Acme Corp" (weight=2, count=5)
  - "ACME CORPORATION PLC" (weight=3, count=8)  ← Canonical (3×8 = 24)
  - "Acme Corporation" (weight=2, count=3)
```

---

## DECISION 10: Inventory Quantity Tracking

### Problem

Should Core service track inventory quantities, or just product metadata?

### Alternatives Considered

**Option A: Metadata Only**
- Store product info (name, SKU, price) but not quantities
- Pros: Simpler schema
- Cons: Can't track stock levels

**Option B: Full Inventory Management**
- Track quantities, reservations, transactions
- Pros: Complete inventory system
- Cons: Out of scope for e-invoicing

**Option C: Basic Quantity with Transactions**
- Track on-hand quantity + transaction history
- Pros: Useful for reconciliation, not overly complex
- Cons: Moderate complexity

### Decision

**Chosen: Option C - Basic Quantity with Transactions**

**Schema**:
- `inventory.quantity_on_hand` - Current stock level
- `inventory.quantity_reserved` - Reserved for orders
- `inventory.quantity_available` - Computed: on_hand - reserved
- `inventory_transactions` table - Movement history

### Rationale

1. **Invoice Validation**: Can warn if invoice quantity exceeds available stock
2. **FIRS Compliance**: Some industries require inventory reconciliation
3. **Future-Proofing**: Enables inventory tracking without major schema changes
4. **Computed Column**: PostgreSQL handles `quantity_available` calculation automatically
5. **Moderate Scope**: Not full ERP, but useful for e-invoicing context

**Note**: This is for informational/validation purposes, not real-time inventory management (which would be separate ERP system)

---

## DECISION 11: Soft Delete vs Hard Delete

### Problem

Should deleted records be permanently removed or flagged as deleted?

### Alternatives Considered

**Option A: Hard Delete**
- Physically remove rows from database
- Pros: Clean database, no deleted rows
- Cons: No recovery, audit trail lost

**Option B: Soft Delete (24-hour recovery window)**
- Flag rows with `deleted_at` timestamp, cleanup after 24 hours
- Pros: User can undo mistakes, audit trail preserved
- Cons: Slightly more complex queries

**Option C: Soft Delete (Permanent)**
- Never physically delete, only flag
- Pros: Complete audit trail
- Cons: Database bloat, performance impact

### Decision

**Chosen: Option B - Soft Delete with 24-hour Recovery**

**Implementation**:
- `deleted_at` timestamp field (NULL = active, NOT NULL = deleted)
- `deleted_by` user email field
- Cleanup job after 24 hours hard-deletes
- All queries filter `WHERE deleted_at IS NULL` (unless explicitly including deleted)

### Rationale

1. **User Safety**: Accidental deletes can be recovered within 24 hours
2. **Audit Trail**: Short-term history of what was deleted and when
3. **Performance**: Hard delete after 24 hours prevents database bloat
4. **Balance**: Not permanent (too much data), not immediate (no recovery)

**Cleanup Job**: HeartBeat runs daily job to hard-delete records where `deleted_at < NOW() - INTERVAL '24 hours'`

---

## DECISION 12: WebSocket Broadcast Mechanism

### Problem

How should Core notify Float SDK of database changes?

### Alternatives Considered

**Option A: Manual Broadcasts in Application Code**
- Core service explicitly calls `websocket.broadcast()` after every change
- Pros: Full control
- Cons: Error-prone, easy to forget

**Option B: Database Triggers (Automatic)**
- PostgreSQL triggers automatically broadcast on INSERT/UPDATE/DELETE
- Pros: Never miss an event, consistent
- Cons: Slightly more complex setup

**Option C: Change Data Capture (CDC)**
- External tool monitors database for changes
- Pros: Decoupled from Core service
- Cons: Over-engineered for this use case

### Decision

**Chosen: Option B - Database Triggers (Automatic)**

**Implementation**:
- PostgreSQL triggers on `invoices`, `customers`, `inventory`, `notifications` tables
- Triggers call `pg_notify()` with JSON event payload
- Core WebSocket server listens to `pg_notify` channel
- Broadcasts events to all connected Float SDK clients

### Rationale

1. **Consistency**: Every database change automatically broadcasts
2. **No Manual Code**: Can't forget to broadcast
3. **ARCHITECTURE DECISION**: Explicitly stated in CORE_ARCHITECTURE.md and HELIUM_OVERVIEW.md
4. **Performance**: PostgreSQL NOTIFY is very fast (<1ms)
5. **Decoupled**: Core service doesn't need to track listeners

**Event Types**:
- `invoice.created`, `invoice.updated`, `invoice.deleted`
- `customer.created`, `customer.updated`, `customer.deleted`
- `inventory.created`, `inventory.updated`, `inventory.deleted`
- `notification.created`, `notification.updated`

---

## DECISION 13: Foreign Key Constraints Across Databases

### Problem

How to handle relationships between tables in different databases (e.g., `invoices.customer_id` → `customers.id`)?

### Alternatives Considered

**Option A: No Foreign Keys**
- Store IDs as integers, no database-level enforcement
- Pros: Flexible, no cross-database constraint issues
- Cons: Referential integrity at application layer only

**Option B: Foreign Keys with ON DELETE SET NULL**
- Reference IDs with foreign keys, but allow orphans
- Pros: Some database enforcement
- Cons: Doesn't work across databases in PostgreSQL

**Option C: Application-Level Validation**
- Validate references in Python code before write
- Pros: Works across databases, explicit control
- Cons: No database-level enforcement

### Decision

**Chosen: Option C - Application-Level Validation**

**Implementation**:
- `invoices.customer_id` references `customers.id` (logically, not database FK)
- Core service validates customer exists before writing invoice
- If customer deleted after invoice created, invoice keeps `customer_id` (orphaned but intentional)
- Denormalized fields (`customer_name`, `customer_tin`) provide fast access without JOIN

### Rationale

1. **Cross-Database Reality**: PostgreSQL doesn't support foreign keys across databases
2. **Denormalization**: Key customer fields copied to invoice for fast access
3. **Explicit Validation**: Core service checks references before write
4. **Historical Accuracy**: If customer deleted, invoice still shows who it was for
5. **Performance**: No cross-database JOINs needed for common queries

**Trade-off**: Must trust application code for referential integrity (acceptable for single-service ownership)

---

## DECISION 14: Date/Time Storage Format

### Problem

How to store dates and timestamps consistently?

### Alternatives Considered

**Option A: Unix Timestamps (INTEGER)**
- Store seconds since epoch
- Pros: Language-agnostic, easy arithmetic
- Cons: Not human-readable, no timezone info

**Option B: ISO 8601 Strings (TEXT)**
- Store as "2026-02-05T14:30:00Z"
- Pros: Human-readable, timezone-aware
- Cons: String comparison issues, storage overhead

**Option C: Native Database Types**
- PostgreSQL `TIMESTAMP`, `DATE`
- Pros: Database-optimized, indexed efficiently
- Cons: Must handle timezone conversion in application

### Decision

**Chosen: Option C - Native Database Types**

**Implementation**:
- `DATE` for date-only fields (`invoice_date`, `due_date`)
- `TIMESTAMP` for date+time fields (`created_at`, `updated_at`)
- All timestamps stored in UTC
- Application converts to user timezone for display

### Rationale

1. **Database Optimization**: Native types have optimized storage and indexes
2. **Query Efficiency**: Date range queries use indexes effectively
3. **Standardization**: UTC everywhere eliminates timezone ambiguity
4. **PostgreSQL/SQLite Compatible**: Both support these types
5. **ISO 8601**: PostgreSQL automatically formats as ISO 8601 for JSON output

**Example**:
```sql
-- Date field
invoice_date DATE  -- "2026-02-05"

-- Timestamp field
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- "2026-02-05 14:30:00"
```

---

## DECISION 15: JSON vs Normalized Tables for FIRS Response

### Problem

How to store FIRS API response data (complex nested structure)?

### Alternatives Considered

**Option A: Normalized Tables**
- `firs_responses`, `firs_response_errors`, `firs_response_validations`
- Pros: Queryable at field level
- Cons: Over-engineering for read-only data

**Option B: JSON/JSONB Column**
- Single `firs_response_data JSONB` column
- Pros: Flexible, preserves structure
- Cons: Less queryable

**Option C: Hybrid (Key Fields + JSON)**
- `firs_status`, `firs_confirmation` (normalized) + `firs_response_data` (JSONB)
- Pros: Best of both worlds
- Cons: Some duplication

### Decision

**Chosen: Option C - Hybrid Approach**

**Implementation**:
- `firs_status` (normalized, indexed) - 'DRAFT', 'SUBMITTED', 'VALIDATED', 'REJECTED'
- `firs_confirmation` (normalized) - Confirmation number from FIRS
- `firs_submitted_at` (normalized, indexed) - Submission timestamp
- `firs_response_data` (JSONB) - Complete FIRS API response

### Rationale

1. **Query Performance**: Can filter on `firs_status` without parsing JSON
2. **Flexibility**: Complete FIRS response preserved for debugging
3. **PostgreSQL JSONB**: Indexable, queryable, compressed
4. **SQLite TEXT**: Falls back to TEXT in SQLite (still JSON string)
5. **Future-Proofing**: FIRS API may change, JSONB adapts without schema migration

**Example**:
```sql
SELECT invoice_id, firs_status, firs_confirmation
FROM invoices
WHERE firs_status = 'SUBMITTED'  -- Fast index scan
  AND firs_response_data->>'validation_code' = 'SUCCESS';  -- JSONB query
```

---

## DECISION 16: Index Strategy for Large Tables

### Problem

What indexes should be created for tables that may grow to millions of rows?

### Alternatives Considered

**Option A: Index Everything**
- Create index on every column
- Pros: Fast queries
- Cons: Slow writes, storage overhead

**Option B: Minimal Indexes**
- Only primary keys and foreign keys
- Pros: Fast writes
- Cons: Slow queries

**Option C: Strategic Indexes**
- Index commonly queried fields (status, dates, identifiers)
- Pros: Balanced performance
- Cons: Requires analysis

### Decision

**Chosen: Option C - Strategic Indexes**

**Index Categories**:

1. **Unique Identifiers**: `invoice_id`, `irn`, `queue_id`, `tin`, `rc_number`
2. **Foreign Keys**: `customer_id`, `product_id`, `invoice_id` (in child tables)
3. **Status Fields**: `firs_status`, `status` (for filtering)
4. **Date Fields**: `created_at`, `invoice_date` (for time-based queries)
5. **Compound Indexes**: `(priority, created_at)` for queue ordering

**Not Indexed**:
- Free-text fields (`description`, `notes`)
- Rarely queried fields (`address_line2`, `website`)

### Rationale

1. **Query Patterns**: Indexes based on expected query patterns from Float UI
2. **Write Performance**: Balance index overhead with query speed
3. **Storage Cost**: Each index adds storage overhead (~10-20% of table size)
4. **Maintenance Cost**: Indexes must be updated on every write
5. **PostgreSQL Query Planner**: Strategic indexes let query planner optimize

**Monitoring**: Track slow queries in production, add indexes as needed

---

## DECISION 17: Audit History Granularity

### Problem

How much detail should `invoice_history` table track?

### Alternatives Considered

**Option A: Complete Row Snapshots**
- Store entire invoice JSON on every change
- Pros: Complete history, easy rollback
- Cons: Massive storage overhead

**Option B: Field-Level Changes**
- Track only changed field (old value → new value)
- Pros: Storage efficient, granular audit
- Cons: Harder to reconstruct full state

**Option C: Event-Based Logging**
- Track only major events (created, submitted, paid)
- Pros: Minimal storage
- Cons: Loses intermediate changes

### Decision

**Chosen: Option B - Field-Level Changes**

**Implementation**:
- `change_type` - 'created', 'updated', 'deleted', 'status_changed'
- `changed_field` - Name of field that changed
- `old_value` - Previous value (TEXT)
- `new_value` - New value (TEXT)
- `changed_by` - User email
- `change_reason` - Optional explanation

### Rationale

1. **Audit Compliance**: FIRS may require field-level audit trail
2. **Storage Efficiency**: Only stores deltas, not full snapshots
3. **Queryable**: Can query "show all price changes" or "who changed customer_tin?"
4. **Reconstruction**: Can rebuild history by replaying changes
5. **User Attribution**: Know who made each change

**Example**:
```sql
-- Track status change
INSERT INTO invoice_history (invoice_id, change_type, changed_field, old_value, new_value, changed_by)
VALUES (123, 'status_changed', 'firs_status', 'DRAFT', 'SUBMITTED', 'user@example.com');
```

---

## DECISION 18: PostgreSQL vs SQLite Syntax Differences

### Problem

How to handle schema differences between PostgreSQL and SQLite?

### Alternatives Considered

**Option A: Lowest Common Denominator**
- Use only features supported by both
- Pros: Single schema file
- Cons: Loses PostgreSQL advantages

**Option B: Separate Schema Files**
- Maintain `schemas/postgres/` and `schemas/sqlite/`
- Pros: Use best features of each database
- Cons: Schema duplication

**Option C: Conditional Schema Generation**
- Template-based schema with database-specific sections
- Pros: Single source of truth
- Cons: Complex templating

### Decision

**Chosen: Option B - Separate Schema Files**

**File Structure**:
```
schemas/
├── postgres/
│   ├── invoices.sql
│   ├── customers.sql
│   └── ...
└── sqlite/
    ├── invoices_sqlite.sql
    └── core_queue_sqlite.sql
```

### Rationale

1. **PostgreSQL Features**: Use JSONB, SERIAL, triggers, pg_notify
2. **SQLite Simplicity**: Use INTEGER PRIMARY KEY AUTOINCREMENT, TEXT for JSON
3. **Clear Separation**: No confusion about which database uses which schema
4. **Type Mapping**: NUMERIC → REAL, JSONB → TEXT, TIMESTAMP → DATETIME
5. **Maintenance**: Schema changes documented separately for each database

**Key Differences**:
- PostgreSQL: `SERIAL`, `JSONB`, `NUMERIC`, `TIMESTAMP`, triggers with `pg_notify`
- SQLite: `INTEGER PRIMARY KEY AUTOINCREMENT`, `TEXT`, `REAL`, `DATETIME`, basic triggers

---

## SUMMARY TABLE: All Database Decisions

| Decision | Chosen Solution | Key Rationale |
|----------|----------------|---------------|
| Database Technology | PostgreSQL (production) + SQLite (test) | Concurrency, scalability, test isolation |
| Database Separation | Separate databases by concern | Independent scaling, access control |
| Test/Production Isolation | Separate invoice DBs + shared master data | Invoice isolation, shared customers/inventory |
| Queue Storage | Database tables + audit.db (no RabbitMQ for core_queue) | User clarification, single audit trail |
| IRN Generation | Core generates IRN (not FIRS) | QR code dependency, faster processing |
| Preview Storage | Blob metadata (JSON) | Stateless Core, automatic cleanup |
| Entity Resolution Timing | Before database write (in-memory) | Clean database, user confirmation |
| Customer Validation | TIN or RC Number required | FIRS compliance, silent rejection |
| Name Deduplication | Canonical name + variants table | Fuzzy matching, audit trail |
| Inventory Tracking | Basic quantity + transactions | Invoice validation, not full ERP |
| Soft Delete | 24-hour recovery window | User safety, audit trail, no bloat |
| WebSocket Broadcasts | Database triggers (automatic) | Consistency, no manual code |
| Foreign Keys | Application-level validation | Cross-database reality, denormalization |
| Date/Time Storage | Native types (DATE, TIMESTAMP) | Database optimization, UTC everywhere |
| FIRS Response Storage | Hybrid (key fields + JSONB) | Query performance, flexibility |
| Index Strategy | Strategic indexes (identifiers, status, dates) | Balanced read/write performance |
| Audit History | Field-level changes | Storage efficiency, granular audit |
| PostgreSQL vs SQLite | Separate schema files | Use best features of each |

---

## SCHEMA EVOLUTION PROCESS

### Minor Changes (Backward Compatible)

1. Add new column with DEFAULT value
2. Add new index
3. Add new table (no dependencies)

**Process**: Apply migration directly, no downtime needed

### Major Changes (Breaking)

1. Rename column
2. Change column type
3. Remove column
4. Change foreign key

**Process**:
1. Create migration script
2. Test in staging environment
3. Schedule maintenance window
4. Apply migration with backup

### Version Tracking

```sql
-- Track schema version in each database
CREATE TABLE schema_version (
    version VARCHAR(10) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);
```

---

## OPEN QUESTIONS FOR FUTURE PHASES

These questions do NOT block Phase 0b implementation but should be addressed in later phases:

1. **Partitioning Strategy**: When to partition `invoices` table by date? (>1M rows)
2. **Archive Strategy**: Move old invoices to archive database? (>7 years)
3. **Replication**: PostgreSQL streaming replication for Enterprise tier?
4. **Read Replicas**: Separate read-only database for reporting?
5. **Sharding**: Multi-tenant sharding strategy for SaaS offering?

---

**End of Document**
