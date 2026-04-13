# HELIUM CORE - ARCHITECTURE & IMPLEMENTATION SPECIFICATION

**Version:** 1.0
**Last Updated:** 2026-01-31
**Status:** CANONICAL IMPLEMENTATION SPECIFICATION

---

## DOCUMENT PURPOSE

This document outlines the complete architecture for Helium Core - the central processing engine that transforms raw invoice files into FIRS-compliant structured data and manages the entire invoice lifecycle.

**Target Audience**: Core service developers, system architects, DevOps engineers

**Prerequisites**: Read `HELIUM_OVERVIEW.md` for ecosystem context

**Scope**: Core service only (processing, transformation, database management, WebSocket events)

---

## SERVICE OVERVIEW

### Core's Mission

Core is the stateful processing engine at the heart of Helium. It transforms raw files into structured, FIRS-compliant invoices while managing master data (customers, inventory) and coordinating with Edge for external transmission.

**Key Metaphor**: If Relay is the "post office" and Edge is the "courier", Core is the "sorting and processing facility" that transforms mail into standardized packages ready for delivery.

### Core Responsibilities

**DOES**:
- Extract invoice, customer, and inventory data from files
- Execute customer-specific transformation scripts
- Enrich data via Prodeus certified APIs
- Generate IRN (Invoice Reference Number)
- Generate QR codes
- Manage master data (customers, inventory)
- Create and update invoice records
- Queue invoices to Edge service
- Broadcast WebSocket events to Float SDK
- Handle preview mode (bulk uploads)
- Apply user edits during finalization
- Enforce access control for CRUD operations

**DOES NOT**:
- Accept file uploads directly (Relay does this)
- Validate HMAC signatures (Relay does this)
- Submit to FIRS directly (Edge does this)
- Call external APIs except Prodeus certified servers

### Callers of Core

```
Core API Consumers:
├─ Relay Services (bulk, NAS, ERP)  → File processing requests
├─ Float SDK                         → Invoice/customer/inventory updates
├─ Edge Service                      → FIRS response updates
└─ HeartBeat                         → Health checks, metrics
```

---

## CORE ARCHITECTURE: THREE BROAD TASK CATEGORIES

### Task A: Extract & Process Data (+ Enrichment)

**Responsibilities**:
1. Parse files (PDF, Excel, CSV, XML, JSON)
2. Execute customer-specific transformation script
3. Extract 3 data types from every file:
   - **Invoice data** → Primary output, sent to Edge
   - **Customer data** → Master data, stored in customers.db
   - **Inventory data** → Master data, stored in inventory.db
4. Enrich data via external Prodeus APIs:
   - HSN code mapping
   - Product category mapping
   - Postal code validation
   - AI enrichment
5. Generate IRN and QR code
6. Generate preview data (for bulk uploads)
7. Append processed data to blob (7-day retention)

**Workers Involved**:
- FileParserWorker
- TransformationWorker
- EnrichmentWorker

---

### Task B: Create & Update Records

**Responsibilities**:
1. Write to `invoices.db` (create, update, delete)
2. Write to `customers.db` (extract from invoices, update master data)
3. Write to `inventory.db` (extract from invoices, update master data)
4. Write to `notifications.db` (alerts, approval requests)
5. Maintain referential integrity
6. Enforce access control for CRUD operations
7. Trigger database-level WebSocket broadcasts

**Workers Involved**:
- DatabaseWorker

**Database Triggers**: Automatic WebSocket event broadcasts on INSERT/UPDATE/DELETE

---

### Task C: Communicate & Hand Off to Edge

**Responsibilities**:
1. Write to `edge_queue` (queue entries for Edge service)
2. Call Edge API:
   - `POST /api/v1/submit` (submit invoice for FIRS processing)
   - Differentiate between SIGN, TRANSMIT, and PORTOBELLO tasks
3. Receive FIRS responses from Edge:
   - Update invoices.db with FIRS confirmation
   - Update invoice status

**Workers Involved**:
- EdgeCommunicationWorker

**Edge Tasks**:
- **SIGN**: Report invoice to FIRS (generate FIRS IRN), do NOT transmit to counterparty
- **TRANSMIT**: Exchange invoice with counterparty via FIRS
- **SIGN_AND_TRANSMIT**: Combined (default when portoBello=false)
- **PORTOBELLO**: Notify counterparty to register/provide details (future)

---

## WORKER ARCHITECTURE

### Worker Model: Modular Task-Based

Core uses a **Celery-style task queue** with specialized workers for different processing stages. This enables:
- Parallel processing of large batches (30K+ invoices)
- Modular transformation scripts
- Horizontal scaling
- Fault isolation

### Worker Types

```
Core Workers:

1. QueueScannerWorker (Continuous)
   - Polls core_queue every 60 seconds
   - Picks up missed entries (robustness)
   - Ensures no task slips through the cracks

2. FileParserWorker
   - Parses PDF, Excel, CSV, XML, JSON
   - Extracts raw structured data
   - Fast, lightweight, stateless

3. TransformationWorker
   - Executes customer-specific transformation script
   - Modularized into sub-tasks:
     ├─ extract_module (parsing logic)
     ├─ validate_module (pre-flight checks, shared with Relay)
     ├─ enrich_module (data enrichment)
     └─ finalize_module (IRN/QR generation)
   - Can spawn parallel sub-workers

4. EnrichmentWorker
   - Calls Prodeus certified APIs (parallel async calls):
     ├─ HSN code mapping
     ├─ Product category mapping
     ├─ Postal code validation
     └─ AI enrichment
   - Handles API failures gracefully

5. DatabaseWorker
   - Writes to invoices.db, customers.db, inventory.db, notifications.db
   - Batch inserts for large uploads (100 invoices per transaction)
   - Ensures transactional integrity
   - Triggers WebSocket broadcasts

6. EdgeCommunicationWorker
   - Writes to edge_queue
   - Calls Edge API with task type (SIGN, TRANSMIT, etc.)
   - Handles Edge API responses
   - Updates invoices.db with FIRS confirmations

7. PortoBelloWorker (Future)
   - Listens for customer detail updates in customers.db
   - Triggers retransmit when counterparty details complete
   - Queues PORTOBELLO notification tasks to Edge
```

### Worker Deployment

**Test/Standard**:
- In-process thread pool (Python threading or multiprocessing)
- Shared SQLite database
- Single Core process with worker threads

**Pro/Enterprise**:
- Celery with Redis/RabbitMQ backend
- Separate worker processes (horizontal scaling)
- Distributed task queue

---

## PROCESSING PIPELINE (8 STEPS)

### Step 1: FETCH

```
INPUT: queue_id (from Relay or internal queue scanner)

PROCESS:
1. Read entry from core_queue table
2. Extract: file_uuid, blob_path, immediate_processing flag
3. Fetch file from blob storage using blob_path

OUTPUT: Raw file data, metadata
```

**Worker**: QueueScannerWorker or API handler

---

### Step 2: PARSE

```
INPUT: Raw file data (PDF, Excel, CSV, XML, JSON)

PROCESS:
1. Detect file type from extension
2. Call appropriate parser:
   - PDF: pdfplumber or PyPDF2
   - Excel: openpyxl or pandas
   - CSV: pandas
   - XML: lxml or xmltodict
   - JSON: json.loads
3. Extract raw structured data

OUTPUT: Raw structured data (dict/list)
```

**Worker**: FileParserWorker

**Performance**: Stateless, can run in parallel for batch uploads

---

### Step 3: TRANSFORM

```
INPUT: Raw structured data, customer_id

PROCESS:
1. Load customer-specific transformation script from config.db
2. Execute transformation script:
   - extract_module: Parse raw data into invoice structure
   - validate_module: Pre-flight validation (shared with Relay)
   - Format data to FIRS-compliant structure
3. Extract 3 data types:
   - Invoice data
   - Customer data
   - Inventory data

OUTPUT: Structured invoice data, customer data, inventory data
```

**Worker**: TransformationWorker

**Transformation Script** (customer-specific):
- Stored in config.db as Python code
- Dynamically imported and executed
- Modularized for performance (see Transformation Scripts section)

---

### Step 4: ENRICH

```
INPUT: Structured invoice data

PROCESS:
1. Call Prodeus certified APIs (parallel async requests):
   - HSN code mapping (for invoice line items)
   - Product category mapping
   - Postal code validation (for addresses)
   - AI enrichment (missing fields, corrections)
2. Apply enrichment data to invoice structure
3. Flag enrichment sources (AUTO, MANUAL, AI)

OUTPUT: Enriched invoice data
```

**Worker**: EnrichmentWorker

**Allowed External APIs** (Prodeus certified only):
- `https://api.prodeus.com/hsn/map`
- `https://api.prodeus.com/category/classify`
- `https://api.prodeus.com/postal/validate`
- `https://api.prodeus.com/ai/enrich`

**All other external API calls**: Handled by Edge service

---

### Step 5: RESOLVE

```
INPUT: Enriched invoice data, customer data, inventory data

PROCESS:
1. Match customer data to customers.db:
   - Search by TIN, name, or address
   - Update existing customer or create new entry
   - Link invoice to customer_id
2. Match inventory data to inventory.db:
   - Search by SKU, product code, or name
   - Update existing inventory or create new entry
   - Link invoice line items to inventory_id

OUTPUT: Resolved invoice with customer_id and inventory_ids
```

**Worker**: DatabaseWorker (read/write to master data)

**Entity Resolution Logic**: Fuzzy matching on name/address if TIN not found

---

### Step 6: PORTO BELLO (Business Logic Gate)

```
INPUT: Resolved invoice, customer config

PROCESS:
1. Check customer config: portoBello flag
2. IF portoBello == true:
   - Generate IRN and QR code
   - Write to invoices.db (status="pending_counterparty_details")
   - Queue to Edge: SIGN task only (report to FIRS, do NOT transmit)
   - Queue to Edge: PORTOBELLO task (notify counterparty)
   - Register listener for customer detail updates (PortoBelloWorker)
   - DO NOT queue TRANSMIT task yet
3. IF portoBello == false:
   - Proceed to Step 7 (normal flow)

OUTPUT: Invoice with status flag
```

**Worker**: EdgeCommunicationWorker

**Porto Bello Implementation**: Deferred (architecture ready, not implemented yet)

---

### Step 7: BRANCH (Preview or Immediate Processing)

```
INPUT: immediate_processing flag (from original request)

IF immediate_processing == false (PREVIEW MODE - Bulk Upload):
  1. Generate preview data:
     - firs_invoices.json (FIRS-compliant invoice structure)
     - report.json (statistics: total_invoices, failed_count, red_flags)
     - customers.json (extracted customer data)
     - inventory.json (extracted inventory data)
     - failed_invoices.xlsx (list of failed invoices with errors)
     - fixed.pdf (corrected invoices, if applicable)
  2. Append preview data to blob as metadata (7-day retention)
  3. Return preview data to Relay
  4. DO NOT create invoices.db record yet
  5. Mark core_queue entry as "preview_ready"
  6. Wait for finalization request

IF immediate_processing == true (IMMEDIATE MODE - NAS, ERP):
  1. Generate IRN and QR code
  2. Create invoices.db record
  3. Proceed to Step 8
```

**Worker**: TransformationWorker + DatabaseWorker

**Preview Timeout**: 24 hours (cleanup job removes stale preview data)

---

### Step 8: FINALIZE

```
For Preview Mode (after user confirms):
INPUT: queue_id, user edits (optional)

PROCESS:
1. Fetch semi-processed data from blob metadata
2. Apply user edits:
   - invoice_edits: Update invoice fields (party_name, TIN, amounts, dates)
   - customer_edits: Update customer data
   - inventory_edits: Update inventory data
3. Re-validate edited data
4. Generate IRN and QR code
5. Create invoices.db record (status="draft")
6. Create/update customers.db entries
7. Create/update inventory.db entries
8. Queue to Edge (SIGN_AND_TRANSMIT or SIGN only if portoBello)
9. Call Edge API
10. Append final processed data to blob
11. Mark core_queue entry as "processed"
12. Delete core_queue entry
13. Trigger WebSocket broadcasts (invoice.created, customer.*, inventory.*)

For Immediate Mode:
PROCESS:
Same as above (steps 5-13), but skip edit application

OUTPUT: Invoice created, WebSocket events broadcast
```

**Worker**: DatabaseWorker + EdgeCommunicationWorker

---

## TRANSFORMATION SCRIPTS

### Storage & Execution

**Storage Location**: `config.db`

```sql
CREATE TABLE transformation_scripts (
    customer_id TEXT PRIMARY KEY,
    script_name TEXT NOT NULL,           -- e.g., "pikwik-transforma-v1.0.py"
    script_version TEXT NOT NULL,        -- e.g., "1.0.0"
    script_content TEXT NOT NULL,        -- Python code (full script)
    modules JSON,                        -- Modular breakdown (see below)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    sync_with_prodeus BOOLEAN DEFAULT FALSE
);
```

**Execution Model**:
```python
# Dynamic import and execution
import importlib.util

def execute_transformation(customer_id, raw_data):
    # Load script from config.db
    script_row = db.query("SELECT script_content FROM transformation_scripts WHERE customer_id = ?", customer_id)
    script_code = script_row["script_content"]

    # Create module dynamically
    spec = importlib.util.spec_from_loader("customer_transform", loader=None)
    module = importlib.util.module_from_spec(spec)

    # Execute script code in module namespace
    exec(script_code, module.__dict__)

    # Call transform function
    result = module.transform(raw_data)

    return result
```

---

### Modularization (Performance Optimization)

**Problem**: Single monolithic transformation script slow for 30K invoice batches

**Solution**: Break script into modules, execute in parallel

**Modular Structure**:
```
Transformation Script Modules:
├─ extract_module.py      (File parsing, raw data extraction)
├─ validate_module.py     (Pre-flight checks, SHARED with Relay)
├─ enrich_module.py       (Data enrichment, API calls)
└─ finalize_module.py     (IRN/QR generation, final formatting)
```

**Parallel Execution** (for bulk uploads):
```python
def process_bulk_batch(invoices, customer_id):
    # Load modular script
    modules = load_transformation_modules(customer_id)

    # Process invoices in batches of 100
    batch_size = 100
    invoice_batches = [invoices[i:i+batch_size] for i in range(0, len(invoices), batch_size)]

    # Spawn parallel workers (Celery tasks)
    tasks = []
    for batch in invoice_batches:
        task = transform_invoice_batch.delay(batch, modules)
        tasks.append(task)

    # Wait for all tasks to complete
    results = [task.get() for task in tasks]

    # Flatten results
    processed_invoices = [inv for batch_result in results for inv in batch_result]

    return processed_invoices
```

**Performance Gain**:
- Single-threaded: 30K invoices in ~10 minutes
- Parallel (10 workers): 30K invoices in ~1 minute

---

### Shared Validation Library with Relay

**Problem**: Relay needs to validate files during bulk upload preview (pre-flight checks), but validation logic is in Core's transformation script.

**Solution**: Extract validation module as shared library

**Shared Library Structure**:
```
helium-shared-lib/
└── validate.py

def validate_invoice_structure(invoice_data):
    """
    Validates invoice structure.
    Used by both Relay (pre-flight) and Core (processing).
    """
    # Check required fields
    if not invoice_data.get("invoice_number"):
        return False, "Missing invoice number"

    if not invoice_data.get("customer_tin"):
        return False, "Missing customer TIN"

    # Validate amounts
    if invoice_data.get("total_amount", 0) <= 0:
        return False, "Invalid total amount"

    return True, "Valid"
```

**Usage**:
- **Relay (pre-flight)**: Quick validation before queueing to Core
- **Core (processing)**: Full validation during transformation

---

## CORE API SPECIFICATION

### API Endpoint Summary

```
POST /api/v1/process              ← Primary processing endpoint (Relay, SDK)
POST /api/v1/retransmit           ← Retry failed FIRS submissions

DELETE /api/v1/invoice/{id}       ← Delete invoice
DELETE /api/v1/customer/{id}      ← Delete customer
DELETE /api/v1/inventory/{id}     ← Delete inventory

PUT /api/v1/invoice/{id}          ← Update invoice (with access control)
PUT /api/v1/customer/{id}         ← Update customer (with access control)
PUT /api/v1/inventory/{id}        ← Update inventory (with access control)

POST /api/v1/update               ← Generic update endpoint (Edge, SDK)

GET /api/v1/core_queue/status     ← Queue status for HeartBeat reconciliation (NEW)
GET /api/v1/health                ← Health check

WS /api/v1/events                 ← WebSocket for Float SDK
```

---

### POST /api/v1/process

**Purpose**: Primary processing endpoint for file processing, preview, and finalization

**Callers**: Relay services, Float SDK (for reprocessing)

**Request**:
```json
{
  "queue_id": "queue_123",                    // Required
  "immediate_processing": false,              // Default: false (preview mode)
  "edits": {                                  // Optional: User edits for finalization
    "invoice_edits": {
      "137861": {
        "accounting_supplier_party": {
          "party_name": "Updated Company Name",
          "tin": "12345678"
        }
      }
    },
    "customer_edits": {},
    "inventory_edits": {}
  }
}
```

**Response (Preview Mode)**:
```json
{
  "queue_id": "queue_123",
  "status": "preview_ready",
  "statistics": {
    "input_file_count": 1,
    "total_invoices": 1000,
    "valid_count": 995,
    "failed_count": 5,
    "duplicate_count": 0,
    "processing_time_seconds": 45.2,
    "total_revenue": 15000000.00,
    "total_tax": 1125000.00,
    "red_flags": [
      {
        "invoice_id": "110675",
        "error": "E402",
        "error_msg": "Missing supplier TIN",
        "severity": "error"
      }
    ]
  },
  "preview_data": {
    "firs_invoices_url": "/api/blob/550e8400-.../firs_invoices.json",
    "report_url": "/api/blob/550e8400-.../report.json",
    "customers_url": "/api/blob/550e8400-.../customers.json",
    "inventory_url": "/api/blob/550e8400-.../inventory.json",
    "failed_invoices_url": "/api/blob/550e8400-.../failed_invoices.xlsx",
    "fixed_pdf_url": "/api/blob/550e8400-.../fixed.pdf"
  }
}
```

**Response (Immediate Mode or Finalized)**:
```json
{
  "queue_id": "queue_123",
  "status": "finalized",
  "statistics": {
    "invoices_processed": 995,
    "invoices_created": 995,
    "invoices_failed": 5,
    "customers_created": 50,
    "customers_updated": 200,
    "inventory_created": 100,
    "inventory_updated": 300,
    "edge_queue_entries": 995,
    "processing_time_seconds": 52.8
  }
}
```

**Error Response (400 Bad Request)**:
```json
{
  "status": "error",
  "error_code": "EDIT_VALIDATION_FAILED",
  "message": "User edits failed validation",
  "details": [
    {
      "invoice_id": "137861",
      "field": "accounting_supplier_party.tin",
      "error": "TIN must be 8 digits"
    }
  ]
}
```

**Processing Logic**:
```
IF edits provided AND immediate_processing == false:
  → Finalization flow (apply edits, create records)

ELSE IF immediate_processing == false:
  → Preview flow (generate preview data, return to Relay)

ELSE:
  → Immediate flow (process and create records immediately)
```

---

### POST /api/v1/retransmit

**Purpose**: Retry failed FIRS submissions

**Callers**: Float SDK (manual retry), PortoBelloWorker (automatic retry)

**Request**:
```json
{
  "invoice_ids": ["INV_001", "INV_002"]
}
```

**Response**:
```json
{
  "status": "queued",
  "retransmitted_count": 2,
  "edge_queue_entries": ["edge_queue_456", "edge_queue_457"]
}
```

---

### DELETE /api/v1/invoice/{invoice_id}

**Purpose**: Delete invoice (soft delete)

**Access Control**: Requires `invoice.delete` permission

**Response**:
```json
{
  "status": "deleted",
  "invoice_id": "INV_001",
  "deleted_at": "2026-01-31T10:00:00Z"
}
```

**Database Effect**: Sets `deleted=true`, `deleted_at=timestamp` in invoices.db

---

### PUT /api/v1/invoice/{invoice_id}

**Purpose**: Update invoice

**Access Control**:
- General updates: Requires `invoice.update` permission
- Status changes: Requires `invoice.update_status` permission
- Mark as paid: Requires `invoice.mark_as_paid` permission

**Request**:
```json
{
  "status": "PAID",
  "payment_date": "2026-01-30",
  "user_id": "user_123"
}
```

**Response**:
```json
{
  "status": "updated",
  "invoice_id": "INV_001",
  "changes": {
    "status": {"old": "PENDING", "new": "PAID"},
    "payment_date": "2026-01-30"
  }
}
```

**WebSocket Event**: Triggers `invoice.updated` event

---

### POST /api/v1/update

**Purpose**: Generic update endpoint for Edge responses and SDK updates

**Authentication**: Caller identity determines allowed operations

**Request (from Edge)**:
```json
{
  "source": "edge",
  "invoice_id": "INV_001",
  "updates": {
    "firs_status": "VALIDATED",
    "firs_confirmation": "CONF_123",
    "firs_irn": "FIRS_IRN_456"
  }
}
```

**Request (from SDK)**:
```json
{
  "source": "sdk",
  "user_id": "user_123",
  "invoice_id": "INV_001",
  "updates": {
    "payment_status": "PAID"
  }
}
```

**Response**:
```json
{
  "status": "updated",
  "invoice_id": "INV_001"
}
```

**Access Control**:
- Edge requests: No permission check (trusted service)
- SDK requests: Permission check based on user_id

---

### GET /api/v1/core_queue/status

**Purpose**: Return status of all core_queue entries for HeartBeat reconciliation

**Callers**: HeartBeat service (during hourly reconciliation)

**Authentication**: Bearer token (HeartBeat API token)

**Response**:
```json
[
  {
    "queue_id": "queue_123",
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "status": "processed",
    "created_at": "2026-01-31T08:00:00Z",
    "updated_at": "2026-01-31T08:05:23Z",
    "processed_at": "2026-01-31T08:05:23Z"
  },
  {
    "queue_id": "queue_124",
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440001",
    "blob_path": "/files_blob/550e8400-...-data.csv",
    "status": "queued",
    "created_at": "2026-01-31T09:45:00Z",
    "updated_at": "2026-01-31T09:45:00Z",
    "processed_at": null
  },
  {
    "queue_id": "queue_125",
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440002",
    "blob_path": "/files_blob/550e8400-...-invoice.xml",
    "status": "processing",
    "created_at": "2026-01-31T09:50:00Z",
    "updated_at": "2026-01-31T09:51:15Z",
    "processed_at": null
  }
]
```

**Use Case**:
- HeartBeat's hourly reconciliation job queries this endpoint
- Cross-verifies Core's processing status with blob_entries records
- Detects stale processing (status="queued" or "processing" for >1 hour)
- Updates blob_entries.status to "finalized" when Core shows "processed"

**Implementation**:
```python
@app.get("/api/v1/core_queue/status")
async def get_core_queue_status(auth: BearerToken = Depends(verify_heartbeat_token)):
    """Return all core_queue entries for reconciliation"""
    entries = db.execute("SELECT * FROM core_queue ORDER BY created_at DESC")
    return [
        {
            "queue_id": e["queue_id"],
            "blob_uuid": e["blob_uuid"],
            "blob_path": e["blob_path"],
            "status": e["status"],
            "created_at": e["created_at_iso"],
            "updated_at": e["updated_at_iso"],
            "processed_at": e.get("processed_at_iso")
        }
        for e in entries
    ]
```

---

### GET /api/v1/health

**Purpose**: Health check

**Response**:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "services": {
    "database": "healthy",
    "blob_storage": "healthy",
    "edge_api": "healthy",
    "prodeus_apis": "degraded"
  },
  "workers": {
    "queue_scanner": "running",
    "file_parser": "running",
    "transformation": "running",
    "enrichment": "degraded",
    "database": "running",
    "edge_communication": "running"
  },
  "queue_depth": {
    "core_queue": 12,
    "edge_queue": 5
  },
  "timestamp": "2026-01-31T10:00:00Z"
}
```

---

### WS /api/v1/events

**Purpose**: WebSocket endpoint for Float SDK real-time sync

**Protocol**: WebSocket (ws:// for Test/Standard, wss:// for Pro/Enterprise)

**Authentication**: JWT token in handshake

**Event Format**: Aligned with SDK WORKSTREAM_3 EVENT_SCHEMAS.md

**Events Broadcast**:
```
Invoice Events:
- invoice.created
- invoice.updated
- invoice.deleted

Customer Events:
- customer.created
- customer.updated
- customer.deleted

Inventory Events:
- inventory.created
- inventory.updated
- inventory.deleted

Notification Events:
- notification.created
- notification.updated
- notification.read

Bulk Sync:
- bulk.sync (full database sync)
```

**Example Event**:
```json
{
  "event_id": "evt_550e8400-...",
  "event_type": "invoice.created",
  "invoice_id": "INV_001",
  "timestamp": "2026-01-31T10:00:00Z",
  "source": "local",
  "version": 1,
  "user_id": "user_123",
  "data": {
    "invoice_id": "INV_001",
    "invoice_number": "INV-2026-001",
    "customer_id": "cust_123",
    "customer_name": "Acme Corp",
    "total_amount": 5000.00,
    "status": "draft",
    "created_at": "2026-01-31T10:00:00Z"
  }
}
```

**Implementation**: Database triggers call WebSocket broadcast function

---

## DATABASE SCHEMAS

### core_queue Table

```sql
CREATE TABLE core_queue (
    queue_id TEXT PRIMARY KEY,
    file_uuid TEXT NOT NULL UNIQUE,
    blob_path TEXT NOT NULL,
    original_filename TEXT,
    source TEXT NOT NULL,                    -- 'relay-bulk-1', 'relay-nas-1', etc.
    immediate_processing BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'preview_ready', 'completed', 'failed')),
    created_at_unix INTEGER NOT NULL,
    created_at_iso TEXT NOT NULL,
    processed_at_unix INTEGER,
    processed_at_iso TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    -- Preview mode support
    preview_data_blob_path TEXT,             -- Path to appended preview data in blob
    preview_expires_at_unix INTEGER,         -- 24-hour expiry
    finalized BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_core_queue_status ON core_queue(status, created_at_unix);
CREATE INDEX idx_core_queue_source ON core_queue(source);
CREATE INDEX idx_core_queue_preview_expiry ON core_queue(preview_expires_at_unix) WHERE status = 'preview_ready';
```

---

### invoices.db Table

```sql
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY,
    irn TEXT UNIQUE NOT NULL,                -- Invoice Reference Number (Core-generated)
    firs_irn TEXT UNIQUE,                    -- FIRS-issued IRN (from Edge after submission)
    invoice_number TEXT NOT NULL,
    business_id TEXT,
    issue_date TEXT NOT NULL,
    invoice_type_code TEXT NOT NULL,         -- '380', '381', '384'
    document_currency_code TEXT DEFAULT 'NGN',
    tax_currency_code TEXT DEFAULT 'NGN',

    -- Supplier/Issuer
    supplier_party_name TEXT NOT NULL,
    supplier_tin TEXT NOT NULL,
    supplier_email TEXT,
    supplier_address JSON,

    -- Customer/Buyer (nullable for porto bello pending cases)
    customer_id TEXT,                        -- Foreign key to customers.db
    customer_party_name TEXT,
    customer_tin TEXT,
    customer_email TEXT,
    customer_address JSON,

    -- Financial totals
    line_extension_amount REAL NOT NULL,
    tax_exclusive_amount REAL NOT NULL,
    tax_inclusive_amount REAL NOT NULL,
    payable_amount REAL NOT NULL,
    tax_amount_total REAL NOT NULL,
    discount_amount REAL DEFAULT 0.0,

    -- Invoice lines (JSON array)
    invoice_lines JSON NOT NULL,

    -- Tax breakdown (JSON)
    tax_total JSON NOT NULL,

    -- Payment info
    payment_means_code TEXT,                 -- '10', '30', '42', etc.
    payment_status TEXT CHECK(payment_status IN ('PENDING', 'PAID', 'PARTIAL', 'OVERDUE')),
    payment_due_date TEXT,
    payment_date TEXT,

    -- FIRS status
    firs_status TEXT CHECK(firs_status IN ('DRAFT', 'SIGNED', 'TRANSMITTED', 'VALIDATED', 'REJECTED', 'ERROR')),
    firs_confirmation TEXT,
    firs_error_message TEXT,

    -- QR code
    qr_code_base64 TEXT,                     -- Base64-encoded QR code image

    -- Metadata
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'pending_counterparty_details', 'submitted_to_firs', 'firs_validated', 'paid', 'archived')),
    source_file_uuid TEXT,                   -- Reference to original blob
    created_at TEXT NOT NULL,
    updated_at TEXT,
    created_by TEXT,
    updated_by TEXT,
    deleted BOOLEAN DEFAULT FALSE,
    deleted_at TEXT,

    -- Porto Bello support
    portobello_pending BOOLEAN DEFAULT FALSE,
    portobello_notified_at TEXT,

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE INDEX idx_invoices_irn ON invoices(irn);
CREATE INDEX idx_invoices_firs_irn ON invoices(firs_irn);
CREATE INDEX idx_invoices_status ON invoices(status);
CREATE INDEX idx_invoices_firs_status ON invoices(firs_status);
CREATE INDEX idx_invoices_customer_id ON invoices(customer_id);
CREATE INDEX idx_invoices_issue_date ON invoices(issue_date);
CREATE INDEX idx_invoices_deleted ON invoices(deleted);
```

---

### customers.db Table

```sql
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    party_name TEXT NOT NULL,
    tin TEXT UNIQUE,
    email TEXT,
    phone TEXT,
    postal_address JSON,                     -- { street_name, city_name, country }

    -- Metadata
    created_at TEXT NOT NULL,
    updated_at TEXT,
    created_from_invoice_id TEXT,            -- First invoice that created this customer
    invoice_count INTEGER DEFAULT 0,         -- Number of invoices for this customer
    total_revenue REAL DEFAULT 0.0,          -- Cumulative revenue

    -- Porto Bello support
    details_complete BOOLEAN DEFAULT FALSE,  -- True when TIN, address, email all present
    details_completed_at TEXT,               -- Timestamp when details became complete

    FOREIGN KEY (created_from_invoice_id) REFERENCES invoices(invoice_id)
);

CREATE INDEX idx_customers_tin ON customers(tin);
CREATE INDEX idx_customers_party_name ON customers(party_name);
CREATE INDEX idx_customers_details_complete ON customers(details_complete);
```

**Database Trigger** (Porto Bello):
```sql
CREATE TRIGGER customer_details_complete_trigger
AFTER UPDATE ON customers
WHEN NEW.tin IS NOT NULL AND NEW.email IS NOT NULL AND NEW.postal_address IS NOT NULL
BEGIN
    UPDATE customers SET details_complete = TRUE, details_completed_at = datetime('now')
    WHERE customer_id = NEW.customer_id;

    -- Notify PortoBelloWorker to retransmit pending invoices for this customer
    -- (Implementation: Insert into portobello_queue or use event system)
END;
```

---

### inventory.db Table

```sql
CREATE TABLE inventory (
    inventory_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sellers_item_identification TEXT,        -- SKU or internal product code
    standard_item_identification_id TEXT,    -- Barcode, EAN, GTIN
    standard_item_identification_id_scheme_id TEXT, -- '0160' for GTIN

    -- HSN classification
    hsn_code TEXT,
    hsn_source TEXT CHECK(hsn_source IN ('AUTO', 'MANUAL', 'AI')),

    -- Product category
    category TEXT,
    category_source TEXT CHECK(category_source IN ('AUTO', 'MANUAL', 'AI')),

    -- Tax info
    default_tax_percent REAL DEFAULT 7.5,
    default_tax_category_id TEXT DEFAULT 'S',

    -- Metadata
    created_at TEXT NOT NULL,
    updated_at TEXT,
    created_from_invoice_id TEXT,            -- First invoice that created this inventory
    invoice_count INTEGER DEFAULT 0,         -- Number of invoices with this product

    FOREIGN KEY (created_from_invoice_id) REFERENCES invoices(invoice_id)
);

CREATE INDEX idx_inventory_sellers_item_id ON inventory(sellers_item_identification);
CREATE INDEX idx_inventory_standard_item_id ON inventory(standard_item_identification_id);
CREATE INDEX idx_inventory_hsn_code ON inventory(hsn_code);
```

---

### notifications.db Table

```sql
CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    notification_type TEXT NOT NULL CHECK(notification_type IN ('info', 'warning', 'error', 'approval_request')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,

    -- Context
    related_invoice_id TEXT,
    related_customer_id TEXT,
    related_inventory_id TEXT,

    -- User targeting
    target_user_id TEXT,                     -- Specific user, or NULL for all users

    -- Status
    read BOOLEAN DEFAULT FALSE,
    read_at TEXT,
    read_by TEXT,
    dismissed BOOLEAN DEFAULT FALSE,
    dismissed_at TEXT,

    -- Approval workflow (Porto Bello, etc.)
    requires_approval BOOLEAN DEFAULT FALSE,
    approved BOOLEAN,
    approved_at TEXT,
    approved_by TEXT,

    -- Metadata
    created_at TEXT NOT NULL,
    expires_at TEXT,                         -- Notifications can expire

    FOREIGN KEY (related_invoice_id) REFERENCES invoices(invoice_id),
    FOREIGN KEY (related_customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (related_inventory_id) REFERENCES inventory(inventory_id)
);

CREATE INDEX idx_notifications_target_user ON notifications(target_user_id, read);
CREATE INDEX idx_notifications_type ON notifications(notification_type);
CREATE INDEX idx_notifications_created_at ON notifications(created_at);
```

---

### user_permissions Table (config.db)

```sql
CREATE TABLE user_permissions (
    user_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (user_id, permission)
);

CREATE INDEX idx_user_permissions_user_id ON user_permissions(user_id);
```

**Permissions**:
```
Invoice Permissions:
- invoice.create
- invoice.update
- invoice.update_status
- invoice.mark_as_paid
- invoice.delete
- invoice.accept_inbound (for B2B inbound invoices)

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

---

## ACCESS CONTROL IMPLEMENTATION

### Permission Check Function

```python
def has_permission(user_id: str, permission: str) -> bool:
    """
    Check if user has specific permission.
    Admin users bypass all checks.
    """
    # Check for admin permission (grants all access)
    admin_result = db.query(
        "SELECT 1 FROM user_permissions WHERE user_id = ? AND permission = 'system.admin'",
        user_id
    )
    if admin_result:
        return True

    # Check for specific permission
    result = db.query(
        "SELECT 1 FROM user_permissions WHERE user_id = ? AND permission = ?",
        user_id, permission
    )
    return bool(result)

def require_permission(user_id: str, permission: str):
    """
    Decorator/wrapper to enforce permission checks.
    Raises PermissionDenied if user lacks permission.
    """
    if not has_permission(user_id, permission):
        raise PermissionDenied(f"User {user_id} lacks permission: {permission}")
```

### Usage Example

```python
def update_invoice_status(invoice_id: str, new_status: str, user_id: str):
    # Basic permission check
    require_permission(user_id, "invoice.update_status")

    # Specific permission for marking as paid
    if new_status == "PAID":
        require_permission(user_id, "invoice.mark_as_paid")

    # Execute update
    db.execute(
        "UPDATE invoices SET status = ?, updated_at = ?, updated_by = ? WHERE invoice_id = ?",
        new_status, datetime.utcnow().isoformat(), user_id, invoice_id
    )

    # Trigger WebSocket event
    broadcast_invoice_updated_event(invoice_id)
```

---

## WEBSOCKET IMPLEMENTATION

### Database Triggers for Auto-Broadcast

**invoices.db Triggers**:
```sql
CREATE TRIGGER invoice_created_trigger
AFTER INSERT ON invoices
BEGIN
    SELECT broadcast_event('invoice.created', NEW.invoice_id, json_object(
        'invoice_id', NEW.invoice_id,
        'invoice_number', NEW.invoice_number,
        'customer_name', NEW.customer_party_name,
        'total_amount', NEW.payable_amount,
        'status', NEW.status,
        'created_at', NEW.created_at
    ));
END;

CREATE TRIGGER invoice_updated_trigger
AFTER UPDATE ON invoices
BEGIN
    SELECT broadcast_event('invoice.updated', NEW.invoice_id, json_object(
        'changes', json_object(
            'status', json_object('old', OLD.status, 'new', NEW.status)
        ),
        'full_invoice', json_object(
            'invoice_id', NEW.invoice_id,
            'status', NEW.status
        )
    ));
END;

CREATE TRIGGER invoice_deleted_trigger
AFTER UPDATE ON invoices
WHEN NEW.deleted = TRUE AND OLD.deleted = FALSE
BEGIN
    SELECT broadcast_event('invoice.deleted', NEW.invoice_id, json_object(
        'deleted_at', NEW.deleted_at,
        'deletion_type', 'soft'
    ));
END;
```

**Note**: `broadcast_event()` is a custom function that interfaces with WebSocket server.

### WebSocket Server Implementation

```python
import asyncio
import websockets
import json

# Connected clients
connected_clients = set()

async def websocket_handler(websocket, path):
    """Handle WebSocket connections from Float SDK."""
    # Authenticate
    token = await websocket.recv()
    user_id = validate_jwt(token)
    if not user_id:
        await websocket.close(code=1008, reason="Invalid token")
        return

    # Register client
    connected_clients.add(websocket)

    try:
        # Keep connection alive
        async for message in websocket:
            # Handle client messages (e.g., subscription filters)
            pass
    finally:
        # Unregister client
        connected_clients.remove(websocket)

async def broadcast_event(event_type: str, entity_id: str, data: dict):
    """
    Broadcast event to all connected clients.
    Called by database triggers via custom function.
    """
    event = {
        "event_id": generate_event_id(),
        "event_type": event_type,
        "invoice_id": entity_id if event_type.startswith("invoice.") else None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": "local",
        "version": get_next_version(entity_id),
        "user_id": get_current_user_id(),
        "data": data
    }

    # Broadcast to all connected clients
    if connected_clients:
        await asyncio.gather(
            *[client.send(json.dumps(event)) for client in connected_clients],
            return_exceptions=True
        )

# Start WebSocket server
async def start_websocket_server():
    async with websockets.serve(websocket_handler, "0.0.0.0", 8080, path="/api/v1/events"):
        await asyncio.Future()  # Run forever
```

---

## PORTO BELLO WORKFLOW (FUTURE)

### Overview

Porto Bello is a business logic mode where invoices are **signed** (reported to FIRS) but **NOT transmitted** to the counterparty until their details are complete in the system.

**Use Case**: Issuer wants to report revenue to FIRS immediately but doesn't have buyer's TIN yet. System holds transmission until buyer registers and provides TIN.

### Implementation (Architecture Ready)

**Configuration** (per customer in config.db):
```sql
ALTER TABLE customers ADD COLUMN portobello_enabled BOOLEAN DEFAULT FALSE;
```

**Workflow**:
```
1. Invoice processed with portobello_enabled=true:
   - Core generates IRN and QR code
   - Core writes to invoices.db (status="pending_counterparty_details", portobello_pending=true)
   - Core queues to Edge:
     ├─ Task 1: SIGN (report to FIRS, get FIRS IRN)
     └─ Task 2: PORTOBELLO (notify counterparty via WhatsApp/Email/CRM)
   - DO NOT queue TRANSMIT task

2. Edge executes SIGN task:
   - Edge submits to FIRS API (sign endpoint)
   - FIRS returns FIRS IRN + confirmation
   - Edge updates invoices.db (firs_status="SIGNED", firs_irn="...")

3. Edge executes PORTOBELLO task:
   - Edge sends WhatsApp message to counterparty (if phone available)
   - Edge sends email to counterparty (if email available)
   - Edge posts to internal CRM (if integrated)
   - Edge posts to Vendor Management System (if integrated)

4. PortoBelloWorker (in Core) listens for customer detail updates:
   - Database trigger on customers.db (when details_complete becomes TRUE)
   - Trigger fires when TIN + email + address all present
   - PortoBelloWorker finds all pending invoices for this customer
   - PortoBelloWorker queues TRANSMIT tasks to Edge for each pending invoice

5. Edge executes TRANSMIT tasks:
   - Edge submits invoices to FIRS API (exchange endpoint)
   - FIRS transmits to counterparty
   - Edge updates invoices.db (status="transmitted", firs_status="TRANSMITTED")
```

**PortoBelloWorker** (pseudocode):
```python
class PortoBelloWorker:
    def run(self):
        # Listen for customer_details_complete events
        while True:
            event = wait_for_customer_details_complete_event()
            customer_id = event["customer_id"]

            # Find pending invoices for this customer
            pending_invoices = db.query(
                "SELECT invoice_id FROM invoices WHERE customer_id = ? AND portobello_pending = TRUE",
                customer_id
            )

            # Queue TRANSMIT tasks to Edge for each invoice
            for invoice in pending_invoices:
                edge_queue.enqueue({
                    "task_type": "TRANSMIT",
                    "invoice_id": invoice["invoice_id"]
                })

                # Call Edge API
                edge_api.submit({
                    "task_type": "TRANSMIT",
                    "invoice_id": invoice["invoice_id"]
                })

            # Update invoices
            db.execute(
                "UPDATE invoices SET portobello_pending = FALSE WHERE customer_id = ?",
                customer_id
            )
```

**Status**: Deferred (not for MVP), but architecture supports it.

---

## SCALING STRATEGY

### Horizontal Scaling (Pro/Enterprise)

**Worker Scaling**:
```
Core Workers (Celery with RabbitMQ/Redis):
├─ QueueScannerWorker: 1 instance (singleton, uses distributed lock)
├─ FileParserWorker: 5-10 instances (stateless, parallel)
├─ TransformationWorker: 10-20 instances (CPU-intensive, parallel)
├─ EnrichmentWorker: 5 instances (I/O-bound, parallel API calls)
├─ DatabaseWorker: 3-5 instances (write-heavy, transaction coordination)
└─ EdgeCommunicationWorker: 3 instances (I/O-bound, API calls)
```

**Batch Processing**:
- Large bulk uploads (30K invoices): Split into batches of 100 invoices
- Each batch processed by separate TransformationWorker instance
- Parallel execution: 10 workers × 100 invoices = 1000 invoices in parallel

**Database Scaling**:
- **Test/Standard**: SQLite (single file, up to 5K invoices/day)
- **Pro**: SQLite with WAL mode (up to 50K invoices/day)
- **Enterprise**: PostgreSQL (distributed, 500K+ invoices/day)

### Infrastructure Scaling Considerations (Deployment-Specific)

**Network Bandwidth**:
- **Context**: Multiple relay instances uploading large files simultaneously (e.g., 100GB total)
- **Bottleneck**: Network I/O between Relay → MinIO, Core → MinIO
- **Consideration**: Pro/Enterprise deployments should plan for 1 Gbps+ network links
- **Mitigation**: MinIO distributed mode with network load balancing
- **Ops Planning**: Monitor network utilization metrics, upgrade links as needed

**Blob Storage IOPS**:
- **Context**: Thousands of small files/second (write IOPS) or concurrent reads (read IOPS)
- **Bottleneck**: MinIO disk I/O limits, filesystem performance
- **Consideration**: SSD vs HDD choice, RAID configuration, MinIO distributed mode
- **Mitigation**:
  - Use SSDs for MinIO storage (10K+ IOPS vs 100-200 IOPS for HDD)
  - Configure MinIO with distributed erasure coding for parallel I/O
  - Separate volumes for files_blob (high IOPS) vs metadata (lower IOPS)
- **Ops Planning**: Monitor MinIO IOPS metrics, provision adequate storage infrastructure

**Task Queue Depth**:
- **Context**: RabbitMQ/Redis queue capacity when thousands of tasks are queued
- **Bottleneck**: Memory for queue storage, consumer throughput lagging producer throughput
- **Consideration**: Max queue depth policies, backpressure mechanisms
- **Mitigation**:
  - Configure RabbitMQ max queue length (e.g., 100K messages)
  - Enable consumer backpressure (reject new tasks if queue > 80% full)
  - Scale workers horizontally when queue depth exceeds thresholds
- **Ops Planning**: Monitor queue depth metrics, alert when queue growth exceeds processing capacity

**Note**: These are infrastructure/deployment concerns rather than application architecture. Detailed capacity planning and tuning guides will be provided in deployment documentation.

---

## IDEMPOTENCE DESIGN

### Invoice Processing Idempotence

**Problem**: Same file processed multiple times (queue retry, network failure, etc.)

**Solution**: Use FIRS IRN as idempotence key

**Implementation**:
```python
def create_invoice(invoice_data):
    # Check if invoice with same IRN already exists
    existing = db.query("SELECT invoice_id FROM invoices WHERE irn = ?", invoice_data["irn"])

    if existing:
        # Invoice already processed - return existing record (idempotent)
        return existing["invoice_id"]

    # Create new invoice
    db.execute("INSERT INTO invoices (...) VALUES (...)", invoice_data)
    return invoice_data["invoice_id"]
```

### API Idempotence

**POST /api/v1/process**:
- If `queue_id` already processed: Return cached response (no reprocessing)
- If `queue_id` in progress: Wait for completion, return result
- If `queue_id` new: Process normally

**Implementation**:
```python
def process_request(queue_id):
    # Check if already processed
    queue_entry = db.query("SELECT status FROM core_queue WHERE queue_id = ?", queue_id)

    if queue_entry["status"] == "completed":
        # Return cached response
        return get_cached_response(queue_id)

    if queue_entry["status"] == "processing":
        # Wait for completion (poll or event-driven)
        return wait_for_completion(queue_id)

    # Process new request
    return process_queue_entry(queue_id)
```

---

## MEMORY CLEANUP

### Preview Data Cleanup

**7-Day Blob Appendages**:
```python
# Cleanup job (runs daily)
def cleanup_preview_data():
    # Find expired preview data
    expired_blobs = db.query(
        "SELECT preview_data_blob_path FROM core_queue WHERE preview_expires_at_unix < ? AND status = 'preview_ready'",
        int(time.time())
    )

    # Delete from blob storage
    for blob in expired_blobs:
        blob_storage.delete_metadata(blob["preview_data_blob_path"])

    # Update queue entries
    db.execute("DELETE FROM core_queue WHERE preview_expires_at_unix < ? AND status = 'preview_ready'", int(time.time()))
```

**24-Hour Preview Expiry**:
- Preview data in blob metadata expires after 24 hours
- Cleanup job runs every 6 hours
- User must finalize within 24 hours or re-upload

### Core Queue Cleanup

**Completed Entries (UPDATED - Delayed Deletion Pattern)**:
```python
# Mark as processed, DO NOT DELETE immediately
def finalize_queue_entry(queue_id):
    # ... processing logic ...

    # Update status (DO NOT DELETE)
    db.execute("""
        UPDATE core_queue
        SET status = ?, processed_at = ?, updated_at = ?
        WHERE queue_id = ?
    """, ("processed", now(), now(), queue_id))

    # ✅ Do NOT delete here
    # HeartBeat cleanup job will delete after 24 hours
```

**Why Delayed Deletion**:
- HeartBeat's hourly reconciliation needs to verify processing happened
- Keeps audit trail for 24 hours
- Provides recovery window if Core crashes
- Minimal storage cost (queue entries are small)

**Cleanup Job (Owned by HeartBeat)**:
- Runs every 1 hour (after reconciliation)
- Deletes core_queue entries where `status = "processed"` AND `updated_at < NOW() - 24 hours`
- Logs deletions to audit.db

**Failed Entries**:
- Retained for 30 days for debugging
- Cleanup job deletes after 30 days

### In-Memory Worker State

**Celery Worker Memory Management**:
```python
# celery.conf
task_acks_late = True           # Acknowledge tasks only after completion
worker_prefetch_multiplier = 1  # Fetch one task at a time (prevent memory bloat)
task_soft_time_limit = 300      # 5-minute soft timeout
task_time_limit = 600           # 10-minute hard timeout
```

**Worker Restart**:
- Workers automatically restart after processing 1000 tasks (prevent memory leaks)

---

## ERROR HANDLING

### Error Categories

**1. Parsing Errors** (FileParserWorker):
```python
try:
    data = parse_pdf(file_path)
except PDFParseError as e:
    log_error("PDF parsing failed", file_uuid, str(e))
    mark_queue_entry_failed(queue_id, f"PDF parsing failed: {e}")
    return
```

**2. Transformation Errors** (TransformationWorker):
```python
try:
    result = execute_transformation_script(customer_id, raw_data)
except Exception as e:
    log_error("Transformation script failed", file_uuid, str(e))
    mark_queue_entry_failed(queue_id, f"Transformation failed: {e}")
    return
```

**3. Enrichment Errors** (EnrichmentWorker):
```python
try:
    hsn_code = call_prodeus_hsn_api(product_name)
except ProduesAPIError as e:
    # Graceful degradation: Continue without HSN code, flag as manual
    log_warning("HSN API unavailable", product_name, str(e))
    hsn_code = None
    hsn_source = "MANUAL"  # User must fill manually
```

**4. Database Errors** (DatabaseWorker):
```python
try:
    db.execute("INSERT INTO invoices (...) VALUES (...)", invoice_data)
except IntegrityError as e:
    # Duplicate invoice (idempotence)
    log_info("Duplicate invoice detected", invoice_data["irn"])
    return existing_invoice_id
except DatabaseError as e:
    # Serious error - retry with backoff
    log_error("Database write failed", invoice_id, str(e))
    retry_with_backoff(write_invoice, invoice_data, max_retries=3)
```

**5. Edge Communication Errors** (EdgeCommunicationWorker):
```python
try:
    edge_api.submit(invoice_id, task_type="SIGN_AND_TRANSMIT")
except EdgeAPIUnavailable as e:
    # Edge is down - invoice safely in edge_queue, will retry
    log_warning("Edge API unavailable", invoice_id, str(e))
    # Edge will poll edge_queue later
```

### Retry Strategy

**Exponential Backoff**:
```python
def retry_with_backoff(func, *args, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func(*args)
        except Exception as e:
            if attempt == max_retries - 1:
                raise  # Final attempt failed
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(wait_time)
```

**Circuit Breaker** (for Prodeus APIs):
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.last_failure_time = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def call(self, func, *args):
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError("Circuit breaker open")

        try:
            result = func(*args)
            if self.state == "HALF_OPEN":
                self.state = "CLOSED"
                self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
            raise
```

---

## AUDIT LOGS & EVENT DEFINITIONS

### Audit Events

**Processing Events**:
```
- file.parsing_started
- file.parsing_completed
- file.parsing_failed
- transformation.started
- transformation.completed
- transformation.failed
- enrichment.started
- enrichment.completed
- enrichment.api_failed (graceful degradation)
```

**Database Events**:
```
- invoice.created
- invoice.updated
- invoice.deleted
- customer.created
- customer.updated
- customer.deleted
- inventory.created
- inventory.updated
- inventory.deleted
```

**Edge Communication Events**:
```
- edge.queued
- edge.api_called
- edge.api_failed
- edge.response_received
```

**Access Control Events**:
```
- permission.denied (failed access control check)
- permission.granted
```

### Audit Log Format

```json
{
  "timestamp": "2026-01-31T10:00:00Z",
  "service": "core",
  "worker": "transformation-worker-3",
  "event_type": "transformation.completed",
  "user_id": "user_123",
  "file_uuid": "550e8400-...",
  "queue_id": "queue_123",
  "invoice_id": "INV_001",
  "details": {
    "customer_id": "execujet-ng",
    "transformation_script": "pikwik-transforma-v1.0.py",
    "invoices_processed": 995,
    "processing_time_seconds": 45.2
  }
}
```

**TODO**: Define complete audit event taxonomy and monitoring strategy.

---

## DEPLOYMENT GUIDE

### Docker Configuration (Pro/Enterprise)

**Dockerfile**:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY config/ ./config/

# Expose API port
EXPOSE 8080

# Run Core service
CMD ["python", "-m", "src.core.main"]
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  core-api:
    build: ./core
    ports:
      - "8080:8080"
    environment:
      - ENVIRONMENT=production
      - CONFIG_PATH=/config/core-config.json
    depends_on:
      - redis
      - rabbitmq
    networks:
      - helium-network

  core-worker-transformation:
    build: ./core
    command: celery -A src.core.workers.transformation worker --loglevel=info --concurrency=10
    environment:
      - CELERY_BROKER_URL=amqp://rabbitmq:5672
    depends_on:
      - rabbitmq
      - redis
    networks:
      - helium-network

  core-worker-enrichment:
    build: ./core
    command: celery -A src.core.workers.enrichment worker --loglevel=info --concurrency=5
    depends_on:
      - rabbitmq
    networks:
      - helium-network

  redis:
    image: redis:7-alpine
    networks:
      - helium-network

  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "5672:5672"
      - "15672:15672"
    networks:
      - helium-network

networks:
  helium-network:
    driver: bridge
```

---

## MONITORING & METRICS

### Prometheus Metrics

```python
# Processing metrics
helium_core_files_processed_total{status="success"} 995
helium_core_files_processed_total{status="failed"} 5

# Processing time
helium_core_processing_time_seconds{quantile="0.95"} 45.2

# Queue depth
helium_core_queue_depth{status="pending"} 12
helium_core_queue_depth{status="processing"} 3

# Worker metrics
helium_core_workers_active{worker_type="transformation"} 10
helium_core_workers_active{worker_type="enrichment"} 5

# Database metrics
helium_core_invoices_total 150000
helium_core_customers_total 5000
helium_core_inventory_total 10000

# Error rates
helium_core_errors_total{error_type="parsing"} 5
helium_core_errors_total{error_type="transformation"} 2
```

---

## IMPLEMENTATION CHECKLIST

### Core Functionality
- [ ] 8-step processing pipeline implemented
- [ ] All 7 worker types implemented
- [ ] Customer-specific transformation script execution
- [ ] Modular transformation script support
- [ ] Preview mode (bulk upload)
- [ ] Finalization with user edits
- [ ] IRN generation algorithm
- [ ] QR code generation

### Database
- [ ] All 5 database schemas created
- [ ] Database triggers for WebSocket events
- [ ] Access control permissions table
- [ ] Referential integrity enforced

### API
- [ ] All 9 API endpoints implemented
- [ ] Access control for CRUD operations
- [ ] WebSocket server for Float SDK
- [ ] Health check endpoint

### Workers
- [ ] QueueScannerWorker (60-second polling)
- [ ] FileParserWorker (PDF, Excel, CSV, XML, JSON)
- [ ] TransformationWorker (script execution)
- [ ] EnrichmentWorker (Prodeus API calls)
- [ ] DatabaseWorker (batch inserts)
- [ ] EdgeCommunicationWorker (Edge API)
- [ ] PortoBelloWorker (future, architecture ready)

### Error Handling
- [ ] Graceful degradation for API failures
- [ ] Retry with exponential backoff
- [ ] Circuit breaker for Prodeus APIs
- [ ] Idempotence for all operations

### Scaling & Performance
- [ ] Horizontal worker scaling (Celery)
- [ ] Batch processing (100 invoices per task)
- [ ] Parallel processing for large uploads

### Monitoring
- [ ] Prometheus metrics
- [ ] Structured audit logging
- [ ] Health check endpoint
- [ ] Worker status tracking

---

## REFERENCES

- **HELIUM_OVERVIEW.md**: Complete Helium ecosystem architecture
- **RELAY_ARCHITECTURE.md**: Relay service architecture
- **SDK WORKSTREAM_3**: WebSocket event schemas

---

## CONTACT & SUPPORT

- **Repository**: Internal Prodeus GitLab
- **Documentation**: `Helium/Services/Core/`
- **Support**: core-team@prodeus.com

---

**End of Document**
