# WS5 FINALIZE — Complete Workstream Overview

**Date:** 2026-03-25
**Status:** Part 1 IMPLEMENTED | Part 2 SPEC'D (pending implementation)
**Owner:** WS5 (Finalize + Edge Integration)
**Test Status:** 66/66 tests passing

---

## WHAT WS5 IS

WS5 is the **commit and dispatch layer** of Helium Core. It receives user-confirmed invoice data from Float SDK, validates that only permitted edits were made, generates FIRS-required identifiers (IRN + QR), commits records to PostgreSQL, and queues invoices to Edge for FIRS submission.

**WS5 is the only workstream that writes invoice data to the database.**

---

## THE 9-STEP FINALIZE FLOW

```
Step 1:  File arrives at Core (via Relay) with preview flag
Step 2:  Core runs full pipeline (Transforma + enrich + resolve) — NO DB writes
Step 3:  Core outputs preview .hlx to HeartBeat (stored with preview flag)
Step 4:  Core pushes same .hlx to Float via SSE
Step 5:  User reviews in Float ReviewPage, edits allowed fields
Step 6:  SDK packages final data as .hlm → sends to Core with finalized flag
Step 7:  Core receives .hlm, skips Transforma, diffs against preview .hlx:
           - Only editable fields may differ
           - Sensitive fields (TIN, amounts, seller info) must match exactly
           - Any tampering → reject with violations
Step 8:  Lightweight final validation → commit to DB
Step 9:  Queue to Edge for FIRS submission
```

**Key design principle:** Core processes the invoice TWICE — once for preview (full pipeline, no DB writes) and once for finalize (skip pipeline, validate edits, commit). The preview .hlx stored in HeartBeat is the tamper-detection reference.

---

## WHAT CAN BE EDITED (AND WHAT CANNOT)

### The Universal Rule

**Source data is sacred.** Only enriched, missing, or low-confidence fields are editable. Per-field provenance metadata (set by Transforma) determines editability.

### Provenance Values

| Value | Meaning | Set By | Editable? |
|-------|---------|--------|-----------|
| `ORIGINAL` | Extracted from source document | Transforma | **No** (unless low confidence) |
| `MISSING` | Not in source, not enriched | Transforma | **Yes** |
| `HIS` | Enriched by HIS / Transforma | Transforma | **Yes** |
| `DERIVED` | Computed by Core (normalized names, match scores) | WS2 | **No** |
| `TENANT` | From tenant config.db (seller/buyer party) | WS2 | **No** |
| `MANUAL` | Set by user in a prior edit cycle | WS5 | **Yes** |

**Low confidence override:** Fields with `classification_confidence < 0.60` are editable even if provenance is `ORIGINAL`.

### Always Editable (User Metadata)

- `reference`, `category`, `notes_to_firs`, `payment_terms_note`, `terms`
- Credit/debit note references (`reference_irn`, `reference_issue_date`)
- `transaction_type` — via dropdown with rules:
  - **B2B <-> B2G:** freely swappable
  - **B2C -> B2B/B2G:** blocked until user fills in counterparty details (TIN, name)
  - **B2B/B2G -> B2C:** allowed

### Conditionally Editable (Provenance-Gated)

- Counterparty address fields: `buyer_lga_code`, `buyer_postal_code`, `buyer_state_code`, `buyer_country_code`, `buyer_address`, `buyer_city`
- Classification: `firs_invoice_type_code`
- Line item classification: `hsn_code`, `service_code`, `product_category`, `service_category`, `vat_treatment`

### NEVER Editable

- All `seller_*` fields (tenant party)
- `buyer_tin`, `buyer_name` (if from source)
- `invoice_number`, `irn`, `csid` (identity)
- All amounts: `subtotal`, `tax_amount`, `total_amount`
- Line item amounts: `quantity`, `unit_price`, `line_total`, `description`
- All dates: `issue_date`, `issue_time`
- All status, audit, and trace fields

### Customer & Inventory in HLX

The `customers.hlm` and `inventory.hlm` sheets in the .hlx are **view-only**. Users see newly detected entities (flagged with `__IS_NEW__`) but cannot edit them from the ReviewPage. Customer and inventory edits go through the Customer List and Inventory tabs in Float (WS4 `PUT /entity/{type}/{id}`).

---

## SOURCE CODE

All implementation lives in `Helium/Services/Core/src/finalize/`.

### Module Map

| File | Purpose | Lines |
|------|---------|-------|
| `provenance.py` | Provenance constants (`ORIGINAL`, `MISSING`, `HIS`, etc.), editability rules, field classifications (always/never/conditional), confidence threshold, tenant party field resolution | ~273 |
| `errors.py` | Error hierarchy: `FinalizeError` base, `EditValidationError`, `IRNGenerationError`, `QRGenerationError`, `RecordCommitError`, `EdgeSubmitError`, `PreviewNotFoundError`, `BatchMismatchError` | ~67 |
| `models.py` | Request/response dataclasses: `FinalizeRequest`, `FinalizeResponse`, `RetryRequest`, `RetransmitRequest`, `AcceptRequest`, `RejectRequest`, `EdgeUpdateRequest` | ~168 |
| `edit_validator.py` | **The diff engine.** Compares submitted .hlm rows against preview .hlx rows. Checks every field against provenance rules. Enforces never-editable, always-editable, provenance-gated, and transaction_type upgrade rules. Returns `EditValidationResult` with violations and accepted changes. | ~426 |
| `irn_generator.py` | FIRS IRN generation: `{INVOICE_NUMBER}-{SERVICE_ID}-{YYYYMMDD}`. Validates alphanumeric invoice number, 8-char service ID, non-future date. | ~113 |
| `qr_generator.py` | QR code generation: 200x200 PNG, base64 encoded. Content is 5-field JSON: `{irn, invoice_number, total_amount, issue_date, seller_tin}`. Supports batch async generation. Requires `qrcode` + `Pillow` libraries. | ~122 |
| `record_creator.py` | **DB committal.** Single-transaction writes: invoice INSERT (ON CONFLICT DO NOTHING), line items INSERT, customer UPSERT (by TIN+company_id), inventory UPSERT (by product_id) + name variant tracking. Returns `CommitResult` with counts. | ~423 |
| `edge_client.py` | HTTP client for Edge FIRS submission service. `POST /api/v1/submit` (batch), `GET /api/v1/status/{batch_id}` (check). Builds `EdgeSubmission` payloads from finalized rows. | ~247 |
| `pipeline.py` | **The orchestrator.** Runs: validate edits -> generate IRN -> generate QR -> commit to DB -> queue to Edge. Returns `FinalizeResult`. Edge failure is non-fatal (invoices are committed, Edge retry happens later). | ~258 |
| `sse_events.py` | SSE event definitions for Float: `hlx.finalize_started`, `hlx.validation_failed`, `hlx.finalized`, `hlx.finalize_failed`, `invoice.created`, `customer.created/updated`, `product.created/updated` | ~159 |
| `router.py` | HTTP endpoints: `POST /api/v1/finalize` (full finalize), `POST /api/v1/finalize/validate` (dry-run validation only), `GET /api/v1/finalize/{batch_id}/status` (check status). | ~187 |
| `idempotency.py` | Idempotency guard: SHA-256 key from `(batch_id, company_id, version)`, 24h TTL, cached result replay on duplicate request. | ~120 |
| `audit_logger.py` | Per-step audit logging to `finalize_audit_log` table. Steps: VALIDATE, IRN_GENERATE, QR_GENERATE, COMMIT, EDGE_SUBMIT. Statuses: STARTED, SUCCEEDED, FAILED. Never crashes the pipeline. | ~70 |
| `schema.sql` | DDL for `finalize_idempotency` and `finalize_audit_log` tables with indexes. | ~55 |
| `__init__.py` | Public API: exports `EditValidator`, `FinalizePipeline`, `RecordCreator`, `generate_irn`, `finalize_routes`, error classes. | ~47 |

### Test Map

All tests in `Helium/Services/Core/tests/ws5/`.

| File | Tests | What It Covers |
|------|-------|----------------|
| `test_edit_validator.py` | 32 | No-change passthrough, never-editable rejection, always-editable acceptance, provenance-gated fields (HIS/MISSING/MANUAL accept, ORIGINAL/DERIVED reject), low-confidence override, tenant party fields (direction-aware), transaction_type B2B<->B2G swap + B2C upgrade rules, line item classification diffs, graceful degradation (no provenance metadata), multiple violation aggregation, serialization |
| `test_irn_generator.py` | 18 | Format validation, date object support, whitespace stripping, alphanumeric enforcement, service ID length, future-date rejection, IRN format validation |
| `test_models.py` | 16 | Request/response dataclass validation, required fields, optional fields, edge update types |
| **Total** | **66** | |

---

## DB INTEGRITY SAFEGUARDS

See: `WS5_DB_INTEGRITY.md` for full spec.

### Implemented (Tier A)

| Safeguard | What | Where |
|-----------|------|-------|
| Idempotency keys | SHA-256(batch+company+version), 24h TTL, cached replay | `idempotency.py` |
| ON CONFLICT guards | `DO NOTHING` on invoice_id and line_item_id duplicates | `record_creator.py` |
| Audit logging | Per-step log entries in `finalize_audit_log` table | `audit_logger.py` |
| Transaction isolation | Single PostgreSQL transaction for all writes per batch | `record_creator.py` |

### Documented as Future Debt

| Tier | Safeguard | Effort |
|------|-----------|--------|
| B | Reconciliation endpoint (diff DB vs HeartBeat preview) | 1-2 days |
| B | Batch-level checksums (SHA-256 over committed data) | 2-3 hours |
| B | Edge dispatch tracking (per-invoice submission log) | 3-4 hours |
| B | Real-time veracity APIs (validate single invoice for auto-upgrade) | 2-3 days |
| C | Event sourcing (append-only event log, derived state) | 2-3 weeks |
| C | Automated rollback (undo all writes for a batch) | 1 week |
| C | Cross-database consistency checks (invoice -> customer -> inventory) | 2-3 days |

---

## KNOWN GAPS (WS5 Part 2)

See: `WS5_PART2_HANDOFF.md` for full spec + field inventory.

**The `record_creator.py` writes ~41 of ~115 invoice fields.** Part 2 addresses the remaining fields:

| Gap | What's Missing | Priority |
|-----|----------------|----------|
| Invoice INSERT completion | 22 fields: helium_invoice_no, workflow_status, processing telemetry, source tracking, config snapshot, denormalized counts | **First** |
| Security/trace fields | 11 fields from HELIUM_SECURITY_SPEC: 3-level trace chain, machine fingerprint, session context | **First** |
| Customer aggregates | 17 fields: total_invoices, average_invoice_size, lifetime_value/tax, last_*_date, total_pending/transmitted/accepted, frequency | **Second** |
| Inventory aggregates | 3 incomplete: total_revenue, avg_unit_price (running average), top_customer | **Second** |
| Blob batch stats | 7 fields: total_invoice_count, submitted/rejected/duplicate counts, processing_time | **Second** |
| File entry stats | 6 fields: extracted/submitted/rejected/duplicate counts, finalized timestamps | **Second** |
| Deferred computation | Async worker architecture (sync commit + async aggregates) | **Second** |

### Open Questions (Need Architect Answers)

1. `helium_invoice_no` format — `WM-{tenant_id}-{seq}` confirmed? Sequence scope?
2. Does SDK already send machine_guid, mac_address, session_id?
3. Should WS5 set workflow_status to `COMMITTED` or `QUEUED`?
4. Does WS5 own blob_batches updates or does WS3?
5. `product_summary` format and max length?
6. `payment_due_date` — who computes? WS5 or Transforma?

---

## DEPENDENCIES ON OTHER WORKSTREAMS

### WS5 Depends On

| Workstream | What WS5 Needs | Status |
|------------|-----------------|--------|
| **WS1 (Ingestion)** | Route finalized .hlm files (with `finalized` flag) directly to WS5, skipping Transforma. Add `FINALIZE_READY` queue status. | See `WS1_HLX_NOTE.md` |
| **Transforma** | Populate `field_provenance` on ALL output fields. Own extraction + enrichment (vat_treatment, customer_type, classification). Framework-level provenance, not per-script. | See `WS_TRANSFORMA_PROVENANCE_NOTE.md` |
| **WS2 (Processing)** | Mechanical provenance stamping ONLY: `TENANT` on tenant party fields, `DERIVED` on computed fields. WS2 does NOT enrich, does NOT call HIS. | See `WS2_HLX_NOTE.md` |
| **WS3 (Orchestrator)** | Serialize `__provenance__` into .hlm sheets. Include `customers.hlm` and `inventory.hlm` pages with `__IS_NEW__` flags. | See `WS3_HLX_NOTE.md` |
| **HeartBeat** | Store preview .hlx with batch_id. Provide `get_preview(batch_id)` API for WS5 to fetch at finalize time. | Assumed available |
| **Float SDK** | Package edits as .hlm (not .hlx). Send finalized flag + trace/security context. | SDK team |

### Other WSs Depend On WS5

| Workstream | What They Need From WS5 | Status |
|------------|--------------------------|--------|
| **Edge** | Receives batch submission via `POST /api/v1/submit`. WS5 builds the payload. Edge owns transmission lifecycle (signing, FIRS API, retry). | `edge_client.py` implemented |
| **Float SDK** | Receives SSE events: `hlx.finalized`, `invoice.created`, `customer.created/updated`, `product.created/updated`. Uses these to update local sync.db. | `sse_events.py` implemented |
| **WS4 (Entity CRUD)** | Customer and inventory records upserted by WS5 are then editable via WS4 endpoints. WS5 creates, WS4 manages. | `record_creator.py` creates records |

---

## CROSS-REFERENCES

### Documentation in This Folder

| File | Contents |
|------|----------|
| `WS5_OVERVIEW.md` | This file — complete workstream overview |
| `WS5_DB_INTEGRITY.md` | DB corruption recovery: Tier A (implemented), B and C (future debt) |
| `WS5_PART2_HANDOFF.md` | Schema reconciliation spec: 74 missing fields, aggregate worker design, deferred computation architecture |

### Documentation in Other Folders

| File | Relevance |
|------|-----------|
| `Core/Documentation/HLX_FORMAT.md` (v1.1) | Sections 10-11: provenance spec, editability rules, transaction_type constraints, customer/inventory view-only. Section 12: shared data model + failed invoice auto-upgrade. Section 13: lifecycle. |
| `Core/Documentation/HLM_FORMAT.md` (v2.0) | .hlm structure, `provenance_default` column property |
| `Workstreams/WS1_INGESTION/WS1_HLX_NOTE.md` | WS1 must route finalized .hlm (skip Transforma) |
| `Workstreams/WS2_PROCESSING/WS2_HLX_NOTE.md` | WS2 mechanical provenance stamping (TENANT, DERIVED) |
| `Workstreams/WS3_ORCHESTRATOR/WS3_HLX_NOTE.md` | WS3 must include entity sheets + provenance in .hlx |
| `Workstreams/WS3_ORCHESTRATOR/WS3_HLX_ARCH_RESPONSE.md` | WS3 team's response + architect answers (Question A needs fix) |
| `Workstreams/WS_TRANSFORMA/WS_TRANSFORMA_PROVENANCE_NOTE.md` | Transforma owns provenance AND enrichment. One pipeline, not two passes. |

### Canonical Schemas (Source of Truth)

| Entity | Path |
|--------|------|
| Invoice | `Helium/Documentation/Schema/invoice/06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` |
| Customer | `Helium/Documentation/Schema/customer/02_CUSTOMER_DB_CANONICAL_SCHEMA_V1.sql` |
| Inventory | `Helium/Documentation/Schema/inventory/02_INVENTORY_DB_CANONICAL_SCHEMA_V1.sql` |
| Blob | `Helium/Documentation/Schema/blob/04_BLOB_DB_CANONICAL_SCHEMA_V1.sql` |

---

## HOW TO RUN TESTS

```bash
cd Helium/Services/Core
python -m pytest tests/ws5/ -v --tb=short
```

Expected: 66 passed. All tests are unit tests (no DB, no network). Edit validator and IRN generator are fully deterministic.

---

## CHANGELOG

| Date | What |
|------|------|
| 2026-03-25 | Part 1 implemented: pipeline, edit validator, IRN/QR, record creator, Edge client, SSE events, router. 66 tests. |
| 2026-03-25 | DB integrity Tier A: idempotency keys, audit logger, ON CONFLICT guards, schema.sql. |
| 2026-03-25 | Part 2 handoff written: 74 missing fields identified, aggregate worker architecture designed, 6 open questions for architect. |
