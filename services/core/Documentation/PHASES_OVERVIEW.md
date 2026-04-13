# CORE SERVICE - PHASES OVERVIEW

**Version:** 1.0
**Last Updated:** 2026-01-31
**Status:** READY FOR PHASE IMPLEMENTATION

---

## EXECUTIVE SUMMARY

Core Service is implemented as **9 sequential phases** (1 infrastructure + 8 processing):

- **Phase 0: INFRASTRUCTURE** (OPUS) - Foundation layer
- **Phase 1: FETCH** (HAIKU) - Retrieve files
- **Phase 2: PARSE** (HAIKU) - Parse file formats
- **Phase 3: TRANSFORM** (SONNET) - Customer transformation scripts
- **Phase 4: ENRICH** (SONNET) - API enrichment
- **Phase 5: RESOLVE** (SONNET) - Entity resolution
- **Phase 6: PORTO BELLO** (OPUS) - Business logic gate
- **Phase 7: BRANCH** (OPUS) - Preview vs immediate mode
- **Phase 8: FINALIZE** (OPUS) - Create records & finalize

**Total Endpoints:** 18 (across all phases)

**Total Lines of Code (Estimated):** ~5000-7000 LOC + ~3000-5000 LOC tests

**Total Effort (Estimated):** 120-160 hours

---

## PHASE EXECUTION TIMELINE

```
Timeline (Parallel Execution Possible):

OPUS (Infrastructure)
├─ [Week 1] Build Foundation (Database, API, WebSocket)
│
├─ HAIKU (Phases 1-2) [Week 1-2]
│  ├─ Phase 1: FETCH (Queue reading, blob fetching)
│  └─ Phase 2: PARSE (File parsing - PDF, Excel, CSV, XML, JSON)
│
├─ SONNET (Phases 3-5) [Week 2-3]
│  ├─ Phase 3: TRANSFORM (Script execution)
│  ├─ Phase 4: ENRICH (Prodeus APIs)
│  └─ Phase 5: RESOLVE (Entity resolution)
│
└─ OPUS (Phases 6-8) [Week 3-4]
   ├─ Phase 6: PORTO BELLO (Business logic)
   ├─ Phase 7: BRANCH (Preview/immediate divergence)
   └─ Phase 8: FINALIZE (Database, WebSocket, Edge integration)

Integration Testing & Deployment
├─ [Week 4-5] End-to-end testing
├─ [Week 5] Performance testing & tuning
└─ [Week 5-6] Deployment & documentation
```

**Parallel Execution:** After Infrastructure ready, HAIKU, SONNET, and OPUS can work in parallel

---

## PHASE DETAILS

### PHASE 0: INFRASTRUCTURE (OPUS)

**Purpose:** Build foundation layer that all phases depend on

**Responsibilities:**
- Create Core/src/database/ (SQLite schemas, connection pooling)
- Create Core/src/api/ (FastAPI app, routing, middleware)
- Create Core/src/websocket/ (event server, broadcasting)
- Create Core/src/access_control/ (permissions, RBAC)
- Create Core/src/errors.py (exception classes, error codes)
- Create Core/src/logging.py (structured logging)
- Create Core/src/config.py (environment configuration)

**Deliverables:**
- `Core/src/database/schemas.py` - All 5 database table definitions
- `Core/src/database/connection.py` - SQLite connection pooling
- `Core/src/api/app.py` - FastAPI application
- `Core/src/api/endpoints.py` - 18 endpoint stubs
- `Core/src/websocket/server.py` - WebSocket server
- `Core/src/access_control.py` - Permission checking
- `Core/src/errors.py` - Error definitions
- `Core/Documentation/INFRASTRUCTURE/` - Infrastructure docs

**Dependencies:** None (foundational)

**Timeline:** 2-3 weeks

**Test Coverage Target:** 85%+ (foundational layer, some manual testing)

---

### PHASE 1: FETCH (HAIKU)

**Purpose:** Retrieve invoice files from blob storage

**Processing Step:** Step 1 of 8-step pipeline

**Responsibilities:**
- Poll core_queue table every 60 seconds
- Fetch files from blob storage (MinIO, S3, etc.)
- Extract file bytes and metadata
- Update queue status ("pending" → "processing")
- Handle errors gracefully (blob not found, read failures)

**Deliverables:**
- `Core/src/workers/queue_scanner.py` - QueueScannerWorker
- `Core/src/workers/blob_fetcher.py` - BlobFetcher utility
- Tests for queue scanning and blob fetching
- `Core/Documentation/PHASE_1_FETCH/` - Phase documentation

**API Endpoints Implemented:**
- `GET /api/v1/health` - Basic health check

**Dependencies:** Infrastructure

**Timeline:** 1 week

**Test Coverage Target:** 92%+

**Outputs to Phase 2:**
- Raw file data (bytes)
- Metadata: {file_uuid, blob_path, original_filename, file_size}

---

### PHASE 2: PARSE (HAIKU)

**Purpose:** Parse file formats (PDF, Excel, CSV, XML, JSON)

**Processing Step:** Step 2 of 8-step pipeline

**Responsibilities:**
- Detect file type from extension
- Call appropriate parser (pdfplumber, openpyxl, pandas, lxml, json)
- Extract raw structured data
- Handle parsing errors gracefully
- Support multiple invoice formats

**Deliverables:**
- `Core/src/workers/file_parser.py` - FileParserWorker
- `Core/src/parsers/pdf_parser.py` - PDF parsing
- `Core/src/parsers/excel_parser.py` - Excel parsing
- `Core/src/parsers/csv_parser.py` - CSV parsing
- `Core/src/parsers/xml_parser.py` - XML parsing
- `Core/src/parsers/json_parser.py` - JSON parsing
- Tests for all parser types
- `Core/Documentation/PHASE_2_PARSE/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1

**Timeline:** 1 week

**Test Coverage Target:** 94%+

**Outputs to Phase 3:**
- Raw structured data (dict/list)
- Metadata: {file_type, row_count, parsing_time_ms}

---

### PHASE 3: TRANSFORM (SONNET)

**Purpose:** Execute customer-specific transformation scripts

**Processing Step:** Step 3 of 8-step pipeline

**Responsibilities:**
- Load customer-specific transformation script from config.db
- Execute script modules: extract_module, validate_module, enrich_module, finalize_module
- Extract 3 data types: invoices, customers, inventory
- Format to FIRS-compliant structure
- Handle transformation errors gracefully
- Support modular transformation scripts

**Deliverables:**
- `Core/src/workers/transformation.py` - TransformationWorker
- `Core/src/transformation/script_loader.py` - Load scripts from config.db
- `Core/src/transformation/executor.py` - Execute scripts safely
- Tests for script execution, error handling
- `Core/Documentation/PHASE_3_TRANSFORM/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1-2

**Timeline:** 2 weeks

**Test Coverage Target:** 91%+

**Outputs to Phase 4:**
- Structured invoice data (list of dicts)
- Structured customer data (list of dicts)
- Structured inventory data (list of dicts)

---

### PHASE 4: ENRICH (SONNET)

**Purpose:** Enrich data via Prodeus certified APIs

**Processing Step:** Step 4 of 8-step pipeline

**Responsibilities:**
- Call Prodeus certified APIs (parallel async):
  - HSN code mapping
  - Product category mapping
  - Postal code validation
  - AI enrichment
- Handle API failures gracefully (circuit breaker)
- Flag enrichment sources (AUTO, MANUAL, AI)
- Implement retry with exponential backoff

**Deliverables:**
- `Core/src/workers/enrichment.py` - EnrichmentWorker
- `Core/src/enrichment/prodeus_client.py` - Prodeus API calls
- `Core/src/enrichment/circuit_breaker.py` - Circuit breaker pattern
- Tests for API calls, error handling, graceful degradation
- `Core/Documentation/PHASE_4_ENRICH/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1-3

**Timeline:** 1.5 weeks

**Test Coverage Target:** 90%+

**Outputs to Phase 5:**
- Enriched invoice data (with HSN, category, postal validation)
- Enriched customer data
- Enriched inventory data

---

### PHASE 5: RESOLVE (SONNET)

**Purpose:** Entity resolution (match customers and inventory to master data)

**Processing Step:** Step 5 of 8-step pipeline

**Responsibilities:**
- Match customer data to customers.db
  - Search by TIN, name, address
  - Fuzzy matching if exact match not found
  - Create new or update existing customer
- Match inventory data to inventory.db
  - Search by SKU, product code, barcode
  - Fuzzy matching if needed
  - Create new or update existing inventory
- Link invoices to customer_id
- Link invoice line items to inventory_id
- Handle master data merges

**Deliverables:**
- `Core/src/workers/resolution.py` - ResolutionWorker
- `Core/src/resolution/entity_matcher.py` - Fuzzy matching logic
- `Core/src/resolution/customer_resolver.py` - Customer matching
- `Core/src/resolution/inventory_resolver.py` - Inventory matching
- Tests for fuzzy matching, merging, edge cases
- `Core/Documentation/PHASE_5_RESOLVE/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1-4

**Timeline:** 1.5 weeks

**Test Coverage Target:** 91%+

**Outputs to Phase 6:**
- Resolved invoice data (with customer_id, inventory_ids)
- Updated customers.db records
- Updated inventory.db records

---

### PHASE 6: PORTO BELLO (OPUS)

**Purpose:** Business logic gate for Porto Bello workflow (architecture ready, implementation deferred)

**Processing Step:** Step 6 of 8-step pipeline

**Responsibilities:**
- Check customer config: portoBello flag
- If portoBello == true:
  - Generate IRN and QR code
  - Write to invoices.db (status="pending_counterparty_details")
  - Queue to Edge: SIGN task only (not TRANSMIT)
  - Queue to Edge: PORTOBELLO task (notify counterparty)
  - DO NOT queue TRANSMIT task yet
- If portoBello == false:
  - Continue to Phase 7 (normal flow)

**Deliverables:**
- `Core/src/workers/porto_bello.py` - PortoBelloWorker (stub + logic)
- Tests for business logic gate
- `Core/Documentation/PHASE_6_PORTO_BELLO/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1-5

**Timeline:** 1 week

**Test Coverage Target:** 90%+

**Outputs to Phase 7:**
- Invoice with status flag (normal or pending_counterparty_details)
- Queue entries to Edge (SIGN + PORTOBELLO tasks if Porto Bello)

---

### PHASE 7: BRANCH (OPUS)

**Purpose:** Handle divergence between preview and immediate processing modes

**Processing Step:** Step 7 of 8-step pipeline

**Responsibilities:**
- Check immediate_processing flag
- If false (PREVIEW MODE):
  - Generate preview data (firs_invoices.json, report.json, etc.)
  - Append to blob (7-day retention)
  - Update core_queue: status="preview_ready"
  - Return preview URLs to Relay
  - STOP (wait for finalization)
- If true (IMMEDIATE MODE):
  - Continue to Phase 8 (finalize immediately)

**Deliverables:**
- `Core/src/workers/branching.py` - BranchingWorker
- `Core/src/branching/preview_generator.py` - Generate preview data
- Tests for both preview and immediate modes
- `Core/Documentation/PHASE_7_BRANCH/` - Phase documentation

**API Endpoints Implemented:** None (internal worker)

**Dependencies:** Infrastructure, Phase 1-6

**Timeline:** 1 week

**Test Coverage Target:** 91%+

**Outputs to Phase 8:**
- Invoice data (normal or preview mode)
- Preview URLs (if preview mode)
- Control flow to Phase 8 (if immediate mode)

---

### PHASE 8: FINALIZE (OPUS)

**Purpose:** Create database records, queue to Edge, broadcast WebSocket events

**Processing Step:** Step 8 of 8-step pipeline

**Responsibilities:**
- Apply user edits (if provided)
- Generate IRN and QR code (if not done in Porto Bello)
- Create invoices.db record
- Create/update customers.db entries
- Create/update inventory.db entries
- Queue to Edge: SIGN_AND_TRANSMIT (or SIGN if Porto Bello)
- Call Edge API
- Update core_queue: status="processed"
- Trigger WebSocket broadcasts
- Implement /retry endpoint (full cycle recovery)
- Implement /retransmit endpoint (exchange only)
- Implement entity CRUD endpoints (PUT/DELETE)
- Implement inbound invoice acceptance (accept/reject)
- Implement generic update endpoint

**Deliverables:**
- `Core/src/workers/finalization.py` - FinalizationWorker
- `Core/src/api/endpoints.py` (complete all endpoints)
- `Core/src/edge_client.py` - Edge API integration
- `Core/src/websocket/broadcaster.py` - WebSocket event broadcasting
- Tests for database operations, Edge API, WebSocket, error handling
- `Core/Documentation/PHASE_8_FINALIZE/` - Phase documentation

**API Endpoints Implemented:**
- `POST /api/v1/process` (finalization logic)
- `POST /api/v1/retry` (full cycle recovery)
- `POST /api/v1/retransmit` (exchange only)
- `PUT /api/v1/entity/{type}/{id}` (update)
- `DELETE /api/v1/entity/{type}/{id}` (delete)
- `POST /api/v1/update` (generic update from Edge)
- `POST /api/v1/invoice/{id}/accept` (B2B accept)
- `POST /api/v1/invoice/{id}/reject` (B2B reject)
- `GET /api/v1/core_queue/status` (queue status for HeartBeat)
- `WS /api/v1/events` (WebSocket broadcasting)

**Dependencies:** Infrastructure, Phase 1-7

**Timeline:** 2-3 weeks

**Test Coverage Target:** 90%+

**Outputs:** Complete invoices in invoices.db, broadcast to Float SDK

---

## PHASE VARIANT ASSIGNMENT

| Phase | Step | Variant | Rationale | Timeline |
|-------|------|---------|-----------|----------|
| **Infrastructure** | — | **OPUS** | Foundational layer | **Weeks 1** |
| **Phase 1** | FETCH | **HAIKU** | Simple queue reading | **Week 1-2** |
| **Phase 2** | PARSE | **HAIKU** | Deterministic file parsing | **Week 1-2** |
| **Phase 3** | TRANSFORM | **SONNET** | Complex script execution | **Week 2-3** |
| **Phase 4** | ENRICH | **SONNET** | Async API calls, error handling | **Week 2-3** |
| **Phase 5** | RESOLVE | **SONNET** | Entity resolution, fuzzy matching | **Week 2-3** |
| **Phase 6** | PORTO BELLO | **OPUS** | Architectural gate | **Week 3** |
| **Phase 7** | BRANCH | **OPUS** | State divergence | **Week 3** |
| **Phase 8** | FINALIZE | **OPUS** | Orchestration, database, WebSocket | **Week 3-4** |

---

## PHASE DEPENDENCIES GRAPH

```
INFRASTRUCTURE (OPUS)
    ↑
    ├─── Phase 1: FETCH (HAIKU)
    │        ↓
    │    Phase 2: PARSE (HAIKU)
    │        ↓
    │    Phase 3: TRANSFORM (SONNET)
    │        ↓
    │    Phase 4: ENRICH (SONNET)
    │        ↓
    │    Phase 5: RESOLVE (SONNET)
    │        ↓
    ├─── Phase 6: PORTO BELLO (OPUS)
    │        ↓
    │    Phase 7: BRANCH (OPUS)
    │        ↓
    └─── Phase 8: FINALIZE (OPUS)
```

**Key Points:**
- INFRASTRUCTURE must be done first (all phases depend)
- Phases 1-2 are sequential (HAIKU)
- Phases 3-5 are sequential (SONNET)
- Phases 6-8 are sequential (OPUS)
- After Infrastructure, HAIKU can start immediately
- After Phase 2 complete, SONNET can start
- After Phase 5 complete, OPUS can continue with Phases 6-8

---

## DOCUMENTATION FILES PER PHASE

Each phase has dedicated documentation:

```
Core/Documentation/PHASE_X_[NAME]/
├── PHASE_X_DECISIONS.md          ← Phase-specific decisions
├── PHASE_X_CHECKLIST.md          ← Implementation checklist
├── API_CONTRACTS.md              ← Phase endpoint specs
└── PHASE_X_STATUS.md             ← Status file (updated hourly)
```

**Files Already Created:**
- ✅ Core/Documentation/CORE_CLAUDE.md (master protocol)
- ✅ Core/Documentation/DECISIONS.md (core-wide decisions)
- ✅ Core/Documentation/PHASE_TEMPLATE.md (template for phases)
- ✅ Core/Documentation/PHASES_OVERVIEW.md (this file)

**Files to Create per Phase:**
- [ ] PHASE_1_FETCH/PHASE_1_DECISIONS.md
- [ ] PHASE_1_FETCH/PHASE_1_CHECKLIST.md
- [ ] PHASE_1_FETCH/API_CONTRACTS.md
- [ ] ... (repeat for Phases 2-8)

---

## ENDPOINTS DISTRIBUTION BY PHASE

### Infrastructure Phase
- `GET /api/v1/health` (stub)
- `WS /api/v1/events` (framework, logic in Phase 8)

### Phase 1 (FETCH)
- `GET /api/v1/health` (implement)

### Phases 2-5
- None (internal workers, no endpoints)

### Phase 6 (PORTO BELLO)
- `POST /api/v1/retransmit` (queue TRANSMIT task)

### Phase 7 (BRANCH)
- None (internal worker)

### Phase 8 (FINALIZE)
- `POST /api/v1/process` (main endpoint, finalization logic)
- `POST /api/v1/retry` (queue SIGN_AND_TRANSMIT task)
- `POST /api/v1/retransmit` (complete implementation)
- `PUT /api/v1/entity/{type}/{id}` (update logic)
- `DELETE /api/v1/entity/{type}/{id}` (delete logic)
- `POST /api/v1/update` (generic update routing)
- `POST /api/v1/invoice/{id}/accept` (B2B accept)
- `POST /api/v1/invoice/{id}/reject` (B2B reject)
- `GET /api/v1/core_queue/status` (queue status)
- `WS /api/v1/events` (complete broadcasting)

---

## EFFORT ESTIMATION

| Phase | Component | Effort (hours) | Notes |
|-------|-----------|----------------|-------|
| **Infrastructure** | Database + API + WebSocket | 40-60 | Foundational, touches many areas |
| **Phase 1** | Queue scanning + Blob fetching | 20-25 | Simple, deterministic |
| **Phase 2** | File parsers (5 types) | 30-40 | Multiple parser implementations |
| **Phase 3** | Script execution + validation | 30-40 | Complex logic, security considerations |
| **Phase 4** | Prodeus APIs + Circuit breaker | 25-30 | Async, error handling, retries |
| **Phase 5** | Entity resolution + Fuzzy match | 30-40 | Algorithm complexity, master data management |
| **Phase 6** | Porto Bello business logic | 15-20 | Less complex, future-focused |
| **Phase 7** | Preview vs Immediate branching | 20-25 | State management, preview generation |
| **Phase 8** | Database transactions + Finalization | 40-50 | Most complex, orchestrates everything |
| **Testing** | Comprehensive test suite | 40-60 | 90%+ coverage required |
| **Documentation** | Architecture, API, implementation docs | 20-30 | Specs written before code |
| **Integration & Deployment** | End-to-end testing, deployment | 20-30 | Performance tuning, deployment guides |
| **Total** | **All Phases** | **320-450 hours** | **4-6 weeks** |

---

## QUALITY GATES

Each phase must satisfy:

- ✅ **API Contract Compliance** - All signatures match API_CONTRACTS.md
- ✅ **Error Code Coverage** - All error codes from ERRORS.md implemented
- ✅ **Test Coverage** - 90%+ code coverage (measured)
- ✅ **Performance Targets** - Latency/throughput targets met
- ✅ **Logging** - Key operations logged with context
- ✅ **Documentation** - Code comments, docstrings, phase docs
- ✅ **Git Hygiene** - Proper commit messages, atomic commits
- ✅ **No TODOs** - All work completed (no FIXMEs or TODOs)

---

## NEXT STEPS (FOR USER)

1. **Review this document** - Confirm phases, timelines, assignments
2. **Create phase-specific DECISIONS files** - Customize decisions for each phase
3. **Create phase checklists** - Adapt template to each phase
4. **Create API contract files** - Define endpoints per phase
5. **Share with OPUS** - Provide Infrastructure setup instructions
6. **Share with HAIKU** - Provide Phases 1-2 instructions
7. **Share with SONNET** - Provide Phases 3-5 instructions
8. **Share with OPUS (again)** - Provide Phases 6-8 instructions

---

**Last Updated:** 2026-01-31
**Version:** 1.0
**Status:** READY FOR PHASE-SPECIFIC CUSTOMIZATION
