# Core Service — Full Pending Changes List (2026-03-30)

**Context**: Consolidated from audit findings, HeartBeat handoff, WS7 spec, and CRITICAL_UPDATES_REQUIRED.md.

---

## A. HeartBeat Integration (from HEARTBEAT_CANONICAL_AUDIT handoff)

### A1. HeartBeat Status Client Method
**Where**: `src/ingestion/heartbeat_client.py`
**What**: Add `update_blob_status()` to call HeartBeat's `POST /api/v1/heartbeat/blob/{blob_uuid}/status`
**Why**: HeartBeat's `file_entries.status` stays at "uploaded" forever — Core never reports phase transitions back.

### A2. Pipeline Phase → HeartBeat Status Callbacks
**Where**: `src/orchestrator/pipeline.py`
**What**: After each phase transition, call `heartbeat_client.update_blob_status()` with the mapping:

| Core Phase | HeartBeat status | processing_stage |
|------------|-----------------|------------------|
| FETCH | processing | fetch |
| PARSE | processing | parse |
| TRANSFORM | processing | transform |
| ENRICH | processing | enrich |
| RESOLVE | processing | resolve |
| PREVIEW | preview_pending | preview |
| FINALIZE | finalized | (null) |
| ERROR | error | (last stage) |

Include `processing_stats` (extracted/rejected/submitted/duplicate counts) on final status update.

### A3. Forward Processing Stats in SSE Events
**Where**: `src/orchestrator/pipeline.py` (SSE emission points)
**What**: Add `extracted_invoice_count`, `rejected_invoice_count`, `submitted_invoice_count`, `duplicate_count` to `processing.complete` and `processing.progress` SSE events.
**Why**: SDK Queue tab needs these stats to display processing results.

---

## B. WS7 — Reports & Statistics (COMPLETE — audited 2026-03-31)

**Status**: Implemented. 14 source files (2,391 lines), 10 test files (91 tests, 90 passing).

### B1. Statistics Endpoint — DONE
`GET /api/v1/statistics` — 5 sections, 5-minute TTL cache, PostgreSQL views.

### B2. Report Generation Service — DONE
`POST /api/v1/reports/generate` (async 202), `GET .../status`, `GET .../download`.
5 report types: compliance, transmission, customer, audit_trail, monthly_summary.

### B3. Database Schema — DONE
`core.reports` table with indexes, CHECK constraints, updated_at trigger.

### B4. Scheduled Reports — DONE
Weekly compliance (Monday 6am), monthly summary (1st 6am), expired cleanup (6h).

### B5. Remaining Gaps
- `weasyprint` not in requirements.txt (PDF uses HTML fallback in dev)
- No chart generation (matplotlib not integrated)
- Statistics cache has no invalidation hook (5-min TTL handles it)

---

## C. Pending from CRITICAL_UPDATES_REQUIRED.md

### C1. 24-Hour Delayed Cleanup for core_queue
**Where**: `src/scheduler.py` + new job in `src/jobs/`
**What**: core_queue entries must NOT be deleted immediately after processing. APScheduler job runs hourly, deletes entries where `completed_at < NOW() - INTERVAL '24 hours'`.
**Why**: HeartBeat reconciliation depends on core_queue entries surviving for 24h.
**Status**: NOT IMPLEMENTED.

### C2. HLX Encryption (Tenant-Bound)
**Where**: `src/orchestrator/preview_generator.py`
**What**: AES-256-GCM encryption keyed by `company_id` for .hlx files.
**Status**: Design complete, implementation pending.

### C3. HLX-to-XLSX Conversion Utility
**Where**: New file `src/utils/hlx_to_xlsx.py`
**What**: Convert .hlx preview files to Excel for email attachments.
**Status**: NOT IMPLEMENTED.

---

## D. HIS Feedback Loop — Finalize-to-HIS Intelligence (NEW)

**Source**: Bob (2026-03-29) — architectural direction for HIS learning from human corrections.

### Context

When a user reviews invoices in HLX (Float preview) and manually corrects data — HSN codes, customer postal codes, LGAs, entity names, etc. — those corrections represent high-fidelity, human-validated data. This intelligence must flow back to HIS so future enrichment improves over time.

Key distinction: **API-submitted invoices are finalized by default** (no human review), so their data carries lower confidence. **HLX-reviewed invoices** where a human actively made changes carry significantly higher confidence and should be weighted accordingly when updating HIS intelligence.

### D1. Finalize → HIS Feedback Client
**Where**: `src/finalize/pipeline.py` (new step after record commit)
**New**: `src/processing/his_feedback_client.py`
**What**: After finalize commits records to the database, extract all human-corrected fields (from `edit_history`) and POST them to HIS as intelligence updates.

**Payload to HIS** (per entity changed):
```
POST /api/v1/his/intelligence/update
{
    "company_id": "...",           // Tenant isolation
    "source": "hlx_review",       // or "api_finalize" (lower weight)
    "confidence_weight": 0.95,     // HLX human review = high weight
    "entity_type": "inventory",    // or "customer"
    "entity_id": "...",
    "corrections": [
        {
            "field": "hsn_code",
            "old_value": "4802.55",
            "new_value": "4802.56",
            "changed_by": "user-123"
        }
    ],
    "context": {
        "invoice_id": "...",
        "product_name": "...",
        "description": "..."
    }
}
```

### D2. Confidence Weighting by Source
**Where**: HIS service (receiver side), but Core must tag the source correctly.
**What**: Core tags every feedback payload with:
- `"source": "hlx_review"` + `"confidence_weight": 0.95` — Human made changes via HLX preview
- `"source": "api_finalize"` + `"confidence_weight": 0.40` — API submission, no human review
- `"source": "hlx_no_change"` + `"confidence_weight": 0.70` — Human reviewed but accepted as-is (implicit validation)

### D3. Per-Tenant Intelligence Isolation
**Where**: HIS service architecture, but Core must always include `company_id` in feedback.
**What**: Every HIS instance maintains a per-tenant intelligence folder/namespace. Corrections from Tenant A must never influence enrichment results for Tenant B.
**Core's responsibility**: Always include `company_id` in the feedback payload. Never batch corrections across tenants.

### D4. Determine Which Fields Changed via HLX
**Where**: `src/finalize/pipeline.py`
**What**: Compare the original `preview_data` (from WS3 orchestrator output) against the finalized `edits` (from user's PUT body). Fields that differ = human corrections to feed back to HIS.
**Key fields to track**:
- **Inventory**: `hsn_code`, `service_code`, `vat_treatment`, `vat_rate`, `product_category`, `service_category`
- **Customer**: `postal_code`, `lga`, `lga_code`, `state`, `state_code`, `tin`, `rc_number`, `tax_classification`
- **Invoice**: `document_type`, `transaction_type`, `payment_status` (less common but possible)

---

## E. Stubs Requiring Real Implementation

### E1. PDF Parser → IntelliCore Integration
**Where**: `src/ingestion/parsers/pdf_parser.py`
**Current**: Returns raw UTF-8 decoded text with a red flag.
**Needed**: IntelliCore Textract integration for structured PDF extraction.
**Blocked by**: IntelliCore service availability.

### E2. HIS Client → Real API
**Where**: `src/processing/his_client.py`
**Current**: `HISStubClient` returns mock HS codes/categories.
**Needed**: Real HTTP client to HIS service when available.
**Note**: Protocol interface + circuit breaker already in place — easy swap.

---

## F. Error Handling & Resilience Gaps

### F1. Open Error Handling Decisions
**Source**: `Documentation/WS_ERROR_HANDLING_KICKSTARTER.md`

| Scenario | Decision Needed |
|----------|----------------|
| File already processed (SHA256 dup) | Status: DUPLICATE? FAILED? |
| HeartBeat blob download 5xx | Retry w/ backoff? How long? |
| Scanner: max attempts exceeded | Notify user? Admin? |
| HIS service down | Continue without enrichment? Which fields missing? |
| Finalize: partial success (50/100 pass) | Atomic? Partial .hlx? Notification? |
| Pipeline timeout at phase boundary | Per-phase timeouts? |
| Memory pressure (large batch) | Max file size? Max invoices per batch? |

### F2. Graceful Shutdown (SIGTERM)
**Where**: `src/app.py` lifespan
**What**: Handle in-flight pipelines on SIGTERM — drain workers, persist state.
**Status**: NOT IMPLEMENTED.

### F3. Idempotency Guards
**Where**: `src/orchestrator/pipeline.py`
**What**: Prevent double-processing of the same file if `/process_preview` is called twice.
**Status**: NOT IMPLEMENTED.

---

## G. Test & Infrastructure Fixes

### G1. Stale Test: test_all_13_codes_exist
**Where**: `tests/ws0/test_errors.py:29`
**What**: Asserts `len(CoreErrorCode) == 13` but actual is 41. Update to 41.

### G2. Schema Init Test: queue_id Column
**Where**: `tests/ws0/test_database_init.py`
**What**: `invoices.sql` references `queue_id` column that doesn't exist in test schema. Schema source-of-truth mismatch.

### G3. Missing Database Indexes
**Where**: `src/database/schemas/invoices.sql`
- Index on `transmission_status + transmission_date`
- Index on `workflow_status + created_at`
- Partial index on `deleted_at IS NOT NULL`

### G4. Per-Tier Config Files (Missing)
**What**: Create `config/test.json`, `config/standard.json`, `config/pro.json`, `config/enterprise.json` with documented defaults for timeouts, batch sizes, worker pools, retry settings.

---

## Priority Summary

| Priority | Items | Description |
|----------|-------|-------------|
| **P0** | A1, A2, C1 | HeartBeat status integration + delayed cleanup (blocks reconciliation) |
| **P1** | A3, D1-D4, F1-F3 | SSE stats, HIS feedback loop, error handling, shutdown, idempotency |
| **P2** | B1-B4 | WS7 Reports & Statistics (full workstream) |
| **P3** | C2, C3, G1-G4 | HLX encryption, XLSX conversion, test fixes, indexes |
| **Blocked** | E1, E2 | PDF parser (needs IntelliCore), HIS client (needs HIS service) |
