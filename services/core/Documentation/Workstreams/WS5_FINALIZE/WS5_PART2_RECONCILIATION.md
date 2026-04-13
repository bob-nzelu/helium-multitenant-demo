# WS5 Part 2 — Schema Reconciliation Register

**Date:** 2026-03-25
**Status:** IN PROGRESS — section-by-section review with architect
**Method:** 3-way alignment: Canonical Schema SQL ↔ Core record_creator.py ↔ SDK schema.py (sync.db)

---

# PART I — FIELD-BY-FIELD AUDIT

## Invoices Table — Section A: Primary Identification (12 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 1 | `id` | PK AUTO | ✅ | Skip | No action — PG auto-generates |
| 2 | `invoice_id` | NOT NULL UNIQUE | ✅ | ✅ `row["invoice_id"]` | Correct |
| 3 | `helium_invoice_no` | NOT NULL UNIQUE | ✅ | ❌ Missing | **ADD** — `f"PRO-{company_id}-{invoice_id}"` |
| 4 | `invoice_number` | NOT NULL | ✅ | ✅ `row["invoice_number"]` | Correct — SWDB "Inv_No" in Area A |
| 5 | `irn` | NOT NULL UNIQUE | ✅ | ✅ `row.get("irn")` | Correct — pipeline step 2 generates |
| 6 | `csid` | nullable | ✅ | — | No action — Edge sets at signing |
| 7 | `csid_status` | nullable | ✅ | — | No action — Edge sets at signing |
| 8 | `invoice_trace_id` | nullable | ✅ | ❌ Missing | **ADD** — `str(uuid7())` per invoice, Core-generated |
| 9 | `user_trace_id` | nullable | ✅ | ❌ Missing | **ADD from request_context** — originator trace (SDK upload time). NULL until SDK wiring lands |
| 10 | `x_trace_id` | nullable | ✅ | ❌ Missing | **ADD from request_context** — Relay trace. NULL until Relay wiring lands |
| 11 | `config_version_id` | nullable | ✅ | ❌ Missing | **ADD from request_context** — tenant config snapshot |
| 12 | `schema_version_applied` | nullable | ✅ | ❌ Missing | **ADD** — read from `schema_version` table at startup, cache |

**Decisions made:**
- Prefix changed from `WM-` to `PRO-` (PRO = Prodeus). Docs update required.
- `invoice_trace_id` is Core-generated UUIDv7 — fresh per invoice (not per batch).
- `user_trace_id` traces the ORIGINATOR (uploader), NOT the finalizer. Immutable across lifecycle.
- `x_trace_id` traces the Relay request. Immutable across lifecycle.
- Finalizer identity goes in `helium_user_id`/`user_email`/`user_name` (Section M) — re-evaluated at finalize time.
- Future: `invoice_approvals` table captures full approval journey (upload → review → approve → finalize). Deferred to dedicated design session.

**Wiring gaps identified:**
1. Relay doesn't merge `x_trace_id` into metadata before HeartBeat call (`ingestion.py:175-181`)
2. SDK finalize request only sends `{queue_id, user_edits}` — no trace or identity context
3. Core `core_queue` has no trace columns

---

## Invoices Table — Section B: Three Independent Classifiers (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 13 | `direction` | NOT NULL DEFAULT 'OUTBOUND' | ✅ | ✅ `row.get("direction", "OUTBOUND")` | Correct |
| 14 | `document_type` | NOT NULL DEFAULT 'COMMERCIAL_INVOICE' | ✅ | ❌ Missing | **ADD** — `row.get("document_type", "COMMERCIAL_INVOICE")` |
| 15 | `firs_invoice_type_code` | nullable | ✅ | ✅ `row.get("firs_invoice_type_code")` | Correct |
| 16 | `transaction_type` | NOT NULL DEFAULT 'B2B' | ✅ | ✅ `row.get("transaction_type", "B2B")` | Correct |

**Notes:**
- `document_type` omission is a data integrity risk: if Transforma outputs a credit note (type_code=381) but record_creator doesn't write `document_type`, it silently becomes `COMMERCIAL_INVOICE` via DB default — contradicting `firs_invoice_type_code` on the same record.
- All three classifiers are independent per FIRS/UBL spec. `direction` ≠ `document_type` ≠ `transaction_type`.

---

## Invoices Table — Sections K+L: Seller Party (16 fields) + Buyer Party (14 fields)

**Seller party (Section K):**

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 50 | `company_id` | NOT NULL | ✅ | ✅ `company_id` param | Correct |
| 51 | `seller_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_id")` |
| 52 | `seller_business_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_business_id")` |
| 53 | `seller_name` | nullable | ✅ | ✅ `row.get("seller_name")` | Correct |
| 54 | `seller_tin` | nullable | ✅ | ✅ `row.get("seller_tin")` | Correct |
| 55 | `seller_tax_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_tax_id")` |
| 56 | `seller_rc_number` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_rc_number")` |
| 57 | `seller_email` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_email")` |
| 58 | `seller_phone` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("seller_phone")` |
| 59 | `seller_address` | nullable | ✅ | ✅ `row.get("seller_address")` | Correct |
| 60 | `seller_city` | nullable | ✅ | ✅ `row.get("seller_city")` | Correct |
| 61 | `seller_postal_code` | nullable | ✅ | ✅ `row.get("seller_postal_code")` | Correct |
| 62 | `seller_lga_code` | nullable | ✅ | ✅ `row.get("seller_lga_code")` | Correct |
| 63 | `seller_state_code` | nullable | ✅ | ✅ `row.get("seller_state_code")` | Correct |
| 64 | `seller_country_code` | DEFAULT 'NG' | ✅ | ✅ `row.get("seller_country_code")` | Correct |

**Buyer party (Section L):**

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 65 | `buyer_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_id")` |
| 66 | `buyer_business_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_business_id")` |
| 67 | `buyer_name` | nullable | ✅ | ✅ `row.get("buyer_name")` | Correct |
| 68 | `buyer_tin` | nullable | ✅ | ✅ `row.get("buyer_tin")` | Correct |
| 69 | `buyer_tax_id` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_tax_id")` |
| 70 | `buyer_rc_number` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_rc_number")` |
| 71 | `buyer_email` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_email")` |
| 72 | `buyer_phone` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("buyer_phone")` |
| 73 | `buyer_address` | nullable | ✅ | ✅ `row.get("buyer_address")` | Correct |
| 74 | `buyer_city` | nullable | ✅ | ✅ `row.get("buyer_city")` | Correct |
| 75 | `buyer_postal_code` | nullable | ✅ | ✅ `row.get("buyer_postal_code")` | Correct |
| 76 | `buyer_lga_code` | nullable | ✅ | ✅ `row.get("buyer_lga_code")` | Correct |
| 77 | `buyer_state_code` | nullable | ✅ | ✅ `row.get("buyer_state_code")` | Correct |
| 78 | `buyer_country_code` | DEFAULT 'NG' | ✅ | ✅ `row.get("buyer_country_code")` | Correct |

**Direction → Party Role Map (from canonical schema):**

```
┌──────────────────────────┬─────────────────────────┬─────────────────────────┐
│ direction                │ seller_* =              │ buyer_* =               │
├──────────────────────────┼─────────────────────────┼─────────────────────────┤
│ OUTBOUND                 │ Tenant (us)             │ Counterparty            │
│ INBOUND                  │ Counterparty            │ Tenant (us)             │
│ SELF_BILLED_INVOICE      │ Counterparty            │ Tenant (us)             │
│ SELF_BILLED_CREDIT       │ Counterparty            │ Tenant (us)             │
└──────────────────────────┴─────────────────────────┴─────────────────────────┘
```

**Notes:**
- `company_id` = "who OWNS this data in Helium" (always the tenant, regardless of direction).
- `seller_id` / `buyer_id` = FK link to `customers` table. For OUTBOUND, `buyer_id` = counterparty customer_id.
- `seller_business_id` = FIRS-registered Business ID (NOT the TIN). MANDATORY for OUTBOUND + SELF_BILLED (app-level validation, not SQL constraint).
- `buyer_business_id` = MANDATORY for SELF_BILLED_INVOICE (app-level validation).
- Three tax identifier formats: `tin` (legacy), `tax_id` (new 13-digit), `rc_number` (CAC). FIRS accepts all three interchangeably.
- `service_id` (8-char FIRS code) is NOT stored on invoices — extractable from IRN. Comes from tenant config.
- Transforma sets all seller_*/buyer_* fields correctly based on direction. WS5 (record_creator) writes what it receives — it does NOT need to reverse fields based on direction.
- SWDB "Customer" column displays the counterparty: buyer_name for OUTBOUND, seller_name for INBOUND.
- All 12 missing fields are simple `row.get()` passthroughs. No computation.

---

## Invoices Table — Sections H+I+J: Status Model + Retry + FIRS Artefacts (10 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 40 | `workflow_status` | NOT NULL DEFAULT 'COMMITTED' | ✅ | 🔧 Wrong name (`status`) AND wrong value (`"FINALIZED"`) | **FIX** — two bugs |
| 41 | `transmission_status` | NOT NULL DEFAULT 'NOT_REQUIRED' | ✅ | ❌ Missing | **ADD** — `"NOT_REQUIRED"` at commit |
| 42 | `transmission_status_error` | nullable | ✅ | — | No action — Core's edge_update handler writes (triggered by Edge callback) |
| 43 | `payment_status` | NOT NULL DEFAULT 'UNPAID' | ✅ | ❌ Missing | **ADD** — `"UNPAID"` at commit |
| 44 | `retry_count` | NOT NULL DEFAULT 0 | ✅ | — | No action — DB default 0. Core edge_update handler increments |
| 45 | `last_retry_at` | nullable | ✅ | — | No action — Core edge_update handler. Don't include in INSERT |
| 46 | `next_retry_at` | nullable | ✅ | — | No action — Core edge_update handler. Don't include in INSERT |
| 47 | `firs_confirmation` | nullable | ✅ | — | No action — Core edge_update handler. Don't include in INSERT |
| 48 | `firs_response_data` | nullable | ✅ | — | No action — Core edge_update handler. Don't include in INSERT |
| 49 | `qr_code_data` | nullable | ✅ | ✅ `row.get("qr_code_data")` | Correct — pipeline step 2 generates |

**Critical decisions:**
- **COMMITTED → QUEUED two-step:** INSERT with `workflow_status='COMMITTED'`. After Edge accepts, UPDATE to `'QUEUED'`. If Edge fails, invoice stays at COMMITTED. Async worker retries later.
- **Edge-owned fields (42, 44-48):** NOT in INVOICE_INSERT. DB defaults handle them. Core's `POST /api/v1/update` endpoint (edge_update handler) is the single writer, triggered by Edge callbacks. Edge NEVER writes to DB directly.
- **payment_status:** Only PAID, PARTIAL, UNPAID active. PARTIAL is future (FIRS doesn't support it yet). DISPUTED and CANCELLED remain in CHECK constraint for forward compat.
- **ARCHIVED:** Kept in workflow_status enum. Canonical schema defines it as "closed state for historical record retention" with transitions VALIDATED→ARCHIVED and ERROR→ARCHIVED.
- **Edge failure handling:** Async retry only (no inline retries). INSERT as COMMITTED, try Edge once, if fails leave at COMMITTED, return success immediately. A new "committed invoice scanner" picks up stuck COMMITTED invoices for Edge retry. The existing queue_scanner only watches `core_queue.status` (PENDING/PROCESSING), NOT `invoices.workflow_status`.
- **service_id:** NOT stored on invoices (architect decision). Extractable from IRN string. Comes from tenant config.
- **seller_business_id / buyer_business_id:** Schema is nullable but app-level validation requires: seller_business_id MANDATORY for OUTBOUND + SELF_BILLED, buyer_business_id MANDATORY for SELF_BILLED_INVOICE. Enforcement is Transforma/edit_validator's job, not record_creator's.

**Two separate state machines identified:**
- `core_queue.status`: PENDING → PROCESSING → PROCESSED → PREVIEW_READY → **FINALIZED** (ingestion lifecycle)
- `invoices.workflow_status`: **COMMITTED** → QUEUED → TRANSMITTING → TRANSMITTED → VALIDATED → ARCHIVED (transmission lifecycle)
- Handoff point: WS5 finalize transitions BOTH — core_queue to FINALIZED, invoices to COMMITTED then QUEUED.
- **Gap found:** WS5 Part 1 does NOT update core_queue.status to FINALIZED after commit.
- **Gap found:** No scanner watches for invoices stuck at COMMITTED workflow_status. Queue scanner only reads core_queue.

---

## Invoices Table — Section M: User Identity (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 79 | `helium_user_id` | nullable | ✅ | ❌ Missing | **ADD** — finalizer identity (re-evaluated at finalize time) |
| 80 | `user_email` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 81 | `user_name` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 82 | `created_by` | nullable | ✅ | ❌ Missing | **ADD** — compat alias, same value as `helium_user_id` |

**Notes:**
- `helium_user_id` = the FINALIZER (User B), not the uploader (User A). This is the authorizing party for FIRS compliance.
- `created_by` is a compat alias for `helium_user_id`. Both carry the same value. `created_by` was the original field name before security spec alignment renamed it.
- Distinction from `user_trace_id`: trace ID tracks the DATA lifecycle (upload time, immutable). `helium_user_id` tracks the PERSON who committed the invoice (finalize time, re-evaluated).
- Currently available: `created_by` param is already passed into `commit_batch()`. Use it for both `helium_user_id` and `created_by`.
- `user_email` and `user_name` require SDK to send identity context in finalize request (DF-002). NULL for now.

---

## Invoices Table — Section N: Queue/Batch/Blob References (5 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 83 | `queue_id` | UNIQUE nullable | ✅ | ❌ Missing | **ADD** — from finalize request body (SDK sends `queue_id`) |
| 84 | `batch_id` | nullable | ✅ | ✅ `batch_id` param | Correct — already passed and written |
| 85 | `file_id` | nullable | ✅ | ❌ Missing | **ADD** — `request_context.get("file_id")`. From core_queue lookup (DF-014) |
| 86 | `blob_uuid` | nullable | ✅ | ❌ Missing | **ADD** — from core_queue lookup via router (DF-014) |
| 87 | `original_filename` | nullable | ✅ | ❌ Missing | **ADD** — from core_queue lookup. Semantically = `sourcefile_name` (the file the invoice was extracted from). Multiple invoices can map to one sourcefile_name/blob_uuid |

**Notes:**
- `original_filename` = "the source file the invoice was extracted from." NOT a UUID-renamed blob file. Example: `"Aramex_Q1_2026.xlsx"`. Multiple invoices extracted from one Excel file share the same `original_filename` and `blob_uuid`.
- Document: `original_filename` ↔ `sourcefile_name` semantic equivalence. Schema keeps `original_filename` to avoid 20+ file rename cost. Both mean the same thing.
- `blob_uuid` = HeartBeat-assigned storage identifier for the source file (not the invoice itself).
- `queue_id` = Core's internal queue entry reference. UNIQUE constraint means one queue entry → one finalize batch.
- `batch_id` is already written (correct). This is the HLX batch identifier.
- Router should enrich `request_context` from core_queue lookup on receipt of finalize request (DF-014).

---

## Invoices Table — Section O: Source System Context (2 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 88 | `source` | nullable | ✅ | ❌ Missing | **ADD** — `request_context.get("source", "BULK_UPLOAD")`. Default for now |
| 89 | `source_id` | nullable | ✅ | ❌ Missing | **ADD** — `request_context.get("source_id")`. NOT data_uuid — see below |

**Critical correction — `source_id` is NOT `data_uuid`:**
- `source_id` = HeartBeat-assigned audit-trail ID for each registered SOURCE.
- For BULK_UPLOAD: `source_id` = `float_id` (assigned by HeartBeat when Float instance registers immutably on first run)
- For ERPs: `source_id` = `connection_id` (e.g., "SAP-4HANA_HQ" has a connection_id assigned at registration)
- This is a STABLE identifier for WHERE the data came from, NOT which batch.
- `data_uuid` is a per-batch identifier (different concept). `data_uuid` is stored as `batch_id`.
- `source` is the channel type: `BULK_UPLOAD`, `API`, `EMAIL`, `POLLER`, `MANUAL`.
- Currently nobody in the pipeline sets `source` or `source_id` explicitly. For WS5 Part 2: default `source` to `"BULK_UPLOAD"`, leave `source_id` NULL until properly wired from HeartBeat registration data.

---

## Invoices Table — Section P: Display/SWDB Fields (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 90 | `reference` | nullable | ✅ | ✅ `row.get("reference")` | Correct — but see ghost columns note |
| 91 | `category` | nullable | ✅ | ✅ `row.get("category")` | **CHANGE** — set NULL, not from row. Scrapped at invoice level |
| 92 | `terms` | nullable | ✅ | ✅ `row.get("terms")` | Correct — display summary of payment terms |
| 93 | `attachment_count` | NOT NULL DEFAULT 0 | ✅ | — | No action — DB default 0. Maintained by PG trigger on invoice_attachments |

**Notes:**
- `category` at invoice level is SCRAPPED. Per-line-item `product_category` and `service_category` are the canonical category fields. SWDB will display from line items under a unified "Category" column header. Set `category = NULL` in INSERT (column exists in schema, but don't populate).
- `reference` = for credit/debit notes, the original invoice being referenced (e.g., "INV-2026-001"). Not a ghost column — this IS in the canonical schema. The GHOST columns are `reference_irn` and `reference_issue_date` which are in the current INSERT but belong in `invoice_references` table.
- `attachment_count` starts at 0. PG trigger on `invoice_attachments` table increments/decrements. When `fixed_invoice_blob_uuid` is set (extractor flow), `attachment_count` should also increment by 1. The trigger handles this IF the fixed invoice is inserted as an attachment record.
- `terms` = display-friendly payment terms summary (e.g., "Net 30", "Immediate"). Separate from `payment_terms_note` (FIRS field).

**Ghost columns in current INVOICE_INSERT (must be removed):**
- `reference_irn` — NOT in canonical invoices table. Belongs in `invoice_references` TABLE 3.
- `reference_issue_date` — NOT in canonical invoices table. Belongs in `invoice_references` TABLE 3.
- **Fix:** Remove both from INVOICE_INSERT. If row has reference data, write to `invoice_references` table separately.

---

## Invoices Table — Section Q: Notes (2 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 94 | `notes_to_firs` | nullable | ✅ | ✅ `row.get("notes_to_firs")` | Correct — formal remarks for FIRS payload (max 500 chars) |
| 95 | `payment_terms_note` | nullable | ✅ | ✅ `row.get("payment_terms_note")` | Correct — FIRS payment terms text. Also used to compute `payment_due_date` |

**New field proposed:**
- `status_notes TEXT` (JSON) — lifecycle error/warning journal. See DF-009 for full design.

---

## Invoices Table — Section Q2: ReviewPage Fields (6 fields, added v2.1.2.0)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 96 | `product_summary` | nullable | ✅ | ❌ Missing | **ADD** — computed: `field_helpers.compute_product_summary(line_items)` |
| 97 | `line_items_count` | DEFAULT 0 | ✅ | ❌ Missing | **ADD** — `len(row.get("line_items", []))` |
| 98 | `foc_line_count` | DEFAULT 0 | ✅ | ❌ Missing | **ADD** — computed: `field_helpers.compute_foc_line_count(line_items)` |
| 99 | `document_source` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("document_source")`. "workbook.xlsx / Sheet Name" |
| 100 | `other_taxes` | DEFAULT 0 | ✅ | ❌ Missing | **ADD** — `row.get("other_taxes", 0)`. Combined non-VAT taxes |
| 101 | `custom_duties` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("custom_duties")`. Import/export duty amount |

**Product summary specification:**
- Format: `"Biscuits - Digestive 400g, Palm Oil - Crude 25L, +1 more"` (max 200 chars)
- Single-line invoice: just the one description (no "+N more")
- No AI, no categorization — purely mechanical string concatenation
- Uses P0 priority field (`description`) for each line item. Falls back through P1→P4 if P0 missing.

**Line item description priority system (P0-P4):**

| Priority | Field | Source | Notes |
|----------|-------|--------|-------|
| P0 (highest) | `description` | Short one-liner (10-15 words) | Primary display |
| P1 | `full_description` | Optional detailed description | Substitution rule with P0 |
| P2 | `customer_sku` | Customer's SKU/part number | Mandatory on inventory |
| P3 | `oem_sku` | OEM SKU | Optional |
| P4 (lowest) | `helium_sku` | Assigned by WS5 on new inventory creation | Always present |

**Invoice popup display rule (click invoice_number in SWDB):**
- Renders 2 lines per line item
- Line 1: highest priority field found (P0 first)
- Line 2: next priority field found
- To be captured in SWDB documentation and possibly in schema field metadata

---

## Invoices Table — Sections R+S+T+U: Inbound + Telemetry + Audit + Machine Context

### Section R: Inbound Invoice Fields (8 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 102 | `inbound_received_at` | nullable | ✅ | — | No action — only for INBOUND direction. Set by inbound handler (future) |
| 103 | `inbound_status` | nullable (ENUM) | ✅ | — | No action — PENDING_REVIEW on arrival for INBOUND |
| 104 | `inbound_action_at` | nullable | ✅ | — | No action — set when user accepts/rejects |
| 105 | `inbound_action_by_user_id` | nullable | ✅ | — | No action |
| 106 | `inbound_action_by_user_email` | nullable | ✅ | — | No action |
| 107 | `inbound_action_reason` | nullable | ✅ | — | No action |
| 108 | `inbound_payload_json` | nullable | ✅ | — | No action |
| 109 | `reminder_count` | NOT NULL DEFAULT 0 | ✅ | — | No action — DB default. Scheduled job increments |

**Notes:**
- All 8 fields are for inbound invoice handling (future scope). WS5 finalize is outbound-focused. NULL at commit time for OUTBOUND invoices.
- For INBOUND: inbound handler sets fields 102-103 on receipt, fields 104-108 on accept/reject action.
- `inbound_action_at` (field 104) = strictly INBOUND "invoice accepted/rejected at" timestamp. This is when the user in Float clicks Accept/Reject on a received invoice.
- `acknowledgement_date` (Section C, field 23) = OUTBOUND only. When the counterparty acknowledged receipt of our invoice.
- These are DIFFERENT concepts: inbound_action_at is OUR action on THEIR invoice. acknowledgement_date is THEIR action on OUR invoice.
- Edge handles the FIRS API call for acceptance. Core sets `inbound_action_by_user_id` BEFORE sending to Edge (user identity), Edge reports back success/failure.

### Section S: Processing Telemetry (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 110 | `finalized_at` | nullable | ✅ | ❌ Missing | **ADD** — `now` (ISO timestamp at commit time) |
| 111 | `processing_started_at` | nullable | ✅ | ❌ Missing | **ADD** — from request_context (set by router at request receipt) |
| 112 | `processing_completed_at` | nullable | ✅ | ❌ Missing | **ADD** — `now` (ISO timestamp at commit time) |
| 113 | `processing_duration_ms` | nullable | ✅ | ❌ Missing | **ADD** — computed: `(completed - started)` in milliseconds |

**Notes:**
- `finalized_at` = when the user clicked Finalize (from SDK perspective). Same as `processing_completed_at` in practice.
- `processing_started_at` = when Core began processing this finalize request. Router records this as `datetime.utcnow().isoformat()` at request receipt, passes through request_context.
- `processing_duration_ms` = wall-clock time for the finalize pipeline. Helper: `field_helpers.compute_processing_duration_ms(started, completed)`.

### Section T: Audit (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 114 | `created_at` | NOT NULL DEFAULT now | ✅ | ✅ `now` | Correct |
| 115 | `updated_at` | NOT NULL DEFAULT now | ✅ | ✅ `now` | Correct |
| 116 | `deleted_at` | nullable | ✅ | — | No action — soft delete (future, admin action) |
| 117 | `deleted_by` | nullable | ✅ | — | No action — who deleted (future) |

### Section U: Machine & Session Context (5 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 118 | `machine_guid` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 119 | `mac_address` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 120 | `computer_name` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 121 | `float_id` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |
| 122 | `session_id` | nullable | ✅ | ❌ Missing | **ADD from request_context** — NULL until SDK wiring (DF-002) |

**Notes:**
- All 5 fields are machine/session context at FINALIZE time (the finalizer's machine, not the uploader's).
- Per HELIUM_SECURITY_SPEC: composite machine fingerprint = `machine_guid` + `mac_address` + `computer_name`. `float_id` is HeartBeat-assigned. `session_id` resets at 8-hour re-auth.
- All NULL until SDK sends identity context in finalize request (DF-002).

---

## Invoices Table — New Fields Proposed (Schema Bump to v2.1.3.0)

| Field | Type | Purpose | Set by | Section |
|-------|------|---------|--------|---------|
| `status_notes` | TEXT (JSON) | Lifecycle error/warning journal — Core + Edge errors | Core at commit (warnings), edge_update handler (transmission errors) | Q |
| `fixed_invoice_blob_uuid` | TEXT | Blob pointer to fixed/rendered invoice PDF (extractor flow only) | Core after PDF generation (post-commit) | P |
| `finalize_trace_id` | TEXT | SDK-generated UUIDv7 for the finalize gesture (finalizer's trace) | SDK at finalize time, passed in request | A |
| `firs_submitted_payload` | TEXT (JSON) | Exact payload Edge sent to FIRS — audit-immutable snapshot. For INBOUND: the inbound payload received from counterparty/FIRS | Core edge_update handler on successful Edge transmission (outbound) or receipt (inbound) | J |
| `payment_updated_at` | TEXT | When payment_status last changed (UNPAID→PAID etc). For invoices sent as already paid, Core uses source invoice date | Core (user action via Float SDK) | H |
| `payment_updated_by` | TEXT | helium_user_id who changed payment_status | Core (user action via Float SDK) | H |
| `customer_total_invoices_at_commit` | INTEGER | Point-in-time snapshot: counterparty's total invoices when this invoice was committed | Aggregate worker (post-commit) | Aggregate |
| `customer_lifetime_value_at_commit` | REAL | Point-in-time snapshot: counterparty's lifetime value (NGN) at commit time | Aggregate worker (post-commit) | Aggregate |
| `customer_compliance_score_at_commit` | INTEGER | Point-in-time snapshot: counterparty's compliance score (0-100) at commit time | Aggregate worker (post-commit) | Aggregate |

**Fields to REMOVE from canonical schema:**

| Field | Reason |
|-------|--------|
| `category` | Scrapped at invoice level. All category is per-line-item (`product_category`, `service_category` on `invoice_line_items`). SWDB reads from line items under unified "Category" header. |

**These require a schema version bump to v2.1.3.0 and updates to:**
- Canonical schema SQL (`06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql`)
- SDK `schema.py` INVOICES_TABLE
- Core record_creator INSERT
- Core edge_update handler (for `firs_submitted_payload`, `payment_updated_at/by`)
- Schema version table INSERT

**Aggregate worker design note:**
- Worker is entity-agnostic. It runs its defined aggregation logic and updates wherever numbers changed.
- Does NOT track which entities were affected by the batch — just recomputes everything.
- The 3 per-invoice snapshot fields (`customer_*_at_commit`) are written back by the aggregate worker AFTER it computes the current customer aggregates. The snapshot captures "what was the customer's state just after this batch was processed."
- Uses Helium Debounce pattern (DF-021): continuous with guards, not "final run." Self-draining loop until no new triggers during run+wait cycle.

**Line items schema changes (for audit self-containment):**
- Add to `invoice_line_items`: `customer_sku`, `oem_sku`, `helium_sku`, `full_description`, `classification_confidence`, `classification_source`, `vat_rate`
- These make each line item a complete product snapshot at commit time
- Covered in DF-013 (LINE_ITEM_INSERT reconciliation)

---

## Invoices Table — AUDIT COMPLETE

**Summary: 121 canonical fields → 41 currently written → ~85 to be written after fixes**

| Category | Count | Status |
|----------|-------|--------|
| Already correct | 23 | ✅ No changes |
| Missing — add to INSERT | 39 | ❌ → ✅ (this session) |
| Bug fixes (wrong name/value) | 3 | 🔧 → ✅ (this session) |
| Ghost columns to remove | 2 | ✅ Remove `reference_irn`, `reference_issue_date` |
| Edge/future — skip at commit | 23 | — DB defaults / other handlers |
| New fields (schema bump) | 3 | Proposed: `status_notes`, `fixed_invoice_blob_uuid`, `finalize_trace_id` |
| Scrapped at invoice level | 1 | `category` → set NULL |

---

## Inventory Table — Full Audit (36 canonical fields)

**Canonical schema:** `Documentation/Schema/inventory/02_INVENTORY_DB_CANONICAL_SCHEMA_V1.sql`
**Current INVENTORY_UPSERT:** 18 fields on INSERT, 11 fields on ON CONFLICT UPDATE

### Section A-C: Identity + Product Info (8 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 1 | `product_id` | PK NOT NULL | ✅ `product_id` ($1) | Correct |
| 2 | `helium_sku` | UNIQUE nullable | ❌ Missing | **ADD** — auto-generate at first insert: `f"HEL-{product_id[:8]}"` |
| 3 | `customer_sku` | NOT NULL | ✅ `item.get("customer_sku", product_id[:16])` | Correct |
| 4 | `oem_sku` | nullable | ❌ Missing | **ADD** — `item.get("oem_sku")` |
| 5 | `product_name` | NOT NULL | ✅ `item.get("description", "")` | Correct (substitution rule: line_item.description → inventory.product_name) |
| 6 | `product_name_normalized` | nullable | ✅ `name.upper().strip()` | Correct |
| 7 | `description` | nullable | ✅ `item.get("full_description")` | Correct |
| 8 | `unit_of_measure` | nullable | ✅ `item.get("unit_of_measure")` | Correct |

### Section D-E: Classification + Type (5 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 9 | `hsn_code` | nullable | ✅ `item.get("hsn_code")` | Correct |
| 10 | `service_code` | nullable | ✅ `item.get("service_code")` | Correct |
| 11 | `product_category` | nullable | ✅ `item.get("product_category")` | Correct |
| 12 | `service_category` | nullable | ✅ `item.get("service_category")` | Correct |
| 13 | `type` | NOT NULL DEFAULT 'GOODS' | ✅ Derived: `"SERVICE" if service_code else "GOODS"` | Correct |

### Section F: Tax/VAT (3 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 14 | `vat_treatment` | DEFAULT 'STANDARD' | ✅ `item.get("vat_treatment")` | Correct |
| 15 | `vat_rate` | DEFAULT 7.5 | ✅ `item.get("vat_rate", 7.5)` | Correct |
| 16 | `is_tax_exempt` | DEFAULT 0 | ❌ Missing | **ADD** — `item.get("is_tax_exempt", 0)` |

### Section G: Pricing (1 field)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 17 | `currency` | DEFAULT 'NGN' | ❌ Missing | **ADD** — `item.get("currency", "NGN")` |

### Section H: PDP Classification Intelligence (6 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 18 | `hs_codes` | nullable (JSON) | ❌ Missing | **ADD** — `item.get("hs_codes")`. ON CONFLICT: COALESCE (don't overwrite PDP data) |
| 19 | `service_codes` | nullable (JSON) | ❌ Missing | **ADD** — `item.get("service_codes")`. ON CONFLICT: COALESCE |
| 20 | `product_categories` | nullable (JSON) | ❌ Missing | **ADD** — `item.get("product_categories")`. ON CONFLICT: COALESCE |
| 21 | `service_categories` | nullable (JSON) | ❌ Missing | **ADD** — `item.get("service_categories")`. ON CONFLICT: COALESCE |
| 22 | `classification_confidence` | DEFAULT 0 | ✅ `item.get("classification_confidence", 0)` | Correct |
| 23 | `classification_source` | nullable | ✅ `item.get("classification_source")` | Correct |

### Section I: Classification Metadata (2 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 24 | `last_classified_at` | nullable | — | Skip — PDP territory |
| 25 | `last_classified_by` | nullable | — | Skip — PDP territory |

### Section J: Aggregates (5 fields — ALL moved to aggregate worker)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 26 | `total_times_invoiced` | DEFAULT 0 | In ON CONFLICT: `+ 1` | **MOVE** to aggregate worker. Remove from ON CONFLICT |
| 27 | `last_invoice_date` | nullable | In ON CONFLICT: `= EXCLUDED.updated_at` | **MOVE** to aggregate worker. Remove from ON CONFLICT |
| 28 | `total_revenue` | DEFAULT 0 | ❌ Missing | Aggregate worker territory |
| 29 | `avg_unit_price` | DEFAULT 0 | ❌ Missing | Aggregate worker territory |
| 30 | `top_customer` | nullable | ❌ Missing | Aggregate worker territory (C2 batch-level recomputation) |

### Section K-L: Notes + Audit (6 fields)

| # | Column | Nullable? | INVENTORY_UPSERT | Verdict |
|---|--------|-----------|-----------------|---------|
| 31 | `notes` | nullable | — | Skip — user-authored, not WS5 |
| 32 | `created_by` | nullable | ✅ `created_by` param | Correct |
| 33 | `updated_by` | nullable | ❌ Missing | **ADD** — same as `created_by` on insert. ON CONFLICT: `= EXCLUDED.updated_by` |
| 34 | `created_at` | NOT NULL | ✅ `now` | Correct |
| 35 | `updated_at` | NOT NULL | ✅ `now` + ON CONFLICT: `CURRENT_TIMESTAMP` | Correct |
| 36 | `deleted_at` | nullable | — | Skip — soft delete via admin |

### Inventory Audit Summary

| Category | Count |
|----------|-------|
| Already correct | 15 |
| Missing — add to INSERT | 9 (`helium_sku`, `oem_sku`, `is_tax_exempt`, `currency`, `hs_codes`, `service_codes`, `product_categories`, `service_categories`, `updated_by`) |
| Move to aggregate worker | 2 (remove `total_times_invoiced + 1` and `last_invoice_date` from ON CONFLICT) |
| Skip (PDP/user/admin territory) | 5 |
| Aggregate worker territory | 5 (computed by async worker, not at INSERT time) |

**Net change to INVENTORY_UPSERT:** INSERT grows from 18 → 27 columns. ON CONFLICT loses 2 fields, gains 5 (4 COALESCE for PDP JSON + `updated_by`).

---

## Customers Table — Audit Summary (54 canonical fields)

**Canonical schema:** `Documentation/Schema/customer/02_CUSTOMER_DB_CANONICAL_SCHEMA_V1.sql`
**Current CUSTOMER_UPSERT:** 14 fields on INSERT, 7 fields on ON CONFLICT UPDATE

### Current CUSTOMER_UPSERT coverage

Writes: `customer_id`, `tin`, `customer_name`, `customer_name_normalized`, `address`, `city`, `state_code`, `country_code`, `lga_code`, `postal_code`, `customer_type`, `company_id`, `created_at`, `updated_at`

ON CONFLICT (tin, company_id): updates `customer_name`, `address`, `city`, `state_code`, `country_code`, `lga_code`, `postal_code`, `updated_at`

### Fields to ADD to CUSTOMER_UPSERT INSERT (identity enrichment)

| Field | Source | Notes |
|-------|--------|-------|
| `rc_number` | `row.get(f"{prefix}rc_number")` | CAC RC Number — one of 3 FIRS tax identifiers |
| `tax_id` | `row.get(f"{prefix}tax_id")` | New 13-digit FIRS Tax ID |
| `email` | `row.get(f"{prefix}email")` | Counterparty email from invoice |
| `phone` | `row.get(f"{prefix}phone")` | Counterparty phone from invoice |
| `business_id` | `row.get(f"{prefix}business_id")` | FIRS business registration ID |
| `updated_by` | `created_by` | helium_user_id of finalizer |
| `created_by` | `created_by` | helium_user_id of finalizer |

### Fields NOT written by CUSTOMER_UPSERT (and shouldn't be)

**Aggregate fields (Section H — 15 fields, all aggregate worker territory):**
`total_invoices`, `average_invoice_size`, `total_transmitted`, `total_accepted`, `receivables_rejected`, `payable_rejected`, `total_pending`, `last_invoice_date`, `last_purchased_date`, `last_inbound_date`, `last_active_date`, `payable_frequency`, `receivables_frequency`, `total_lifetime_value`, `total_lifetime_tax`

**Compliance fields (computed by aggregate worker or scheduled job):**
`compliance_score`, `compliance_details`, `is_mbs_registered`

**Company info fields (user-managed, not from invoices):**
`trading_name`, `short_code`, `customer_code`, `website`, `business_description`, `primary_identifier`, `business_unit`, `default_due_date_days`, `industry`, `is_fze`

**Audit fields (skip at insert):**
`deleted_at`, `pending_sync`

### ON CONFLICT additions needed

Add COALESCE enrichment for new identity fields:
```sql
ON CONFLICT (tin, company_id) DO UPDATE SET
    customer_name = COALESCE(EXCLUDED.customer_name, customers.customer_name),
    rc_number = COALESCE(EXCLUDED.rc_number, customers.rc_number),
    tax_id = COALESCE(EXCLUDED.tax_id, customers.tax_id),
    email = COALESCE(EXCLUDED.email, customers.email),
    phone = COALESCE(EXCLUDED.phone, customers.phone),
    business_id = COALESCE(EXCLUDED.business_id, customers.business_id),
    address = COALESCE(EXCLUDED.address, customers.address),
    city = COALESCE(EXCLUDED.city, customers.city),
    ...existing fields...
    updated_by = EXCLUDED.updated_by,
    updated_at = CURRENT_TIMESTAMP
```

### Customers Audit Summary

| Category | Count |
|----------|-------|
| Already correct | 14 |
| Missing — add to INSERT | 7 (`rc_number`, `tax_id`, `email`, `phone`, `business_id`, `updated_by`, `created_by`) |
| Aggregate worker territory | 15 (per-entry aggregates, async post-commit) |
| Compliance (computed) | 3 (`compliance_score`, `compliance_details`, `is_mbs_registered`) |
| Company info (user-managed) | 10 (Float UI / admin / config) |
| Skip (admin/sync) | 5 |

**Net change to CUSTOMER_UPSERT:** INSERT grows from 14 → 21 columns. ON CONFLICT gains 5 COALESCE fields + `updated_by`.

---

## Invoices Table — Sections F+G: Delivery + Commercial References (4 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 36 | `delivery_date` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("delivery_date")` |
| 37 | `delivery_address` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("delivery_address")` |
| 38 | `purchase_order_number` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("purchase_order_number")` |
| 39 | `contract_number` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("contract_number")` |

**Notes:**
- All IBN (Include But Nullable) passthroughs. Populated when source document contains the data.
- `purchase_order_number` is critical for B2G — government MDAs require PO matching before payment approval.
- `contract_number` less common — typically for service contracts and recurring billing.

---

## Invoices Table — Section E: Payment Means + FIRS Code (2 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 34 | `payment_means` | nullable (ENUM) | ✅ | ❌ Missing | **ADD** — `row.get("payment_means")` |
| 35 | `firs_payment_means_code` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("firs_payment_means_code")` |

**Notes:**
- Both are passthroughs from Transforma. WS5 does not derive `firs_payment_means_code` from `payment_means` — Transforma owns the FIRS mapping.
- Nullable because many invoices don't specify payment method.
- Both stored at commit time for audit immutability (if FIRS code mapping changes, old invoices retain the original code).

---

## Invoices Table — Section D: Financial (10 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 24 | `document_currency_code` | NOT NULL DEFAULT 'NGN' | ✅ | 🔧 Wrong name: writes `currency_code` | **FIX** — rename column in INSERT |
| 25 | `tax_currency_code` | NOT NULL DEFAULT 'NGN' | ✅ | ❌ Missing | **ADD** — `row.get("tax_currency_code", "NGN")` |
| 26 | `subtotal` | NOT NULL DEFAULT 0.0 | ✅ | ✅ `row.get("subtotal", 0)` | Correct |
| 27 | `tax_amount` | NOT NULL DEFAULT 0.0 | ✅ | ✅ `row.get("tax_amount", 0)` | Correct |
| 28 | `total_amount` | NOT NULL DEFAULT 0.0 | ✅ | ✅ `row.get("total_amount", 0)` | Correct |
| 29 | `exchange_rate` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("exchange_rate")` |
| 30 | `has_discount` | NOT NULL DEFAULT 0 | ✅ | ❌ Missing | **ADD** — computed: 1 if any line item discount > 0 |
| 31 | `wht_amount` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("wht_amount")`, keep NULL semantics |
| 32 | `discount_amount` | nullable | ✅ | ❌ Missing | **ADD** — computed: sum of line item discounts, NULL if none |
| 33 | `adjustment_type` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("adjustment_type")` |

**Notes:**
- `currency_code` in record_creator.py is a **bug** — column doesn't exist in canonical or sync.db. Must be `document_currency_code`.
- `document_currency_code` vs `tax_currency_code`: usually both NGN. Differ for cross-currency B2B (invoice in USD, tax in NGN, linked by `exchange_rate`).
- `has_discount` is boolean convenience (0/1). `discount_amount` is the precise value. Both needed — has_discount for quick filtering, discount_amount for display.
- `wht_amount` keeps NULL semantics: NULL = WHT not applicable, 0.0 = WHT evaluated as zero. Don't default to 0.
- `adjustment_type` only populated for credit/debit notes (PRICE_ADJUSTMENT, FULL_CANCELLATION, QUANTITY_CORRECTION, RETURN). NULL for standard invoices. More on credit notes at Section P/references.

---

## Invoices Table — Section C: Dates (7 fields)

| # | Column | Nullable? | sync.db | record_creator.py | Verdict |
|---|--------|-----------|---------|-------------------|---------|
| 17 | `issue_date` | NOT NULL | ✅ | ✅ `row.get("issue_date")` | Correct |
| 18 | `issue_time` | nullable | ✅ | ❌ Missing | **ADD** — `row.get("issue_time")` |
| 19 | `due_date` | nullable | ✅ | ✅ `row.get("due_date")` | Correct |
| 20 | `payment_due_date` | nullable | ✅ | ❌ Missing | **ADD** — computed: `issue_date + "Net N"` from `payment_terms_note` |
| 21 | `sign_date` | nullable | ✅ | — | No action — Edge sets at signing |
| 22 | `transmission_date` | nullable | ✅ | — | No action — Edge sets at FIRS transmission |
| 23 | `acknowledgement_date` | nullable | ✅ | — | No action — Edge sets on counterparty ack |

**Notes:**
- `payment_due_date` ≠ `due_date`. `due_date` is document-stated (from source PDF). `payment_due_date` is platform-computed (issue_date + Net N days). Both kept independently.
- `issue_time` is optional — most invoices are date-only. Transforma extracts when present (e.g., POS receipts, real-time B2G).

---

# PART II — ACTION REGISTER

## Immediate Fixes (WS5 Part 2 — this session)

### IF-001: Add `helium_invoice_no` to INVOICE_INSERT
- **Source:** Section A, field 3
- **Type:** Bug — NOT NULL UNIQUE column missing
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Risk:** HIGH — INSERT fails on real PostgreSQL with NOT NULL constraint
- **Agreed fix:** Add column to INSERT. Create helper `field_helpers.build_helium_invoice_no(company_id, invoice_id)` returning `f"PRO-{company_id}-{invoice_id}"`. Call in `_commit_invoice()`. Prefix is `PRO` (Prodeus), not `WM`.

### IF-002: Add `invoice_trace_id` to INVOICE_INSERT
- **Source:** Section A, field 8
- **Type:** Missing field
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Generate `str(uuid7())` fresh per invoice at commit time. Core's responsibility per canonical spec. One trace per invoice, not per batch. Uses `uuid6` package (already in codebase).

### IF-003: Add `user_trace_id` to INVOICE_INSERT
- **Source:** Section A, field 9
- **Type:** Missing field — originator trace
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Source: `request_context.get("user_trace_id")`. Will be NULL until SDK wiring lands (DF-002). This is the ORIGINATOR's trace (upload time), NOT the finalizer's. Immutable across preview → finalize lifecycle.

### IF-004: Add `x_trace_id` to INVOICE_INSERT
- **Source:** Section A, field 10
- **Type:** Missing field — Relay infrastructure trace
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Source: `request_context.get("x_trace_id")`. Will be NULL until Relay wiring lands (DF-001) and SDK passes it through (DF-002). Immutable from upload onward.

### IF-005: Add `config_version_id` to INVOICE_INSERT
- **Source:** Section A, field 11
- **Type:** Missing field — config snapshot reference
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Source: `request_context.get("config_version_id")`. Pipeline/router populates from tenant config cache when available. NULL until config versioning system is in place.

### IF-006: Add `schema_version_applied` to INVOICE_INSERT
- **Source:** Section A, field 12
- **Type:** Missing field — schema audit trail
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Query `SELECT MAX(version) FROM schema_version` at app startup, cache as class attribute on `RecordCreator`. Write cached value per invoice. Avoids hardcoding; auto-updates when schema migrates.

### IF-007: Add `document_type` to INVOICE_INSERT
- **Source:** Section B, field 14
- **Type:** Bug — NOT NULL column missing, causes silent data corruption
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Risk:** MEDIUM — DB default masks the bug but creates contradictions with `firs_invoice_type_code`
- **Agreed fix:** Add column to INSERT. Value: `row.get("document_type", "COMMERCIAL_INVOICE")`. Simple passthrough from Transforma output.

### IF-008: Add `issue_time` to INVOICE_INSERT
- **Source:** Section C, field 18
- **Type:** Missing field — optional time component
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("issue_time")`. Simple passthrough — NULL for most invoices, populated when Transforma extracts a timestamp (POS receipts, real-time B2G).

### IF-009: Add `payment_due_date` to INVOICE_INSERT
- **Source:** Section C, field 20
- **Type:** Missing field — platform-computed due date
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Create helper `field_helpers.compute_payment_due_date(issue_date, payment_terms_note)` that parses "Net N" regex from `payment_terms_note`, returns `issue_date + N days` as ISO string, or NULL if unparseable. Independent of `due_date` (document-stated).

### IF-010: Fix `currency_code` → `document_currency_code` in INVOICE_INSERT
- **Source:** Section D, field 24
- **Type:** Bug — column name mismatch, INSERT will fail on real PostgreSQL
- **File:** `record_creator.py` → `INVOICE_INSERT` line 71, `_commit_invoice()` line 237
- **Risk:** HIGH — phantom column, fails on canonical schema
- **Agreed fix:** Rename column in INSERT from `currency_code` to `document_currency_code`. Value: `row.get("document_currency_code") or row.get("currency_code", "NGN")` — try canonical name first, fall back to legacy key for backward compat with older Transforma outputs.

### IF-011: Add `tax_currency_code` to INVOICE_INSERT
- **Source:** Section D, field 25
- **Type:** Missing field — NOT NULL column
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("tax_currency_code", "NGN")`. Usually same as document_currency_code for domestic invoices.

### IF-012: Add `exchange_rate` to INVOICE_INSERT
- **Source:** Section D, field 29
- **Type:** Missing field — cross-currency support
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("exchange_rate")`. Simple passthrough — NULL for domestic NGN invoices.

### IF-013: Add `has_discount` to INVOICE_INSERT
- **Source:** Section D, field 30
- **Type:** Missing field — NOT NULL convenience boolean
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Create helper `field_helpers.compute_has_discount(line_items)` — returns 1 if any line item `discount_amount > 0`, else 0.

### IF-014: Add `wht_amount` to INVOICE_INSERT
- **Source:** Section D, field 31
- **Type:** Missing field — Withholding Tax denormalized display
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("wht_amount")`. Keep NULL semantics — NULL means WHT not applicable (don't default to 0). Passthrough from Transforma.

### IF-015: Add `discount_amount` to INVOICE_INSERT
- **Source:** Section D, field 32
- **Type:** Missing field — precise discount value
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Create helper `field_helpers.compute_discount_amount(line_items)` — sums line item `discount_amount` values. Returns sum if any discount exists, NULL if all zero/absent (maintain NULL = no discount semantics).

### IF-016: Add `adjustment_type` to INVOICE_INSERT
- **Source:** Section D, field 33
- **Type:** Missing field — credit/debit note classification
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("adjustment_type")`. Passthrough from Transforma. NULL for standard commercial invoices. Only populated for CREDIT_NOTE/DEBIT_NOTE document types.

### IF-017: Add `payment_means` to INVOICE_INSERT
- **Source:** Section E, field 34
- **Type:** Missing field — payment method for SWDB display + FIRS payload
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("payment_means")`. Passthrough from Transforma. Nullable. ENUM values: CASH, CHEQUE, BANK_TRANSFER, CARD, MOBILE_MONEY, DIGITAL_WALLET, OFFSET, OTHER.

### IF-018: Add `firs_payment_means_code` to INVOICE_INSERT
- **Source:** Section E, field 35
- **Type:** Missing field — FIRS numeric payment code
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("firs_payment_means_code")`. Passthrough from Transforma. WS5 does NOT derive this from `payment_means` — Transforma owns the FIRS mapping.

### IF-019: Add `delivery_date` to INVOICE_INSERT
- **Source:** Section F, field 36
- **Type:** Missing field — IBN passthrough
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("delivery_date")`. Nullable passthrough.

### IF-020: Add `delivery_address` to INVOICE_INSERT
- **Source:** Section F, field 37
- **Type:** Missing field — IBN passthrough
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("delivery_address")`. Nullable passthrough.

### IF-021: Add `purchase_order_number` to INVOICE_INSERT
- **Source:** Section G, field 38
- **Type:** Missing field — critical for B2G PO matching
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("purchase_order_number")`. Nullable passthrough.

### IF-022: Add `contract_number` to INVOICE_INSERT
- **Source:** Section G, field 39
- **Type:** Missing field — IBN passthrough
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `row.get("contract_number")`. Nullable passthrough.

### IF-023: Fix `status` → `workflow_status` in INVOICE_INSERT (TWO BUGS)
- **Source:** Section H, field 40
- **Type:** Bug — wrong column name AND wrong value
- **File:** `record_creator.py` → `INVOICE_INSERT` line 85, `_commit_invoice()` line 267
- **Risk:** HIGH — (a) column `status` doesn't exist in canonical schema, INSERT fails. (b) value `"FINALIZED"` not in CHECK constraint, INSERT fails.
- **Agreed fix:** Rename column from `status` to `workflow_status`. Change value from `"FINALIZED"` to `"COMMITTED"`. After Edge accepts in pipeline step 4, UPDATE to `"QUEUED"`. Two-step state transition: COMMITTED at INSERT, QUEUED after Edge submit.

### IF-024: Add `transmission_status` to INVOICE_INSERT
- **Source:** Section H, field 41
- **Type:** Missing field — NOT NULL with DB default, but explicit is better for clarity
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `"NOT_REQUIRED"` (always at commit time). Edge transitions this through the 13-state enum via Core's edge_update handler.

### IF-025: Add `payment_status` to INVOICE_INSERT
- **Source:** Section H, field 43
- **Type:** Missing field — NOT NULL with DB default
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add column to INSERT. Value: `"UNPAID"` (always at commit time). Only PAID, PARTIAL, UNPAID are active. User updates via Float (permission-gated).

### IF-026: Add COMMITTED→QUEUED status update after Edge submission
- **Source:** Section H, pipeline sequencing discussion
- **Type:** New logic — two-step status transition
- **File:** `pipeline.py` → `finalize()` after step 4 (Edge submit)
- **Agreed fix:** After `edge_client.submit_batch()` succeeds, execute `UPDATE invoices SET workflow_status = 'QUEUED', updated_at = $now WHERE batch_id = $batch_id AND workflow_status = 'COMMITTED'`. If Edge fails, leave at COMMITTED. No inline retries — async retry only. Log errors with batch_id + invoice IDs.

### IF-028 to IF-031: Add 4 user/identity fields to INVOICE_INSERT
- **Source:** Section M, fields 79-82
- **Type:** Missing fields — finalizer identity
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 4 columns to INSERT:
  - `helium_user_id` → `created_by` param or `request_context.get("helium_user_id")`. The FINALIZER (not uploader)
  - `user_email` → `request_context.get("user_email")`. NULL until SDK wiring (DF-002)
  - `user_name` → `request_context.get("user_name")`. NULL until SDK wiring
  - `created_by` → same value as `helium_user_id` (compat alias)

### IF-032 to IF-035: Add 4 queue/blob/source reference fields to INVOICE_INSERT
- **Source:** Sections N+O, fields 83, 85-89
- **Type:** Missing fields — provenance chain
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 4 columns to INSERT + 2 source fields:
  - `queue_id` → from finalize request body (SDK already sends this)
  - `file_id` → `request_context.get("file_id")`. May be NULL
  - `blob_uuid` → from core_queue lookup via router (DF-014)
  - `original_filename` → from core_queue lookup via router (DF-014)
  - `source` → `request_context.get("source", "BULK_UPLOAD")`. Not wired yet (DF-012), defaults to BULK_UPLOAD
  - `source_id` → from core_queue `data_uuid`

### IF-036: Add `status_notes` to INVOICE_INSERT (NEW FIELD — schema bump)
- **Source:** Section Q discussion, lifecycle error journal
- **Type:** New field — schema addition to canonical invoices table
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`; canonical schema; SDK schema.py
- **Agreed fix:** Add `status_notes TEXT` (JSON-encoded) to INSERT. Set from pipeline warnings at commit time: `json.dumps([{"timestamp": now, "service": "core", "level": w.level, "message": w.message, "resolved": False} for w in warnings])`. NULL if no warnings. Schema version bump to 2.1.3.0. Full design in DF-009.

### IF-037 to IF-042: Add 6 ReviewPage fields to INVOICE_INSERT
- **Source:** Section Q2, fields 96-101
- **Type:** Missing fields — v2.1.2.0 ReviewPage rendering
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 6 columns to INSERT:
  - `product_summary` → computed: helper `field_helpers.compute_product_summary(line_items)`. String concat "Item1, Item2, +N more" (max 200 chars). No AI — pure string manipulation
  - `line_items_count` → `len(row.get("line_items", []))`
  - `foc_line_count` → computed: helper `field_helpers.compute_foc_line_count(line_items)`. Count of unit_price == 0
  - `document_source` → `row.get("document_source")`. "workbook.xlsx / Sheet Name" format. Transforma sets this
  - `other_taxes` → `row.get("other_taxes", 0)`. Combined non-VAT taxes (WHT, levies, excise)
  - `custom_duties` → `row.get("custom_duties")`. Customs duty for import/export invoices

### IF-043 to IF-048: Add 6 missing seller fields to INVOICE_INSERT
- **Source:** Section K, fields 51-52, 55-58
- **Type:** Missing fields — seller party completeness
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 6 columns to INSERT, all simple passthroughs:
  - `seller_id` → `row.get("seller_id")`
  - `seller_business_id` → `row.get("seller_business_id")` (MANDATORY for OUTBOUND+SELF_BILLED — enforced by Transforma, not WS5)
  - `seller_tax_id` → `row.get("seller_tax_id")`
  - `seller_rc_number` → `row.get("seller_rc_number")`
  - `seller_email` → `row.get("seller_email")`
  - `seller_phone` → `row.get("seller_phone")`

### IF-034 to IF-039: Add 6 missing buyer fields to INVOICE_INSERT
- **Source:** Section L, fields 65-66, 69-72
- **Type:** Missing fields — buyer party completeness
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoke()`
- **Agreed fix:** Add 6 columns to INSERT, all simple passthroughs:
  - `buyer_id` → `row.get("buyer_id")`
  - `buyer_business_id` → `row.get("buyer_business_id")` (MANDATORY for SELF_BILLED_INVOICE — enforced by Transforma, not WS5)
  - `buyer_tax_id` → `row.get("buyer_tax_id")`
  - `buyer_rc_number` → `row.get("buyer_rc_number")`
  - `buyer_email` → `row.get("buyer_email")`
  - `buyer_phone` → `row.get("buyer_phone")`

### IF-049 to IF-052: Add 4 processing telemetry fields to INVOICE_INSERT
- **Source:** Section S, fields 110-113
- **Type:** Missing fields — finalize timing data
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 4 columns to INSERT:
  - `finalized_at` → `now` (ISO timestamp at commit time)
  - `processing_started_at` → `request_context.get("processing_started_at")` (set by router at request receipt)
  - `processing_completed_at` → `now` (ISO timestamp at commit time)
  - `processing_duration_ms` → computed: `field_helpers.compute_processing_duration_ms(started, completed)`

### IF-053 to IF-057: Add 5 machine/session context fields to INVOICE_INSERT
- **Source:** Section U, fields 118-122
- **Type:** Missing fields — finalizer machine fingerprint
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 5 columns to INSERT, all from request_context (NULL until SDK wiring DF-002):
  - `machine_guid` → `request_context.get("machine_guid")`
  - `mac_address` → `request_context.get("mac_address")`
  - `computer_name` → `request_context.get("computer_name")`
  - `float_id` → `request_context.get("float_id")`
  - `session_id` → `request_context.get("session_id")`

### IF-058: Set `category` to NULL in INVOICE_INSERT (scrapped at invoice level)
- **Source:** Section P, field 91
- **Type:** Design change — invoice-level category scrapped
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Change from `row.get("category")` to `None`. Per-line-item `product_category` and `service_category` are the canonical category fields. SWDB displays from line items under unified "Category" header.

### IF-059: Remove ghost columns `reference_irn` and `reference_issue_date` from INVOICE_INSERT
- **Source:** Section P, ghost column analysis
- **Type:** Bug — columns not in canonical invoices table (belong in `invoice_references` TABLE 3)
- **File:** `record_creator.py` → `INVOICE_INSERT` lines 84-85, `_commit_invoice()` lines 264-265
- **Risk:** MEDIUM — INSERT fails on real PostgreSQL (columns don't exist)
- **Agreed fix:** Remove both columns from INVOICE_INSERT. Add new `REFERENCE_INSERT` for `invoice_references` table. Execute only when `row.get("reference_irn")` is present. This correctly puts credit/debit note reference data in the child table where it belongs.

### IF-060: Add `source` and `source_id` to INVOICE_INSERT
- **Source:** Section O, fields 88-89
- **Type:** Missing fields — source provenance
- **File:** `record_creator.py` → `INVOICE_INSERT`, `_commit_invoice()`
- **Agreed fix:** Add 2 columns:
  - `source` → `request_context.get("source", "BULK_UPLOAD")`. Default until properly wired.
  - `source_id` → `request_context.get("source_id")`. NULL until wired. NOT data_uuid — source_id is the HeartBeat-assigned ID for the source system (float_id for BULK_UPLOAD, connection_id for ERPs).

### IF-027: Update core_queue.status to FINALIZED after commit
- **Source:** Section H, queue lifecycle gap
- **Type:** Bug — core_queue never transitions to FINALIZED after WS5 commit
- **File:** `pipeline.py` → `finalize()` after step 3 (commit), OR `router.py`
- **Agreed fix:** After `record_creator.commit_batch()` succeeds, execute `UPDATE core_queue SET status = 'FINALIZED', updated_at = NOW() WHERE queue_id = $queue_id`. The `queue_id` comes from `request_context`. This completes the ingestion lifecycle (PREVIEW_READY → FINALIZED).

---

## Inventory Table — Immediate Fixes

### IF-061: Add `helium_sku` to INVENTORY_UPSERT INSERT
- **Source:** Inventory audit, field 2
- **Type:** Missing field — auto-generated at first insert
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT. Generate `f"HEL-{product_id[:8]}"` for new records. ON CONFLICT: `helium_sku = inventory.helium_sku` (never overwrite — stable once assigned).

### IF-062: Add `oem_sku` to INVENTORY_UPSERT INSERT
- **Source:** Inventory audit, field 4
- **Type:** Missing field — manufacturer SKU passthrough
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT. Value: `item.get("oem_sku")`. ON CONFLICT: COALESCE (don't overwrite existing).

### IF-063: Add `is_tax_exempt` to INVENTORY_UPSERT INSERT
- **Source:** Inventory audit, field 16
- **Type:** Missing field — tax exempt flag
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT. Value: `item.get("is_tax_exempt", 0)`. ON CONFLICT: `= EXCLUDED.is_tax_exempt`.

### IF-064: Add `currency` to INVENTORY_UPSERT INSERT
- **Source:** Inventory audit, field 17
- **Type:** Missing field — pricing currency
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT. Value: `item.get("currency", "NGN")`. ON CONFLICT: `= EXCLUDED.currency`.

### IF-065: Add 4 PDP JSON array fields to INVENTORY_UPSERT INSERT
- **Source:** Inventory audit, fields 18-21
- **Type:** Missing fields — PDP classification intelligence
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT: `hs_codes`, `service_codes`, `product_categories`, `service_categories`. All from `item.get()`. ON CONFLICT: `= COALESCE(inventory.X, EXCLUDED.X)` — preserve existing PDP data, only fill in if currently NULL.

### IF-066: Add `updated_by` to INVENTORY_UPSERT
- **Source:** Inventory audit, field 33
- **Type:** Missing field — audit trail
- **File:** `record_creator.py` → `INVENTORY_UPSERT`, `_upsert_inventory_from_line_items()`
- **Agreed fix:** Add to INSERT: `updated_by = created_by`. ON CONFLICT: `updated_by = EXCLUDED.updated_by`.

### IF-067: Remove `total_times_invoiced` and `last_invoice_date` from INVENTORY_UPSERT ON CONFLICT
- **Source:** Inventory audit, fields 26-27
- **Type:** Move to aggregate worker — per-entry aggregates are non-critical path
- **File:** `record_creator.py` → `INVENTORY_UPSERT` ON CONFLICT clause
- **Agreed fix:** Remove `total_times_invoiced = inventory.total_times_invoiced + 1` and `last_invoice_date = EXCLUDED.updated_at` from ON CONFLICT. These are computed by the aggregate worker (per-entry aggregates, same connection, new transaction, non-blocking).

---

## Customers Table — Immediate Fixes

### IF-068: Add 7 identity fields to CUSTOMER_UPSERT INSERT
- **Source:** Customers audit
- **Type:** Missing fields — counterparty identity enrichment
- **File:** `record_creator.py` → `CUSTOMER_UPSERT`, `_upsert_customer()`
- **Agreed fix:** Add to INSERT: `rc_number`, `tax_id`, `email`, `phone`, `business_id`, `updated_by`, `created_by`. All sourced from `row.get(f"{prefix}...")` using the existing direction-based prefix logic. `created_by` and `updated_by` from `created_by` param.

### IF-069: Add 5 COALESCE fields to CUSTOMER_UPSERT ON CONFLICT
- **Source:** Customers audit
- **Type:** Missing enrichment on update
- **File:** `record_creator.py` → `CUSTOMER_UPSERT` ON CONFLICT clause
- **Agreed fix:** Add to ON CONFLICT:
  - `rc_number = COALESCE(EXCLUDED.rc_number, customers.rc_number)`
  - `tax_id = COALESCE(EXCLUDED.tax_id, customers.tax_id)`
  - `email = COALESCE(EXCLUDED.email, customers.email)`
  - `phone = COALESCE(EXCLUDED.phone, customers.phone)`
  - `business_id = COALESCE(EXCLUDED.business_id, customers.business_id)`
  - `updated_by = EXCLUDED.updated_by`

---

## Deferred Fixes (Other teams / future sessions)

### DF-001: Relay — merge `x_trace_id` into metadata
- **Source:** Section A wiring gap #1
- **Service:** Relay
- **File:** `Relay/src/services/ingestion.py` lines 175-181
- **Impact:** HeartBeat stores `x_trace_id = NULL` on all blob records until this is fixed
- **Recommended scope:** This session (3 lines, surgical, low risk)
- **Agreed fix:** Before calling HeartBeat `write_blob()` and `register_blob()`, merge Relay's trace_id into the metadata dict: `metadata["x_trace_id"] = request.state.trace_id`. Same merge before Core `enqueue()` call. Three insertion points, one line each.

### DF-002: SDK — extend finalize request with trace + identity context
- **Source:** Section A wiring gap #2
- **Service:** Float SDK
- **Files:** `core_client.py`, `finalize_worker.py`, `upload_flow.py`, `result_page.py`
- **Key design point:** User identity is RE-EVALUATED at finalize time (finalizer ≠ uploader). Traces are IMMUTABLE from upload.
- **Recommended scope:** This session (~40 lines, well-defined plumbing)
- **Agreed fix:** Four changes:
  1. `finalize_worker.py` — add `finalize_context: dict | None = None` param to `__init__()`, pass to `core_client.finalize()`
  2. `core_client.py` — add `finalize_context: dict | None = None` param to `finalize()`, merge into JSON payload
  3. `upload_flow.py` — build `finalize_context` dict from `self._upload_manager` (identity, machine fingerprint, user_trace_id from blob_batches, x_trace_id from upload response) and pass to `FinalizeWorker`
  4. `result_page.py` — same as upload_flow.py for the ResultPage finalize path
  - Context dict has two categories: (A) Data traces from upload — `user_trace_id`, `x_trace_id` (immutable); (B) Finalizer identity — `helium_user_id`, `user_email`, `user_name`, `machine_guid`, `mac_address`, `computer_name`, `float_id`, `session_id` (current user at finalize time)

### DF-003: Core WS1 — add trace columns to core_queue
- **Source:** Section A wiring gap #3
- **Service:** Core
- **File:** `Core/src/database/schemas/core.sql`, `queue_repository.py`
- **Recommended scope:** This session (10 lines)
- **Agreed fix:** Add `user_trace_id TEXT` and `x_trace_id TEXT` nullable columns to `core_queue` CREATE TABLE. In `queue_repository.py enqueue()`, extract from `queue_data.get("metadata", {})` and store. Belt-and-suspenders — SDK finalize request (DF-002) is the primary trace path, but queue should carry traces for operational completeness.

### DF-004: HeartBeat — add processing stats columns to blob tables
- **Source:** Canonical blob schema vs HeartBeat schema.sql gap
- **Service:** HeartBeat
- **Files:** `HeartBeat/databases/schema.sql`, new migration `005_add_processing_stats.sql`
- **Recommended scope:** DEFER to HeartBeat session — needs endpoint + migration + tests
- **Agreed fix (when built):**
  - Add to `blob_batches`: `total_invoice_count INTEGER`, `total_rejected_count INTEGER`, `total_submitted_count INTEGER`, `total_duplicate_count INTEGER` (all nullable, Core-populated)
  - Add to `blob_entries`: `extracted_invoice_count INTEGER`, `rejected_invoice_count INTEGER`, `submitted_invoice_count INTEGER`, `duplicate_count INTEGER` (all nullable, Core-populated)
  - New HeartBeat API endpoint: `PATCH /api/internal/blobs/{batch_uuid}/stats` for Core to write back processing stats after finalize
  - Core's async aggregate worker (IF-xxx, TBD) calls this endpoint post-commit

### DF-005: Canonical docs — PRO- prefix
- **Source:** Section A, field 3 decision
- **Recommended scope:** This session (find-replace, low risk)
- **Agreed fix:** Replace `WM-{TenantID}` with `PRO-{TenantID}` in:
  - `Documentation/Schema/invoice/01_CANONICAL_FIELD_LIST_V1.md` line 43
  - `Documentation/Schema/invoice/generate_sample_invoices_db.py` line 301
  - `Float/App/src/sdk/database/models.py` line 333
  - `06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` comments (lines 152-154)
  - `Core/src/database/schemas/invoices.sql` comments (if present)

### DF-007: Committed Invoice Scanner (Edge retry for stuck invoices)
- **Source:** Section H, Edge failure handling + queue lifecycle analysis
- **Service:** Core
- **Agreed concept:** New background task (or extension of existing queue_scanner) that periodically scans `invoices` table for `workflow_status = 'COMMITTED'` entries older than N minutes. These are invoices committed to DB but where Edge submission failed. Scanner retries Edge submission and updates to QUEUED on success. After max retries, updates to ERROR. Uses `invoice_transmission_attempts` table for structured error logging.
- **Why needed:** The existing `queue_scanner` only reads `core_queue.status` (PENDING/PROCESSING). It has no visibility into `invoices.workflow_status`. Without this scanner, invoices stuck at COMMITTED due to Edge failure would never be retried unless the user manually triggers `POST /api/v1/retry`.
- **Recommended scope:** This session if time permits, otherwise next session. The logic is similar to queue_scanner (poll loop, threshold, retry count).

### DF-008: Transforma/edit_validator — enforce mandatory party fields per direction
- **Source:** Section K+L discussion
- **Service:** Core (Transforma + edit_validator)
- **Agreed concept:** App-level validation (not SQL constraint) to enforce:
  - `seller_business_id` MANDATORY for direction IN ('OUTBOUND', 'SELF_BILLED_INVOICE', 'SELF_BILLED_CREDIT')
  - `buyer_business_id` MANDATORY for direction = 'SELF_BILLED_INVOICE'
- WS5 (record_creator) does NOT enforce this — it's a faithful committer. Transforma/edit_validator should reject invalid data before it reaches WS5.

### DF-009: Add `status_notes` JSON column to canonical invoice schema
- **Source:** Section Q discussion, lifecycle error journal
- **Type:** New field — schema addition, version bump to 2.1.3.0
- **Agreed fix:** Add `status_notes TEXT` (JSON-encoded) to invoices table. Captures lifecycle errors/warnings from ALL services (Core, Edge, Relay) with structure:
  ```json
  [{"timestamp": "...", "service": "core|edge", "level": "info|warning|error", "message": "...", "resolved": false}]
  ```
  - Shows in SWDB only when status is ERROR, hidden when resolved
  - Feeds into full audit trail during trace/log analysis
  - Encapsulates both Core processing errors and Edge transmission errors
  - Set at commit time from `FinalizeResult.warnings`; appended by edge_update handler on failures
- **Files to update:** Canonical schema SQL, SDK schema.py, Core INSERT, Core edge_update handler

### DF-010: Add `fixed_invoice_blob_uuid` to canonical invoice schema
- **Source:** Section P discussion, "View Fixed Invoice" in SWDB
- **Type:** New field — schema addition
- **Agreed fix:** Add `fixed_invoice_blob_uuid TEXT` to invoices table. Stores blob storage pointer to the generated fixed/rendered invoice PDF. Set after finalize when PDF renderer generates the document. SWDB "View Fixed Invoice" button uses this to fetch from HeartBeat blob store. `attachment_count` should also increment by 1 when fixed invoice is generated.

### DF-011: Harmonize sync.db with updated invoices.db and HLX
- **Source:** Architect instruction at start of Sections M-Q2
- **Type:** Cross-layer alignment
- **Agreed fix:** After all schema changes are finalized (new fields like `status_notes`, `fixed_invoice_blob_uuid`, PRO- prefix), update:
  1. SDK `schema.py` INVOICES_TABLE to match canonical schema
  2. HLX format spec if any new fields affect the .hlx/.hlm structure
  3. Verify live `sync.db` at `Float/App/data/sync.db` has all columns (may need migration)

### DF-012: Source pipeline wiring (source + source_id)
- **Source:** Section O discussion
- **Type:** Pipeline gap — source/source_id not wired from origin to commit
- **Agreed concept:** `source` should be set by whoever initiates the invoice (SDK for BULK_UPLOAD/MANUAL, API caller for API, Relay poller for POLLER, email handler for EMAIL). Currently nobody sets it. `source_id` = `data_uuid` from Relay. For WS5 Part 2: default `source` to `"BULK_UPLOAD"`, get `source_id` from queue entry `data_uuid`. Proper wiring deferred to when other ingestion paths are built.

### DF-013: LINE_ITEM_INSERT ghost columns
- **Source:** Line items reconciliation (discovered during M-Q2 discussion)
- **Type:** Bug — record_creator.py writes columns that don't exist in canonical schema
- **Ghost columns in LINE_ITEM_INSERT:** `unit_of_measure`, `discount_amount`, `vat_treatment` — NOT in canonical `invoice_line_items` table (TABLE 2)
- **Canonical has but LINE_ITEM_INSERT misses:** `line_item_type`, `tax_rate`, `product_code`, `product_name`, `classification_confidence`, `classification_source`
- **Agreed fix:** Full line items reconciliation needed (similar to invoices audit). Defer to implementation phase — fix alongside INVOICE_INSERT.

### DF-014: Router should enrich request_context from core_queue lookup
- **Source:** Section N discussion
- **Type:** Implementation detail
- **Agreed fix:** When finalize request arrives with `queue_id`, the router looks up the `core_queue` entry and extracts `blob_uuid`, `original_filename`, `data_uuid` to populate `request_context`. This avoids requiring the SDK to send these fields (they're already stored from the ingestion phase).

### DF-015: Build SWDB field reference (every field → who sets, when, where displayed, FIRS mapping)
- **Source:** Architect instruction at start of Sections M-Q2
- **Type:** Documentation deliverable
- **Agreed fix:** After the audit is complete, build a comprehensive reference table for all invoice fields with columns: Field, Type, Set By, When Set, SWDB Column, FIRS Mapping. Deliverable at end of session.

### DF-016: Design — tenant_invoice_metrics table (dedicated workstream)
- **Source:** Sections M-Q2 aggregate discussion
- **Type:** New schema — substantial (40+ fields, scheduled recomputation, daily snapshots)
- **Status:** Concept designed, implementation deferred to dedicated workstream
- **Agreed concept:** `tenant_invoice_metrics` table with one row per `company_id`, carrying:
  - **Volume metrics:** total_invoices_all_time, per-direction counts, credit/debit note counts
  - **Status distribution:** total_committed, total_queued, total_transmitted, total_validated, total_errored, total_archived
  - **Time-windowed:** invoices/rejected counts for 7d/30d/90d rolling windows
  - **Financial:** total_value, total_tax, averages, 30d windowed
  - **Quality/Compliance:** rejection_rate, first_pass_success_rate, avg_processing_time, composite compliance_score (0-100) with 5-component JSON breakdown (first_pass_rate 25%, rejection_rate 25%, timeliness 20%, data_quality 15%, payment_tracking 15%)
  - **Drift indicators:** volume/rejection/value drift 7d vs 30d (% change)
  - **Payment:** total_unpaid, total_paid, total_overdue, total_overdue_value
- **Computation model:**
  - Post-finalize aggregate worker: increments snapshot metrics (counts, status distribution)
  - Daily scheduled job: recomputes windowed metrics, drift, compliance score
  - Edge_update handler: updates status distribution in real time on status changes
- **Also needs:** `tenant_invoice_metrics_history` table for daily snapshots (trend analysis)
- **Flash Card data requirements (currently ALL MOCK in `stats_flash_card.py`):**
  - **VIEW 0 (Transmission Summary):** acceptance_rate, total_tax_all_time (VAT recorded), invoices_last_7_days, total_errored, compliance_score
  - **VIEW 1 (Revenue Histogram):** hourly/daily/weekly/monthly revenue buckets — too granular for static table, needs materialized view `tenant_revenue_buckets` refreshed daily + real-time for last 5 days
  - **VIEW 2 (Compliance Sparklines):** 6-point time series of filing_rate, error_resolution_rate, avg_processing_time — requires `tenant_invoice_metrics_history` daily snapshots
  - Flash Card file: `Float/App/src/swdb/ui/stats_flash_card.py` (2600+ lines, mock data at lines 99-263)
- **Recommended scope:** Dedicated workstream after WS5 Part 2 completes

### DF-017: WS-SCANNER — Universal Core Scanner Module
- **Source:** Section H discussion, committed invoice scanner + future scanning needs
- **Type:** New module — generalizes existing `queue_scanner.py`
- **Agreed concept:** Single scanner module that handles ALL pending-state recovery across Core. Runs periodically and scans for:
  - `core_queue` entries stuck in PROCESSING (existing queue_scanner logic)
  - Invoices stuck at COMMITTED (Edge never received them) — retry Edge submission
  - Invoices stuck at QUEUED (Edge accepted but never progressed) — alert/escalate
  - Any future identifiable "stuck" states
- When it runs, it scans Core ENTIRELY for pendings — not just one table.
- **Recommended scope:** Dedicated implementation after WS5 Part 2

### DF-018: Document inter-service payload schemas (SDK→Relay→Core)
- **Source:** M-Q2 discussion
- **Type:** Documentation + contract enforcement
- **Agreed concept:** Pydantic models for each inter-service payload. What SDK sends to Relay, how Relay sends to Core, what Core expects at each endpoint. Currently no formal schemas exist for the ingest and finalize request chains.
- **Deliverable:** Pydantic models in a shared `helium_contracts/` package or documented per-service.
- **Recommended scope:** Start of next major session

### DF-019: `source_id` documentation and wiring
- **Source:** Section O discussion
- **Type:** Documentation + pipeline wiring
- **Agreed concept:** `source_id` is NOT `data_uuid`. It is:
  - For BULK_UPLOAD: `float_id` (HeartBeat-assigned at Float registration)
  - For ERPs: `connection_id` (e.g., "SAP-4HANA_HQ" with assigned ID)
  - Stable identifier for WHERE data came from, not WHICH batch
- **Documentation needed:** Source system model (what is a "source"? how are they registered? what IDs are assigned?)
- **Wiring needed:** Pipeline must carry `source_id` from HeartBeat registration through to invoice commit

### DF-020: Line item description priority (P0-P4) documentation and schema alignment
- **Source:** Section Q2 discussion
- **Type:** Documentation + possible schema update
- **Agreed concept:** Each line item has 5 descriptive fields with priority: P0 (description) > P1 (full_description) > P2 (customer_sku) > P3 (oem_sku) > P4 (helium_sku). Invoice popup renders 2 lines using highest two priorities found. May require updates to inventory and invoice_line_items schemas to ensure all 5 fields are present and prioritized.
- **Deliverable:** SWDB line item display spec + schema field verification

### DF-021: Aggregate worker debounce pattern (Helium Debounce)
- **Source:** M-Q2 aggregate discussion
- **Type:** Implementation pattern for aggregate_worker.py
- **Agreed concept:** Trailing-edge debounce with minimum interval (~4-5 seconds):
  1. Worker reacts to commit trigger
  2. If already running or < 4-5s since last run completed, queue trigger (keep LATEST only, don't stack)
  3. When current run finishes + minimum interval passes, run ONE more time with latest trigger
  4. Final run queries ALL committed data — always accurate (idempotent aggregate queries)
  5. Example: 5,000 invoices in 10s → only 2 aggregate worker runs, both correct
- **Implementation:** `asyncio.Event` + `asyncio.Lock` + timestamp tracking in aggregate_worker.py

### DF-022: DataCount box metrics (minimum aggregates for SWDB)
- **Source:** M-Q2 discussion
- **Type:** Minimum viable metrics for tenant_invoice_metrics
- **Agreed minimum:** The DataCount widgets need at a minimum:
  - eInvoices tab: `total_invoices_all_time` (total submitted to date)
  - Contacts tab: `total_customers` (from customers table count)
  - Inventory tab: `total_inventory_items` (from inventory table count)
  - Files/Queue: from SSE to HeartBeat (NOT Core — already wired)
- These are the baseline for the `tenant_invoice_metrics` table (DF-016)

### DF-023: Harmonize naming — `batch_id` vs `data_uuid` vs `file_id`
- **Source:** M-Q2 discussion
- **Type:** Documentation
- **Status:** Document, don't rename (rename cost too high for 20+ files)
- **Agreed fix:** Create a naming glossary that maps:
  - `batch_id` (Core) = `data_uuid` (Relay) = group identifier for a multi-file upload
  - `file_id` (Core/Relay) = individual file within a batch
  - `blob_uuid` (HeartBeat) = storage identifier for a single file
  - `queue_id` (Core) = staging entry for processing pipeline
  - `source_id` (HeartBeat) = registered source system (float_id / connection_id)
  - `original_filename` = `sourcefile_name` = human-readable source file name

### DF-006: Design — invoice_approvals table
- **Source:** Section A trace model discussion
- **Status:** Concept approved, design deferred
- **Recommended scope:** Dedicated design session when multi-step approval is needed
- **Agreed concept:** Separate table captures full approval journey (upload → review → approve → finalize). Each step gets its own `approval_trace_id` (UUIDv7, SDK-generated per gesture), approver identity (helium_user_id, email, name), machine context, and action_type enum. Invoice header fields (`helium_user_id`, `user_email`) remain as denormalized snapshot of the FINAL approver for quick display. Current single-step model (originator trace + finalizer identity on invoice) is sufficient until multi-step approval is needed.

### DF-024: Canonical schema SQL file updates (v2.1.3.0 bump)
- **Source:** Thorough scan — gap identified
- **Type:** Schema file updates for all agreed changes
- **Files to update:**
  1. `Documentation/Schema/invoice/06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` — add: `status_notes`, `fixed_invoice_blob_uuid`, `finalize_trace_id`, `firs_submitted_payload`, `payment_updated_at`, `payment_updated_by`, `customer_total_invoices_at_commit`, `customer_lifetime_value_at_commit`, `customer_compliance_score_at_commit`. Remove: `category`. Bump version to 2.1.3.0
  2. `Documentation/Schema/invoice/01_CANONICAL_FIELD_LIST_V1.md` — update field list to match SQL changes
  3. `invoice_line_items` TABLE 2 — add: `customer_sku`, `oem_sku`, `helium_sku`, `full_description`, `classification_confidence`, `classification_source`, `vat_rate` (7 fields for audit self-containment)
  4. Schema version INSERT for v2.1.3.0
- **Recommended scope:** Session 4 (WS5 Part 2C — Schema Bump)

### DF-025: HLX format alignment with schema v2.1.3.0
- **Source:** Thorough scan — gap identified
- **Type:** Format spec update
- **Question:** If the invoices schema adds new fields, does the `.hlm` data dict need to carry them? Does `.hlx` format need a version bump?
- **Files:** `helium_formats/hlx/`, `HLX_FORMAT.md`
- **Recommended scope:** Session 4, after schema updates are finalized

### DF-026: Edit validator editability enforcement
- **Source:** Architect instruction (not previously captured)
- **Type:** New logic in `edit_validator.py`
- **Agreed concept:** Edit validator must enforce which fields are editable vs immutable between preview and finalize. Currently it checks WHAT changed but doesn't enforce a whitelist of editable fields. Immutable fields (IRN, invoice_id, seller_tin, buyer_tin, direction, etc.) should be rejected if modified.
- **File:** `Core/src/finalize/edit_validator.py`
- **Recommended scope:** Session 2A (WS5-specific, ties into record_creator changes)

### DF-027: WS_ERROR_HANDLING full implementation
- **Source:** `WS_ERROR_HANDLING_KICKSTARTER.md` — 10 deliverables, 7 decision areas
- **Type:** Dedicated session (Session 3)
- **Deliverables not yet captured in DF entries:**
  1. Error handling audit: every `except` block in WS1-WS5
  2. Retry policy document (per phase, per service)
  3. Timeout configuration (overall + per-phase + per-service)
  4. Dead letter handling for permanently failed items
  5. Graceful shutdown (SIGTERM handling)
  6. Idempotency guards (prevent double-processing)
  7. User-facing error messages (what Float shows for each failure class)
  8. Resource limits (max file size, max invoices, memory guards)
  9. Integration tests for failure simulation
  10. Error handling documentation (DECISIONS update + runbook)
- **Partially overlaps:** DF-007 (scanner), DF-009 (status_notes)
- **Recommended scope:** Full Session 3

### DF-028: Fixed PDF stamper module build
- **Source:** `WS5_SUPPLEMENTARY_FIXED_PDF.md` — 7 deliverables
- **Type:** New shared module in `helium_formats/pdf_stamper/`
- **Deliverables:**
  1. `FixedPDFStamper` class (`helium_formats/pdf_stamper/stamper.py`)
  2. `StampPlacement` model (`models.py`)
  3. EIC placement resolver (`placement.py`)
  4. Default placement (`defaults.py`)
  5. Wire into WS5 pipeline (`Core/src/finalize/pipeline.py`)
  6. Tests
  7. Sample fixed PDF
- **Dependencies:** pypdf, reportlab, Pillow
- **Recommended scope:** Session 4 (WS5 Part 2C)

### DF-029: invoice_line_items full audit
- **Source:** Thorough scan — incomplete audit
- **Type:** Field-by-field audit of LINE_ITEM_INSERT against canonical TABLE 2
- **Known issues (from DF-013):**
  - Ghost columns in current INSERT: `unit_of_measure`, `discount_amount`, `vat_treatment`
  - Missing from INSERT: `line_item_type`, `tax_rate`, `product_code`, `product_name`, `classification_confidence`, `classification_source`
  - New fields for self-containment: `customer_sku`, `oem_sku`, `helium_sku`, `full_description`, `vat_rate`
- **Recommended scope:** Session 2A (alongside INVOICE_INSERT and INVENTORY_UPSERT fixes)

### DF-030: Per-entry aggregate error handling pattern
- **Source:** Architecture discussion (not previously captured as formal pattern)
- **Type:** Implementation pattern
- **Agreed pattern:**
  - Critical path (INVOICE INSERT, LINE_ITEM INSERT, CUSTOMER UPSERT, INVENTORY UPSERT) → single transaction, rolls back on ANY failure
  - Non-critical path (per-entry aggregates: customer counts, inventory counts, invoice snapshots) → same connection, NEW transaction per entity, individual try/except. Failure logged, does NOT block critical path
  - Safety net: debounced tenant-level aggregate worker corrects any per-entry failures within seconds (DF-021)
- **File:** `record_creator.py` structure

### DF-031: `service_id` on invoices table — DECISION NEEDED
- **Source:** Section K+L discussion — ambiguous status
- **Status:** Initially said "extractable from IRN, don't store." Architect then said "capture in section B." Unclear if this means add to schema or just document.
- **Current decision:** NOT stored on invoices (extractable from IRN string). Comes from tenant config. Document this in the naming glossary (DF-023).
- **If architect changes mind:** Add `service_id TEXT` to invoices table, set from `request_context.get("service_id")` which comes from the `service_id` param already passed to `pipeline.finalize()`.

### DF-032: Flash Card → metrics wiring (SSE → SDK → SWDB)
- **Source:** Flash Card analysis + DF-016 metrics table
- **Type:** End-to-end data flow
- **Components:**
  1. Core aggregate worker publishes SSE events with updated metrics
  2. SDK receives SSE events, updates local metrics cache
  3. SWDB Flash Card reads from SDK metrics cache (replacing mock data)
  4. DataCount widgets read from SDK metrics cache
- **Not built:** Any of these components. All Flash Card data is currently MOCK.
- **Recommended scope:** Dedicated workstream after metrics table is built

### DF-033: `finalize_trace_id` in SDK finalize request
- **Source:** Section A trace model discussion — approved but not captured in DF-002
- **Type:** Addition to DF-002 (SDK finalize request extension)
- **Agreed fix:** SDK generates a fresh UUIDv7 when user clicks Finalize (`finalize_trace_id`). This is the FINALIZER's gesture trace — distinct from `user_trace_id` (uploader's gesture trace). SDK sends it in the finalize request payload. Core writes it to `invoices.finalize_trace_id`.
- **Update needed in DF-002:** Add `finalize_trace_id` to the list of fields SDK sends.

### DF-034: `firs_submitted_payload` for INBOUND invoices
- **Source:** Audit self-containment discussion
- **Type:** Implementation detail for Edge service
- **Agreed concept:** For OUTBOUND: Edge saves the exact JSON payload sent to FIRS. For INBOUND: Edge saves the payload received from counterparty/FIRS. Both stored in `invoices.firs_submitted_payload`. Core's edge_update handler writes this on successful transmission (outbound) or receipt (inbound).
- **File:** Core edge_update handler + Edge service

### DF-035: sync.db migration tooling
- **Source:** DF-011 identifies the need but doesn't specify tooling
- **Type:** SDK tooling
- **Agreed concept:** When canonical schema changes (v2.1.3.0), SDK's `schema.py` INVOICES_TABLE must be updated AND a migration must run on existing `sync.db` files to add new columns. SDK needs a migration mechanism (ALTER TABLE IF NOT EXISTS pattern or versioned migration scripts).
- **File:** `Float/App/src/sdk/database/schema.py`, migration scripts
- **Recommended scope:** Session 4

---

## SESSION PLAN (Revised — 5 sessions)

### Session 2A: WS5 Record Creator Reconciliation
**Scope:** IF-001 through IF-069 (record_creator.py fixes), field_helpers.py, edit validator editability (DF-026), LINE_ITEM_INSERT audit (DF-029), per-entry aggregate pattern (DF-030), pipeline wiring, tests.
**Input:** This reconciliation document
**Output:** Working record_creator with full field coverage, passing tests

### Session 2B: Aggregate Worker + Metrics
**Scope:** Aggregate worker with Helium Debounce (DF-021), per-entry aggregates (customers + inventory), tenant_invoice_metrics table (DF-016), DataCount minimums (DF-022), SSE events for aggregate updates.
**Input:** Completed record_creator from Session 2A
**Output:** Async aggregate system, metrics table, SSE events

### Session 3: WS-ERROR — Cross-Pipeline Error Handling
**Scope:** Full WS_ERROR_HANDLING_KICKSTARTER walkthrough (DF-027), status_notes implementation (DF-009), WS-SCANNER (DF-017), committed invoice scanner (DF-007), retry policies, timeout hierarchy, graceful shutdown, idempotency.
**Input:** Error handling kickstarter doc + completed WS5 pipeline
**Output:** Comprehensive error handling across all Core phases

### Session 4: Schema Bump + Cross-Service Alignment
**Scope:** Canonical schema SQL updates v2.1.3.0 (DF-024), HLX alignment (DF-025), Fixed PDF stamper (DF-028), SDK finalize request extension (DF-002), Relay x_trace_id wiring (DF-001), sync.db harmonization (DF-011 + DF-035), PRO- prefix (DF-005), inter-service Pydantic schemas (DF-018).
**Input:** All schema changes agreed from Sessions 2A/2B/3
**Output:** Aligned canonical schemas, cross-service wiring, sync.db migration

### Session 5: Documentation + Metrics Wiring
**Scope:** SWDB field reference (DF-015), naming glossary (DF-023), source_id documentation (DF-019), P0-P4 line item display spec (DF-020), Flash Card metrics wiring (DF-032), tenant_invoice_metrics_history for sparklines.
**Input:** All implementation from Sessions 2A-4
**Output:** Complete documentation suite, Flash Card connected to real data
