# CRITICAL UPDATES TO CORE DOCUMENTATION

**Date:** 2026-02-01
**Status:** URGENT - Updates required before implementation starts
**Priority:** CRITICAL (blocks Core implementation if not addressed)

---

## SUMMARY OF CHANGES REQUIRED

After reading:
1. **CORE_QUEUE_DELAYED_CLEANUP_SPEC.md** (HeartBeat integration requirement)
2. **CORE_INTEGRATION_REQUIREMENTS.md** (Relay integration requirement)

**THREE CRITICAL CHANGES** must be made to the Core documentation:

---

## CHANGE #1: NEW ENDPOINTS (CRITICAL)

### What Changed
**NEW ENDPOINTS REQUIRED** that were NOT in the original 18 endpoint list:

#### **Added Endpoint 1: POST /api/v1/enqueue**
- Purpose: Queue a file for processing (from Relay)
- Request: file_uuid, blob_path, original_filename, source, immediate_processing
- Response: queue_id, status="queued"
- Handled By: Phase 1 (FETCH)

#### **Added Endpoint 2: POST /api/v1/process_preview**
- Purpose: Process file with preview (blocking call, up to 300 second timeout)
- Request: queue_id
- Response: queue_id, status, statistics, preview_data
- Timeout: Returns 202 Accepted if still processing after 300 seconds
- Handled By: Phases 1-7 (entire pipeline)

#### **Added Endpoint 3: POST /api/v1/finalize**
- Purpose: Finalize previewed invoices with user edits
- Request: queue_id, edits (invoice_edits, customer_edits, inventory_edits)
- Response: queue_id, status="finalized", statistics
- Handled By: Phase 8 (FINALIZE)

#### **Clarification on Existing Endpoint: POST /api/v1/process**
- This was the "main entry point" but actually maps to `/enqueue`
- Should be split into `/enqueue` (queue) + `/process_preview` (processing)

### Impact on Documentation

**Update CORE_CLAUDE.md:**
- Change "18 endpoints" to **21 endpoints** (added 3)
- Clarify the `/process` endpoint is actually two separate flows:
  - Enqueue flow: `/api/v1/enqueue`
  - Processing flow: `/api/v1/process_preview` (blocking, returns preview data)
  - Finalization flow: `/api/v1/finalize` (apply edits, create records)

**Update PHASES_OVERVIEW.md:**
- Phase 1 (FETCH) now includes `/api/v1/enqueue` endpoint
- Phases 1-7 orchestrate `/api/v1/process_preview` (blocking call)
- Phase 8 implements `/api/v1/finalize` endpoint

---

## CHANGE #2: core_queue DELAYED CLEANUP (CRITICAL)

### What Changed

**CRITICAL:** Core CANNOT immediately delete `core_queue` entries after processing.

**Current Understanding (WRONG):**
```python
# Phase 8 was going to:
core_db.core_queue.update_one(
    {"queue_id": queue_id},
    {"status": "processed"}
)
# Then delete immediately - WRONG!
core_db.core_queue.delete_one({"queue_id": queue_id})
```

**New Requirement (CORRECT):**
```python
# Phase 8 must:
core_db.core_queue.update_one(
    {"queue_id": queue_id},
    {
        "status": "processed",
        "processed_at": now(),
        "updated_at": now()  # ✅ NEW: Track processing time
    }
)

# ✅ Schedule deletion for 24 hours LATER (not immediately)
scheduler.add_job(
    delete_queue_entry,
    'date',
    run_date=now() + timedelta(hours=24),
    args=[queue_id],
    id=f"cleanup_queue_{queue_id}"
)
```

### Why This Matters

**HeartBeat Reconciliation:**
- HeartBeat needs to query Core's queue status every hour
- HeartBeat calls: `GET /api/v1/core_queue/status`
- If Core deletes entries immediately, HeartBeat can't verify processing happened
- 24-hour window allows HeartBeat reconciliation to work correctly

**Recovery Window:**
- If Core crashes during processing, entries stay in queue
- On restart, Core can detect unprocessed/half-processed entries
- Provides safety net for crash recovery

### Required Implementation

**Phase 8 (FINALIZE) must add:**

1. **APScheduler dependency**
   - Install: `pip install apscheduler`
   - Initialize scheduler in Core startup
   - Persist scheduled jobs to `jobs.db`

2. **delete_queue_entry() function**
   - Called 24 hours after processing
   - Verifies entry status is "processed" before deleting
   - Logs deletion to audit trail
   - Handles crash recovery

3. **Startup recovery logic**
   - On Core startup, scan for processed entries without scheduled cleanup jobs
   - Reschedule cleanups for entries past their 24-hour window
   - Handles case where scheduler database was lost

4. **GET /api/v1/core_queue/status endpoint**
   - Returns all queue entries (pending, processing, processed)
   - Includes timestamps: created_at, updated_at, processed_at
   - Used by HeartBeat for reconciliation

### Impact on Documentation

**Update CORE_CLAUDE.md:**
- Add **1 new endpoint:** `GET /api/v1/core_queue/status` (for HeartBeat)
- Update total to **21 endpoints** (was 18)

**Update DECISIONS.md:**
- Add NEW decision: "core_queue Delayed Cleanup"
  - Cleanup timing: 24 hours after processing (not immediate)
  - Method: APScheduler with per-entry scheduling
  - Rationale: Enables HeartBeat reconciliation + crash recovery

**Update PHASE_8_FINALIZE/PHASE_8_DECISIONS.md:**
- Add decision about cleanup scheduling
- Add APScheduler as Phase 8 dependency
- Add recovery logic requirement

**Update Infrastructure Phase:**
- Add APScheduler initialization to main.py
- Add jobs.db to database setup

---

## CHANGE #3: RED FLAGS TAXONOMY & PREVIEW DATA STRUCTURE

### What Changed

**Core must classify processing issues into RED FLAGS** that Relay shows to users:

#### Red Flag Types (From Relay Spec)

| Type | Severity | Example |
|------|----------|---------|
| `missing_hsn_code` | error | HSN code could not be determined |
| `missing_supplier_tin` | error | Supplier TIN is missing |
| `invalid_date_format` | error | Date format invalid |
| `suspicious_amount` | warning | Amount exceeds typical range |
| `duplicate_invoice_number` | warning | Invoice number already exists |
| `missing_customer_details` | warning | Customer info incomplete |
| `invalid_tax_calculation` | error | Tax calc doesn't match |
| `unsupported_currency` | error | Currency not NGN |

**Response Format:**
```json
{
    "queue_id": "queue_123",
    "status": "processed",
    "statistics": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "red_flags": [
            {
                "type": "missing_hsn_code",
                "invoice_id": "INV_003",
                "severity": "error",
                "message": "HSN code could not be determined"
            }
        ]
    },
    "preview_data": {
        "firs_invoices_url": "/api/blob/{uuid}/firs_invoices.json",
        "report_url": "/api/blob/{uuid}/report.json",
        "customers_url": "/api/blob/{uuid}/customers.json",
        "inventory_url": "/api/blob/{uuid}/inventory.json",
        "failed_invoices_url": "/api/blob/{uuid}/failed_invoices.xlsx",
        "fixed_pdf_url": "/api/blob/{uuid}/fixed.pdf"
    }
}
```

#### Preview Data Blob Files

Core must create these 6 files in blob storage (7-day retention):

1. **firs_invoices.json** - FIRS-compliant invoice data
2. **report.json** - Statistics and summary
3. **customers.json** - Extracted customer master data
4. **inventory.json** - Extracted inventory/product data
5. **failed_invoices.xlsx** - Failed invoices (Excel download)
6. **fixed.pdf** - Corrected/fixed invoices (optional)

### Impact on Documentation

**Update PHASE_7_BRANCH/PHASE_7_DECISIONS.md:**
- Add decision: "Red Flags Taxonomy"
- Add decision: "Preview Data Structure"
- Document which phase generates which preview files

**Update API_CONTRACTS for /process_preview endpoint:**
- Define red_flags structure
- Define preview_data URLs structure
- Define statistics structure

**Update Phase 8 implementation requirements:**
- Validate edits before creating records
- Return proper error responses with structured details

---

## CHANGE #4: TIMEOUT HANDLING (IMPORTANT)

### What Changed

**`POST /api/v1/process_preview` is a BLOCKING CALL with TIMEOUT:**

- Relay timeout: 300 seconds (5 minutes)
- For small batches (<100 invoices): Complete within timeout
- For large batches (1000+): Return 202 Accepted immediately, continue processing

**Response Codes:**
- `200 OK` - Processing complete, preview data ready
- `202 Accepted` - Still processing, call back later
- `503 Service Unavailable` - Core down

### Impact on Documentation

**Update DECISIONS.md:**
- Add decision: "Processing Timeout Handling"
- Small batches complete within 300 seconds
- Large batches return 202 Accepted, continue in background

**Update PHASES_OVERVIEW.md:**
- Add performance requirement: Small batch <300 seconds

---

## CHANGE #5: DEDUPLICATION INTEGRATION

### What Changed

**Core must maintain `processed_files` table** for deduplication:

- Relay checks SHA256 hash locally (current batch)
- HeartBeat checks against Core's `processed_files` (historical)
- Core checks again during processing (final verification)

**Core must return in response:**
- `duplicate_count` in statistics
- `duplicate_invoice_number` in red_flags
- `original_queue_id` for duplicate files

### Impact on Documentation

**Update DECISIONS.md:**
- Add decision: "Deduplication Strategy"
- Core maintains `processed_files` table (SHA256 hashes)

**Update database schemas:**
- Add `processed_files` table definition

---

## CHANGE #6: AUDIT LOGGING REQUIREMENTS

### What Changed

**Core must log these events to `audit.db`:**

1. `core.processing.started`
2. `core.processing.completed`
3. `core.processing.failed`
4. `core.finalization.completed`

Each event is a JSON record with context (queue_id, invoice counts, errors, etc.)

### Impact on Documentation

**Update DECISIONS.md:**
- Add decision: "Audit Event Taxonomy"

**Update logging implementation:**
- Define which worker logs which events
- Define audit event structure

---

## CHANGE #7: ERROR CODES & STRUCTURED ERRORS

### What Changed

**Core error responses must be STRUCTURED:**

```json
{
    "error": "ERROR_CODE",
    "message": "Human-readable message",
    "details": [
        {
            "field": "field_name",
            "error": "Specific error for field"
        }
    ]
}
```

**Core error codes:**
- `CORE_ENQUEUE_FAILED` (500)
- `BLOB_NOT_FOUND` (404)
- `INVALID_FILE_FORMAT` (400)
- `PROCESSING_FAILED` (500)
- `INVALID_EDITS` (400)
- `SERVICE_UNAVAILABLE` (503)

### Impact on Documentation

**Update ERRORS.md (create if not exists):**
- Define all Core error codes
- Define error response structure

---

## COMPLETE LIST OF DOCUMENTATION CHANGES

| Document | Change | Impact |
|----------|--------|--------|
| CORE_CLAUDE.md | Update endpoint count (18 → 21) | 3 new endpoints required |
| CORE_CLAUDE.md | Add GET /api/v1/core_queue/status | HeartBeat integration |
| CORE_CLAUDE.md | Add POST /api/v1/enqueue | Relay integration |
| CORE_CLAUDE.md | Add POST /api/v1/process_preview | Relay integration (blocking) |
| CORE_CLAUDE.md | Add POST /api/v1/finalize | Finalization flow |
| DECISIONS.md | Add delayed cleanup decision | 24-hour retention required |
| DECISIONS.md | Add deduplication decision | processed_files table |
| DECISIONS.md | Add red flags taxonomy | Classification of issues |
| DECISIONS.md | Add timeout handling decision | 300-second max for small batches |
| PHASES_OVERVIEW.md | Update effort estimates | Add cleanup scheduling ~1-2 days |
| PHASE_1_DECISIONS.md | Add /enqueue endpoint | Relay entry point |
| PHASE_7_DECISIONS.md | Add red flags + preview data | Data structure decisions |
| PHASE_8_DECISIONS.md | Add cleanup scheduling | APScheduler integration |
| Infrastructure docs | Add APScheduler setup | Scheduler initialization |
| Database schemas | Add processed_files table | Deduplication tracking |
| Database schemas | Add updated_at to core_queue | Tracking processing time |
| ERRORS.md | Create new file | Error codes and structure |

---

## IMPLEMENTATION IMPACT

### For OPUS (Infrastructure)
- Add APScheduler initialization
- Add `processed_files` table to schema
- Add `updated_at` column to `core_queue`
- Initialize scheduler on startup

### For HAIKU (Phases 1-2)
- Implement `/api/v1/enqueue` endpoint in Phase 1
- Integrate with Relay (write to core_queue)

### For SONNET (Phases 3-5)
- Generate red_flags during processing
- Implement graceful degradation (continue without errors where possible)

### For OPUS (Phases 6-8)
- Implement `/api/v1/process_preview` (blocking call)
- Implement `/api/v1/finalize` (with user edits)
- Implement cleanup scheduling (24-hour delayed)
- Implement startup recovery logic
- Generate preview data files (6 files to blob)
- Maintain audit trail
- Return structured error responses

### Effort Impact
- **Additional effort**: ~2-3 extra days for Core team
- **Critical path**: Must be completed before Relay Phase 1C

---

## RECOMMENDED NEXT STEPS

1. **Update CORE_CLAUDE.md** - Add 3 new endpoints, update endpoint count
2. **Update DECISIONS.md** - Add 4 new binding decisions
3. **Create ERRORS.md** - Define error codes and response structure
4. **Update PHASE_* documents** - Allocate new requirements to phases
5. **Share with OPUS** - Provide updated documentation
6. **Estimate effort** - Add 2-3 days to Core implementation timeline

---

## CRITICAL QUESTIONS FOR CLARIFICATION

**Question 1:** Is `/api/v1/process` being replaced by `/enqueue` + `/process_preview` + `/finalize`, or should all three coexist?

**Question 2:** The 24-hour delayed cleanup - should failed entries be deleted after 30 days (as per DECISIONS.md), or follow a different retention?

**Question 3:** Preview data generation (6 files) - who creates the fixed.pdf file? Should Core attempt to fix errors automatically or just flag them?

> **ANSWERED (2026-02-20):** Core creates the fixed PDF. A "fixed PDF" is the original invoice PDF with the IRN number and QR code overlaid onto it — it is NOT a corrected or modified invoice. Core places the QR code and IRN number using placement details found in the tenant's EIC (Extraction Intelligence Config) via the `qr_irn_placement` section. If no tenant EIC exists or the EIC lacks QR/IRN placement config, Core uses default intelligence captured in the EI (Extraction Intelligence) base knowledge. Core does NOT attempt to fix invoice errors — it only overlays IRN + QR compliance markings.

**Question 4:** For deduplication, should Core query the `processed_files` table on Relay's behalf, or should Relay do all dedup checking?

**Question 5:** Red flags taxonomy - can Core extend beyond the 8 types defined by Relay?

---

## APPROVAL NEEDED

**These changes require user approval before implementation begins:**

- [ ] Approve 21 total endpoints (was 18)
- [ ] Approve 3 new endpoints (/enqueue, /process_preview, /finalize)
- [ ] Approve 24-hour delayed cleanup requirement
- [ ] Approve red flags taxonomy
- [ ] Approve preview data structure (6 files)
- [ ] Approve deduplication integration
- [ ] Approve audit logging requirements
- [ ] Approve error response structure
- [ ] Approve updated timeline (add 2-3 days)

---

**Status:** AWAITING USER APPROVAL

**Next Step:** User to review and approve all changes before Core implementation begins

---

## CHANGE #8: CORE SSE ENDPOINT — TWO STREAM TYPES (CRITICAL)

**Date Added:** 2026-03-08
**Source:** Relay + SDK + HeartBeat identity harmonization session

### What's Required

Core MUST expose `GET /sse/stream` (or `/api/sse/stream`).
SDK already has `core_sse_url = "http://localhost:8080/sse"` configured.

Core SSE carries **TWO distinct stream types** that must NOT be conflated:

#### Stream 1: ENUMed Statuses (persistent — updates sync.db)

State machine transitions. SDK writes these to sync.db columns. SWDB re-renders.

```
event: invoice.status_changed
data: {"data_uuid": "...", "invoice_id": "...", "status": "processing"}

event: invoice.created
data: {"data_uuid": "...", "invoice_id": "...", "status": "created"}

event: customer.created / inventory.updated (etc.)
```

#### Stream 2: Processing Logs (ephemeral — display only, NOT persisted)

Free-form human-readable messages while Core works. Drives Float ProgressFeed
and **live counter on ReviewPage** as invoices finalize one by one.

```
event: processing.log
data: {"data_uuid": "...", "message": "Parsing invoice.pdf...", "level": "info"}

event: processing.log
data: {"data_uuid": "...", "message": "Extracted 3 line items", "level": "info"}

event: processing.progress
data: {"data_uuid": "...", "invoices_ready": 1, "invoices_total": 3}

event: processing.log
data: {"data_uuid": "...", "message": "Transformation complete — 1 invoice ready", "level": "success"}
```

#### Field definitions

| Event | Field | Type | Description |
|-------|-------|------|-------------|
| `processing.log` | `data_uuid` | string | Per-request group ID (from Relay) |
| `processing.log` | `message` | string | Human-readable log line |
| `processing.log` | `level` | string | `info` / `success` / `warning` / `error` |
| `processing.progress` | `data_uuid` | string | Per-request group ID |
| `processing.progress` | `invoices_ready` | int | Completed so far |
| `processing.progress` | `invoices_total` | int | Total being processed |

### Identity Model (from March 2026 harmonization)

Core receives from Relay:

| Identifier | Scope | Generated by |
|-----------|-------|-------------|
| `data_uuid` | Per-request group | Relay |
| `blob_uuid` | Per-file (HeartBeat internal name for `file_uuid`) | Relay |
| `x_trace_id` | Log correlation | Relay middleware |
| `queue_id` | Core processing queue | Core |

All SSE events MUST include `data_uuid` for SDK correlation.

### Impact on Documentation

- [ ] Add SSE endpoint to PHASES_OVERVIEW.md (Phase 0 infrastructure)
- [ ] Add `processing.log` and `processing.progress` event types
- [ ] Distinguish ENUMed statuses (persistent) from processing logs (ephemeral)
- [ ] ReviewPage live counter depends on `processing.progress` events

### Transforma EventEmitter Integration (Added 2026-03-21)

Transforma (the in-process transformation library) now emits events via an `EventEmitter` callback protocol during its 8-phase pipeline run. **Core must wire this to SSE.**

#### How it works:

1. Core calls `execute_transformation(script, raw_files, ..., event_emitter=my_emitter)`
2. Transforma calls `await event_emitter(event_type, data)` at every phase boundary
3. Core's `my_emitter` relays each event to the SSE endpoint

#### EventEmitter Protocol (defined in `Transforma/src/transforma/callbacks.py`):

```python
class EventEmitter(Protocol):
    async def __call__(self, event_type: str, data: dict) -> None: ...
```

#### Events emitted by Transforma:

| Phase | Event Type | Example Message |
|-------|-----------|-----------------|
| Phase 1 (Parse) | `processing.log` | "Phase 1: Parsing 3 file(s)" |
| Phase 1 | `processing.progress` | `{ phase: "parse", phase_number: 1, phase_total: 8 }` |
| Phase 2-3 (Extract) | `processing.log` | "Extracted 45 invoices, 12 customers, 18 products" |
| Phase 4 (Validate) | `processing.log` | "Validation complete — 3 errors, 5 warnings" |
| Phase 5 (Enrich) | `processing.log` | "Phase 5: Enriching 18 products" |
| Phase 6 (Format) | `processing.log` | "Phase 6: Generating IRN and QR codes" |
| Phase 7 (Quality) | `processing.log` | "Compliance score: 82.0% (B)" |
| Phase 8 (Branch) | `processing.log` | "submission: 40, failed: 3, duplicate: 2" |
| Final | `processing.log` | "Pipeline complete in 2160ms — 40 for submission, 3 failed, 2 duplicates" |

Core does NOT need to transform these events — just relay them to SSE with the existing `processing.log` and `processing.progress` event types.

### Edge Transmission Events (Added 2026-03-21)

After finalization, Core hands invoices to Edge for FIRS submission. Core must emit **per-invoice transmission events** so the ReviewPage can show live status updates:

```
event: invoice.status_changed
data: {"data_uuid": "...", "invoice_id": "inv-001", "status": "processed", "timestamp": "..."}

event: transmission.progress
data: {"data_uuid": "...", "transmitted_count": 45, "total_count": 120, "current_invoice_id": "inv-046"}

event: transmission.complete
data: {"data_uuid": "...", "success_count": 118, "error_count": 2, "total_count": 120}
```

| Event | Field | Type | Description |
|-------|-------|------|-------------|
| `transmission.progress` | `data_uuid` | string | Bulk upload batch ID |
| `transmission.progress` | `transmitted_count` | int | Invoices transmitted so far |
| `transmission.progress` | `total_count` | int | Total invoices to transmit |
| `transmission.progress` | `current_invoice_id` | string | ID of the invoice just transmitted |
| `transmission.complete` | `success_count` | int | Successfully accepted by FIRS |
| `transmission.complete` | `error_count` | int | Rejected by FIRS |

---

## CHANGE #10: HLX-TO-XLSX CONVERSION (MEDIUM)

**Date Added:** 2026-03-21
**Source:** ReviewPage design session

### What's Required

Core must be able to convert an `.hlx` archive into an `.xlsx` workbook for **email attachments**. This is NOT a Transforma concern — Core does this after Transforma produces the .hlx.

### Use Case

When Core sends email notifications (e.g., "Your bulk upload is ready for review"), it can attach an Excel summary. The recipient may not have Float installed.

### Conversion Rules

- Each .hlm sheet in the .hlx → one Excel worksheet
- Use the DISPLAY column profile (not FULL)
- Column headers from `display_name` field in HLMColumn definitions
- Amount columns formatted with comma separators (#,##0.00)
- Date columns formatted as DD/MM/YYYY
- Sheet tabs named from `display_name` (e.g., "Invoices for Submission", "Failed Invoices")
- Include a "Summary" sheet with key stats from `report.json` (total invoices, compliance grade, red flags)

### Impact

- Core: Add `hlx_to_xlsx(hlx_bytes) -> xlsx_bytes` utility
- Dependency: `openpyxl` (already in Transforma's deps, Core can share)
- This is a presentation layer concern — Core owns it, Transforma does not

---

## CHANGE #11: HLX LIFECYCLE — GENERATED + FINALIZED (CRITICAL)

**Date Added:** 2026-03-21
**Source:** Bob (product owner)

### What's Required

An `.hlx` file is generated at **TWO points** in the lifecycle, and BOTH must be stored in HeartBeat Blob:

#### 1. Generated HLX (post-Transforma, pre-finalization)

- Created by Core WS3 after Transforma completes the 8-phase pipeline
- Contains all 7 sheets with status = `Pending` for submission invoices
- Stored in HeartBeat Blob as `{data_uuid}.hlx`
- Float downloads this for ReviewPage rendering
- This is the "review copy" — user has not yet finalized

#### 2. Finalized HLX (post-finalization, post-Edge transmission)

- Created by Core AFTER all invoices have been transmitted to FIRS via Edge
- Contains **updated data**:
  - Status column updated: Pending → Processed or Error (per invoice)
  - Error notes from Edge added to failed invoices (FIRS rejection reasons)
  - Final success timestamp on each processed invoice
  - `transmission.complete` summary in report.json
- Stored in HeartBeat Blob as `{data_uuid}_finalized.hlx`
- This is the "audit copy" — permanent record of what was sent and what FIRS said

#### Blob Storage

| File | Blob Key | When Created | Purpose |
|------|----------|-------------|---------|
| `{data_uuid}.hlx` | `blob/{company_id}/{data_uuid}.hlx` | After Transforma pipeline | Review copy |
| `{data_uuid}_finalized.hlx` | `blob/{company_id}/{data_uuid}_finalized.hlx` | After Edge transmission completes | Audit copy |

Both copies are retained. The generated copy is NOT deleted when the finalized copy is created.

### Impact

- Core: Generate second .hlx after finalization with updated statuses and Edge error notes
- HeartBeat Blob: Store both copies
- HLX_FORMAT.md: Document the two-stage lifecycle
- Float: Can open either copy (generated = live review, finalized = historical audit)

---

## CHANGE #12: HLX ENCRYPTION — TENANT-BOUND (MEDIUM)

**Date Added:** 2026-03-21
**Source:** Bob (product owner)

### What's Required

HLX files must be encrypted so that only the Float app instance belonging to the same tenant can open them.

#### Encryption Scheme

- **Algorithm**: AES-256-GCM (fast, authenticated encryption)
- **Key derivation**: The encryption key is derived from the **tenant Helium ID** (`company_id`)
  - `key = HKDF(SHA-256, ikm=company_id.encode(), salt=b"helium-hlx-v1", info=b"hlx-encryption", length=32)`
  - HKDF ensures the company_id (which may be short) produces a full 256-bit key
- **Nonce**: Random 12-byte nonce, prepended to ciphertext
- **File format**: `[12-byte nonce][AES-GCM ciphertext][16-byte auth tag]`

#### Validation Flow

1. Float receives `.hlx` file
2. Float derives encryption key from its own tenant `company_id` (from JWT / local config)
3. Float attempts AES-GCM decryption
4. If decryption succeeds AND `metadata.json → company_id` matches Float's tenant ID → open
5. If decryption fails → "Access Denied: This file belongs to a different tenant"
6. If decryption succeeds but company_id mismatch → "Access Denied" (defense in depth)

#### Who Encrypts / Decrypts

| Action | Component | When |
|--------|-----------|------|
| Encrypt | Core (WS3) | When generating .hlx (both generated and finalized copies) |
| Decrypt | Float SDK | When opening .hlx for ReviewPage |

### Impact

- Core: Encrypt .hlx before storing to Blob and before sending to Float
- Float SDK: Decrypt .hlx on open using local tenant ID
- HLX_FORMAT.md: Document encryption as part of file format
- HeartBeat: No changes (stores opaque bytes)
- This is a **simple and fast** encryption — the goal is tenant isolation, not military-grade security

---

---

## CHANGE #9: SDK — FLOAT-TRIGGERS-HEARTBEAT AUTO-START (FUTURE DEBT)

**Date Added:** 2026-03-08
**Priority:** DEFERRED — not blocking, build after Core is functional
**Owner:** SDK team

### What's Required

For dev UX, Float/SDK should detect whether HeartBeat is running and start it if not.
HeartBeat's Keep Alive manager then cascades all other services per lifecycle spec.

**Startup flow (dev mode):**
```
User launches Float
  → SDK calls HeartBeat /health
  → If DOWN: SDK starts HeartBeat as subprocess
  → HeartBeat initializes → Keep Alive starts Core (Priority 1)
  → Keep Alive starts Relay + HIS (Priority 2)
  → Keep Alive starts Edge (Priority 3)
  → SDK connects, auth flow begins
```

**Production flow (unchanged):**
HeartBeat is an NSSM/systemd OS service — always running at boot.

### Proposed SDK API

```python
# In SDK initialization (e.g., HeliumSDK.__init__ or startup hook)
await sdk.ensure_services_running()

# Implementation:
# 1. GET http://localhost:9000/health
# 2. If connection refused → subprocess.Popen(heartbeat_cmd)
# 3. Poll /health until 200 (timeout 30s)
# 4. HeartBeat Keep Alive handles the rest
```

### Current Workaround

`Services/scripts/run_all_dev.bat` — manual launcher script.
Starts HeartBeat + Relay via Python process manager (`run_all_dev.py`).

### Impact

- SDK: Add `ensure_services_running()` method
- SDK config: Add `heartbeat_executable_path` or `heartbeat_start_cmd`
- No Core/Relay/HeartBeat changes needed — Keep Alive already handles cascade

---

**Document Version:** 1.3
**Created:** 2026-02-01
**Updated:** 2026-03-21 (added Change #10: HLX-to-XLSX, Change #11: HLX Lifecycle, Change #12: HLX Encryption; expanded Change #8 with Transforma EventEmitter + Edge transmission events)
**Status:** CRITICAL UPDATES IDENTIFIED
