# .HLX FORMAT — Helium Exchange Document

**Version:** 1.1
**Date:** 2026-03-24
**Status:** CANONICAL — Supersedes HLE_FORMAT.md
**Scope:** Core Service (producer), Float SDK (consumer/renderer), HeartBeat Blob Store (versioned storage)

---

> **NAMING**: `.hle` (Helium Envelope) is **retired**. The format is now `.hlx` (Helium Exchange).
> The rename reflects the expanded role: .hlx is not just a delivery envelope — it is a
> **shareable, versioned, interactive document** tied to a tenancy.

---

## 1. WHAT .HLX IS

`.hlx` (Helium Exchange) is a **tar.gz archive** that bundles everything Float needs to present, review, and act on a bulk upload's processing results.

### .hlm vs .hlx — The Distinction

| | .hlm | .hlx |
|---|------|------|
| **What** | Individual data frame (invoices, customers, inventory) | Tar/gzip archive bundling multiple .hlm sheets + report + metadata |
| **Metaphor** | A single spreadsheet tab | The workbook file containing tabs + summary |
| **Direction** | Core ↔ Float (at finalize, Float sends edited .hlm back) | Core → Float (delivery). Float → Float (sharing within tenancy). |
| **Sharing** | Not shareable independently | **Shareable within same tenancy.** Cross-tenancy = access denied. |
| **Contains** | One data type, one profile | Multiple .hlm sheets + report.json + manifest.json + metadata.json |
| **When produced** | Throughout pipeline | At end of preview generation (WS3, Phase 7: BRANCH) |
| **Stored where** | In-memory during pipeline | HeartBeat blob store — **versioned** |
| **Interactions** | None (raw data) | Open in ReviewPage, email, finalize, notify, export |

### Key Principles

1. **Core speaks .hlm internally.** The pipeline operates on .hlm-shaped data. Core never opens or reads .hlx files.
2. **Core packages .hlm → .hlx for delivery.** At the end of preview generation, WS3 bundles all .hlm outputs + report + metadata into a single .hlx archive.
3. **Float opens .hlx.** Float SDK downloads one .hlx file, unpacks it, and renders the ReviewPage from its contents. Users can also receive .hlx files from colleagues within the same tenancy.
4. **Finalize sends .hlm back, NOT .hlx.** When the user finalizes, Float sends back only the edited data as `.hlm` — Core receives .hlm, validates, and finalizes. No round-trip of the entire .hlx.
5. **One .hlx per bulk upload.** Each `data_uuid` gets exactly one .hlx. HeartBeat stores **versions** as the .hlx is updated (e.g., failed invoices edited and re-finalized).
6. **Tenancy-bound.** Every .hlx carries a `company_id`. Float checks this against the current session's tenancy. Mismatch = access denied error.
7. **Bundle integrity.** All .hlm sheets within a .hlx MUST share the same `data_uuid` (i.e., originate from the same upload). You cannot bundle submission.hlm from upload A with failed.hlm from upload B.

---

## 2. PHYSICAL FORMAT

`.hlx` is a **tar.gz archive** (gzip-compressed tar) containing:

```
{data_uuid}.hlx
├── manifest.json          — Declares which sheets are present, their interactions, metadata
├── report.json            — Processing statistics, red flags, compliance summary
├── metadata.json          — Processing metadata (timestamps, versions, trace IDs, tenancy)
└── sheets/
    ├── submission.hlm     — Invoices ready for FIRS submission
    ├── duplicate.hlm      — Duplicate invoices detected
    ├── late.hlm           — Late invoices (past due date threshold)
    ├── foc.hlm            — Free-of-charge invoices
    ├── unusual.hlm        — Unusual amount/pattern invoices
    ├── possible_b2b.hlm   — Possible B2B candidates (downgraded to B2C)
    ├── failed.hlm         — All failed invoices (unified, with __STREAM__ column)
    ├── customers.hlm      — Extracted/resolved customers (view-only, with __IS_NEW__ flag)
    └── inventory.hlm      — Extracted/resolved products (view-only, with __IS_NEW__ flag)
```

**MIME type:** `application/x-helium-exchange` (custom)
**Extension:** `.hlx`
**Compression:** gzip (level 6, balance of speed and size)
**Icon:** Custom document icon (grid + bullets on document page — see `assets/hlx_icon.png`)

---

## 3. MANIFEST (manifest.json)

The manifest is the **table of contents**. Float reads the manifest first to know what sheets exist, how to render them, and what interactions each supports.

```json
{
    "hlx_version": "1.0",
    "data_uuid": "0193f5a0-...",
    "queue_id": "0193f5a1-...",
    "company_id": "tenant_001",
    "generated_at": "2026-03-19T10:30:00Z",
    "generated_by": "core",
    "schema_version": "2.1.1.0",

    "bundle_integrity": {
        "source_data_uuid": "0193f5a0-...",
        "source_type": "bulk_upload",
        "all_sheets_same_source": true
    },

    "sheets": [
        {
            "id": "submission",
            "filename": "sheets/submission.hlm",
            "display_name": "Invoices for Submission",
            "category": "output",
            "interaction_tier": "primary",
            "row_count": 145,
            "column_count": 27,
            "icon": "check_circle",
            "sort_order": 1,
            "description": "Valid invoices ready for FIRS submission"
        },
        {
            "id": "duplicate",
            "filename": "sheets/duplicate.hlm",
            "display_name": "Duplicate Invoices",
            "category": "output",
            "interaction_tier": "informational",
            "row_count": 3,
            "column_count": 11,
            "icon": "content_copy",
            "sort_order": 2,
            "description": "Invoices with matching IRN or invoice number"
        },
        {
            "id": "failed",
            "filename": "sheets/failed.hlm",
            "display_name": "Failed Invoices",
            "category": "failed",
            "interaction_tier": "actionable",
            "row_count": 5,
            "column_count": 9,
            "icon": "error",
            "sort_order": 7,
            "description": "Invoices that failed validation (all streams)"
        },
        {
            "id": "customers",
            "filename": "sheets/customers.hlm",
            "display_name": "Extracted Customers",
            "category": "entity",
            "interaction_tier": "informational",
            "row_count": 17,
            "column_count": 12,
            "icon": "people",
            "sort_order": 8,
            "description": "Customers detected in this upload (NEW flag for first-time customers)"
        },
        {
            "id": "inventory",
            "filename": "sheets/inventory.hlm",
            "display_name": "Extracted Products",
            "category": "entity",
            "interaction_tier": "informational",
            "row_count": 42,
            "column_count": 10,
            "icon": "inventory_2",
            "sort_order": 9,
            "description": "Products/services detected in this upload (NEW flag for first-time items)"
        }
    ],

    "report": {
        "filename": "report.json",
        "has_summary_cards": true,
        "has_red_flags": true,
        "has_compliance_score": true
    },

    "metadata": {
        "filename": "metadata.json"
    },

    "statistics": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "duplicate_count": 3,
        "processing_time_ms": 45200,
        "overall_confidence": 0.92
    }
}
```

---

## 4. SHEET INTERACTION TIERS

Different .hlm sheets within a .hlx support different levels of user interaction. This is declared per-sheet in the manifest via `interaction_tier`.

### Tier Definitions

| Tier | Sheets | Available Actions | Description |
|------|--------|-------------------|-------------|
| **`primary`** | `submission` | Finalize & Submit, Email, Notify Superior, Export as Excel, Share | The main deliverable. Full action palette. |
| **`actionable`** | `failed`, `possible_b2b` | Edit Rows, Re-finalize, Export as Excel, Share | User can fix errors and resubmit. Editing creates a new .hlx version. |
| **`informational`** | `duplicate`, `late`, `foc`, `unusual` | Export as Excel, Share as Email Attachment | View-only classification sheets. No editing or finalizing. |

### Interaction Matrix

| Action | `primary` | `actionable` | `informational` |
|--------|:---------:|:------------:|:----------------:|
| View in ReviewPage | ✅ | ✅ | ✅ |
| Export as Excel | ✅ | ✅ | ✅ |
| Share as Email Attachment | ✅ | ✅ | ✅ |
| Edit Rows | ✅ | ✅ | ❌ |
| Finalize & Submit to FIRS | ✅ | ❌ (re-finalize only) | ❌ |
| Notify Superior | ✅ | ❌ | ❌ |
| Email (in-app) | ✅ | ❌ | ❌ |

### Re-Finalize Flow (actionable tier)

When a user edits failed invoices and re-finalizes:

1. User opens .hlx → ReviewPage → navigates to `failed` sheet
2. User edits rows (fixes validation errors)
3. User clicks "Re-finalize" on the failed sheet
4. Float sends edited rows as `.hlm` to Core (`POST /api/v1/finalize` with `is_refinalze: true`)
5. Core re-validates, moves passing invoices to submission pipeline
6. Core generates a **new .hlx version** with updated sheets
7. HeartBeat stores the new version (old version preserved for audit)
8. Float receives updated .hlx, re-renders ReviewPage

### B2B Upgrade Flow (possible_b2b — actionable tier)

When a user adds B2B details to a possible_b2b invoice and re-finalizes:

1. User opens .hlx → ReviewPage → navigates to `possible_b2b` sheet
2. User adds TIN, email, and postal address to a B2C invoice
3. User clicks "Re-finalize" on the possible_b2b sheet
4. Float sends edited rows as `.hlm` to Core (`POST /api/v1/finalize` with `is_refinalze: true`)
5. Core upgrades invoice from B2C → B2B (includes `accounting_customer_party` in FIRS submission)
6. Core moves the invoice to the submission pipeline (increases submission count)
7. Core creates a new customer record in `customers.db` from the newly-complete B2B data
8. Core generates a **new .hlx version** with updated sheets
9. Float receives updated .hlx, re-renders ReviewPage

---

## 5. TENANCY BINDING & SHARING

### Tenancy Check

Every .hlx carries `company_id` in both manifest.json and metadata.json. When Float opens a .hlx file:

```
1. Read manifest.json → extract company_id
2. Compare against current session's company_id (from JWT)
3. If mismatch → "Access Denied: This document belongs to a different organization"
4. If match → proceed to render ReviewPage
```

### Sharing Within Tenancy

Users can share .hlx files with colleagues in the same tenancy:

- **In-app:** "Share" action → select recipient from team list → recipient gets notification
- **Email:** "Share as Email Attachment" → .hlx file attached to email. Recipient opens in their Float instance.
- **Direct file:** User can copy the .hlx file. Any Float instance authenticated to the same tenancy can open it.

Cross-tenancy sharing is **blocked**. The tenancy check is enforced at open time, not at share time.

### What Happens When Opened

Regardless of how the .hlx was received (generated by Core, shared by colleague, or opened from file):

1. Float validates bundle integrity (`all_sheets_same_source`)
2. Float validates tenancy (`company_id` match)
3. Float opens **ReviewPage** with the .hlx contents
4. ReviewPage renders sheet tabs from manifest, in `sort_order` sequence
5. Each tab shows the corresponding .hlm sheet data
6. Action buttons appear based on `interaction_tier` of the active tab

---

## 6. VERSIONING (HeartBeat)

HeartBeat maintains a version history for each .hlx document. Every modification (re-finalize of failed invoices, post-finalize corrections) creates a new version.

### blob.hlx_versions Table

```sql
CREATE TABLE blob.hlx_versions (
    version_id      TEXT PRIMARY KEY,           -- UUIDv7
    hlx_id          TEXT NOT NULL,              -- Stable document ID (same across versions)
    version_number  INTEGER NOT NULL,           -- 1, 2, 3...
    data_uuid       TEXT NOT NULL,              -- Source upload data_uuid
    blob_uuid       TEXT NOT NULL,              -- FK to blob.blob_files (the actual .hlx file)
    company_id      TEXT NOT NULL,              -- Tenant
    created_by      TEXT NOT NULL,              -- helium_user_id who triggered the version
    created_at      TIMESTAMPTZ DEFAULT now(),
    change_reason   TEXT,                       -- 'initial', 'failed_invoices_edited', 're_finalized', 'correction'
    change_summary  TEXT,                       -- Human-readable: "5 failed invoices corrected and resubmitted"
    status          TEXT DEFAULT 'active',      -- 'active' | 'superseded' | 'archived'

    CONSTRAINT uq_hlx_version UNIQUE(hlx_id, version_number)
);

CREATE INDEX idx_hlx_versions_hlx ON blob.hlx_versions(hlx_id, version_number DESC);
CREATE INDEX idx_hlx_versions_company ON blob.hlx_versions(company_id);
CREATE INDEX idx_hlx_versions_data ON blob.hlx_versions(data_uuid);
```

### Version Lifecycle

```
Upload processed → Core generates .hlx v1 → HeartBeat stores (version_number=1, status='active')
    │
    ▼ (user edits failed invoices and re-finalizes)
    │
Core generates .hlx v2 → HeartBeat stores (version_number=2, status='active')
                          HeartBeat updates v1 → status='superseded'
    │
    ▼ (user makes another correction)
    │
Core generates .hlx v3 → HeartBeat stores (version_number=3, status='active')
                          HeartBeat updates v2 → status='superseded'
```

- Only **one active version** per hlx_id at any time
- Superseded versions are retained for audit (not deleted)
- `archived` status = manual archive by admin (retention policy)

### Metadata Carries Version Info

metadata.json inside each .hlx includes:

```json
{
    "hlx_id": "0193f5a0-...",
    "version_number": 2,
    "previous_version_id": "0193f5b0-...",
    "change_reason": "failed_invoices_edited",
    "change_summary": "5 failed invoices corrected: 3 missing TIN, 2 invalid amounts"
}
```

---

## 7. BUNDLE INTEGRITY

All .hlm sheets within a .hlx MUST originate from the same source upload (`data_uuid`). This is enforced at two points:

### At Generation (Core WS3)
Core WS3 only bundles sheets from the same pipeline run. Since each pipeline run processes one `data_uuid`, integrity is guaranteed by construction.

### At Open (Float SDK)
Float validates on open:

```python
def validate_bundle_integrity(manifest: dict, sheets: list[HLMFile]) -> None:
    source_uuid = manifest["bundle_integrity"]["source_data_uuid"]
    for sheet in sheets:
        if sheet.metadata.data_uuid != source_uuid:
            raise HLXIntegrityError(
                f"Sheet {sheet.metadata.data_type} has data_uuid "
                f"{sheet.metadata.data_uuid}, expected {source_uuid}. "
                f"All sheets in an .hlx must originate from the same upload."
            )
```

**Error message to user:** "Cannot open file: This document contains sheets from different uploads, which is not allowed."

---

## 8. REPORT (report.json)

Processing statistics and red flags. Drives the ReviewPage's summary cards. See `REPORT_ENGINE.md` for the full ProcessingReport model.

```json
{
    "summary": {
        "total_invoices": 150,
        "valid_count": 145,
        "failed_count": 5,
        "duplicate_count": 3,
        "overall_confidence": 0.92,
        "processing_time_ms": 45200
    },

    "red_flags": [
        {
            "id": "V-INV-045",
            "type": "missing_hsn_code",
            "severity": "error",
            "message": "HS code could not be determined",
            "evidence": "Row 3: hsn_code is NULL, product_name='Widget X'",
            "impact": "Invoice cannot be submitted to FIRS without HS code",
            "suggested_fix": "Manually enter HS code or update HIS enrichment data",
            "sheet": "submission",
            "row_index": 2
        }
    ],

    "compliance_score": {
        "overall": 94.5,
        "grade": "A",
        "components": {
            "field_completeness": 98.0,
            "amount_verification": 100.0,
            "entity_resolution": 85.0,
            "firs_formatting": 95.0
        }
    },

    "phase_timings": {
        "fetch_ms": 200,
        "parse_ms": 5000,
        "transform_ms": 3000,
        "enrich_ms": 30000,
        "resolve_ms": 5000,
        "branch_ms": 2000
    }
}
```

Report tiering (CFO, Owner, Audit, Metrics views) is **Core's job**, not the format's. The .hlx carries the full report. Core decides what to include based on user designation.

---

## 9. METADATA (metadata.json)

Processing metadata for audit, tenancy validation, and versioning.

```json
{
    "data_uuid": "0193f5a0-...",
    "queue_id": "0193f5a1-...",
    "company_id": "tenant_001",
    "uploaded_by": "helium_user_123",
    "x_trace_id": "0193f5a2-...",
    "user_trace_id": "0193f5a3-...",

    "hlx_id": "0193f5c0-...",
    "version_number": 1,
    "previous_version_id": null,
    "change_reason": "initial",
    "change_summary": null,

    "source_files": [
        {
            "blob_uuid": "0193f5a4-...",
            "original_filename": "january_sales.xlsx",
            "file_size_bytes": 524288,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "parser_type": "excel",
            "was_hlm": false
        }
    ],

    "pipeline": {
        "phases_executed": ["fetch", "parse", "transform", "enrich", "resolve", "branch"],
        "phases_skipped": [],
        "transform_script": "pikwik_till_report_v2.1",
        "enrichment_source": "his",
        "hlm_detected": false
    },

    "versions": {
        "core_version": "1.0.0",
        "hlx_version": "1.0",
        "hlm_version": "2.0",
        "schema_version": "2.1.1.0"
    },

    "generated_at": "2026-03-19T10:30:00Z",
    "expires_at": "2026-03-26T10:30:00Z"
}
```

---

## 10. PER-FIELD PROVENANCE METADATA

Every .hlm sheet within an .hlx carries per-field provenance information that tracks WHERE each field's value came from. This is critical for editability enforcement — only fields that were NOT sourced from the original document may be edited at preview time.

### 10.1 Provenance Values

| Value | Meaning | Editable? |
|-------|---------|-----------|
| `ORIGINAL` | Value extracted directly from the source document | **No** — source data is sacred |
| `HIS` | Value enriched by HIS (Helium Intelligence Service) | **Yes** — enrichment may be wrong |
| `MISSING` | Field was not present in source and not enriched | **Yes** — user can supply |
| `MANUAL` | Value set manually by user in a previous edit | **Yes** — user's own data |
| `DERIVED` | Value computed by Core (e.g., totals, normalized names) | **No** — system-computed |
| `TENANT` | Value from tenant config.db (seller details on outbound, buyer on inbound) | **No** — edit via config.db only |

### 10.2 Provenance in .hlm Column Definition

Each column in the `columns` array gains a new optional property:

```json
{
    "name": "hsn_code",
    "type": "text",
    "nullable": true,
    "display_name": "HS Code",
    "editable": true,
    "category": "compliance",
    "provenance_default": "HIS"
}
```

`provenance_default` declares the typical source for this field. However, provenance can vary **per row** (e.g., one invoice's HS code came from source, another was enriched). Per-row provenance is stored in a parallel `__provenance__` object on each row.

### 10.3 Per-Row Provenance

Each row in the `rows` array MAY include a `__provenance__` object mapping field names to their provenance value for that specific row:

```json
{
    "invoice_number": "INV-001",
    "hsn_code": "1905.90",
    "buyer_lga_code": "LGA-042",
    "notes_to_firs": "",
    "__provenance__": {
        "invoice_number": "ORIGINAL",
        "hsn_code": "HIS",
        "buyer_lga_code": "HIS",
        "notes_to_firs": "MISSING"
    }
}
```

**Rules:**
- If `__provenance__` is absent on a row, all fields default to `ORIGINAL`
- Only fields that differ from `ORIGINAL` need to be listed in `__provenance__`
- Provenance is generated by WS2 (Processing) during enrichment and carried through WS3 into the .hlx

### 10.4 Low-Confidence Override

Fields with enrichment confidence below a threshold are also editable, even if provenance is `ORIGINAL`. This is tracked via the existing `classification_confidence` field on line items. When `classification_confidence < 0.60`, the field is treated as editable regardless of provenance.

---

## 11. EDITABILITY RULES

Source data is sacred. The following rules govern which fields a user may edit on the ReviewPage at preview time.

### 11.1 Universal Rule

A field is editable at preview time if ANY of these conditions is true:
1. Its provenance is `HIS`, `MISSING`, or `MANUAL`
2. Its provenance is `ORIGINAL` but `classification_confidence < 0.60`
3. It is in the **always-editable** list (user metadata fields)

A field is NEVER editable if:
1. Its provenance is `TENANT` (edit via config.db only)
2. Its provenance is `DERIVED` (system-computed)
3. It is a financial amount (`subtotal`, `tax_amount`, `total_amount`, `wht_amount`, `discount_amount`, `line_total`, `unit_price`, `quantity`)
4. It is a line item description or product name (strict audit — changes require source re-upload)
5. It is an identity field (`invoice_id`, `invoice_number`, `irn`, `customer_id`, `product_id`)

### 11.2 Invoice Editable Fields

**Always editable (user metadata):**
- `reference`, `category`, `notes_to_firs`, `payment_terms_note`, `terms`

**Always editable (references child table):**
- Credit note references (`reference_irn`, `reference_issue_date`) — can be multiple
- Debit note references (`reference_irn`, `reference_issue_date`) — can be multiple

**Always editable (classification — with rules):**
- `transaction_type` — editable via dropdown on ReviewPage, with constraints:
  - **B2B ↔ B2G**: freely swappable (both have full customer details)
  - **B2C → B2B or B2G**: requires user to first fill in all counterparty details (TIN, name, address, etc.). B2C customers do NOT have customer details — the upgrade is blocked until details are complete.
  - **B2B/B2G → B2C**: allowed (strips customer detail requirement)

**Conditionally editable (provenance-gated):**
- Counterparty address: `buyer_lga_code`, `buyer_postal_code`, `buyer_state_code`, `buyer_country_code`, `buyer_address`, `buyer_city`
- Classification: `firs_invoice_type_code`

**Line item classification (provenance-gated, on invoice_line_items):**
- `hsn_code`, `service_code`, `product_category`, `service_category`, `vat_treatment`

**NEVER editable on invoices:**
- All `seller_*` fields (provenance: TENANT)
- `buyer_tin`, `buyer_name`, `buyer_rc_number` (provenance: ORIGINAL — unless TIN was MISSING)
- `invoice_number`, `irn`, `csid` (identity)
- All amounts: `subtotal`, `tax_amount`, `total_amount`, `wht_amount`, `discount_amount`
- Line item amounts: `quantity`, `unit_price`, `line_total`, `tax_amount`
- Line item descriptions: `description`
- All dates: `issue_date`, `issue_time` (source document dates)
- `due_date` — editable only if provenance is `MISSING` or `HIS`
- All status fields, audit fields, trace IDs

### 11.3 Customer and Inventory Pages

The `customers.hlm` and `inventory.hlm` sheets in the .hlx are **view-only** (`interaction_tier: informational`). Users cannot edit customer or inventory data from the ReviewPage.

- Customer/inventory edits happen through the Customer List and Inventory tabs in Float (WS4 `PUT /entity/{type}/{id}`)
- This prevents conflicting edits between the submission flow and entity management

### 11.4 Tenant Party Rule

The tenant's own details are NEVER editable through the invoice preview flow:
- **Outbound invoices:** All `seller_*` fields come from config.db (provenance: `TENANT`)
- **Inbound invoices:** All `buyer_*` fields come from config.db (provenance: `TENANT`)

To change tenant details, the user updates config.db directly. Future invoices will use the updated values.

---

## 12. SHARED DATA MODEL (Cross-Sheet Consistency)

Within an .hlx, the same counterparty or product may appear across multiple sheets (e.g., a customer exists in both `submission.hlm` and `failed.hlm`). Edits to shared entities must be consistent.

### 12.1 Principle

When a user edits a counterparty field (e.g., `buyer_lga_code`) on an invoice in the submission sheet, the same counterparty's data in the failed sheet must reflect the change immediately. The .hlx is a **single logical document**, not isolated sheets.

### 12.2 Implementation

The Float SDK maintains an in-memory entity index when an .hlx is loaded:

```
Counterparty Index:
  buyer_tin="12345678-001" → appears in rows [submission:2, submission:15, failed:0]

Product Index:
  hsn_code="1905.90" + description="Biscuits" → appears in rows [submission:2, submission:5]
```

When a user edits a counterparty field on one row, the SDK propagates the edit to all rows sharing the same counterparty identity.

### 12.3 Failed Invoice Auto-Upgrade

When a user corrects errors on a failed invoice:
- If the .hlx has **NOT been finalized yet**: the corrected invoice is validated in real-time. If it now passes all checks, it is visually upgraded to the submissions sheet. This requires real-time veracity APIs (future implementation).
- If the .hlx **HAS been finalized**: failed invoices cannot be upgraded in-place. They must be re-sent as a new bulk upload, which produces a new .hlx.

### 12.4 Version Impact

Editing shared data and re-finalizing creates a **new .hlx version** (see Section 6). The new version reflects all cross-sheet consistency changes.

---

## 13. LIFECYCLE

### Generation (Core → Blob → SDK → ReviewPage)

```
1.  Core WS3 completes pipeline (Phases 1-7)
2.  WS3 PreviewGenerator serializes results:
    a. Each output/failed category → .hlm file
    b. Statistics + red flags → report.json
    c. Processing metadata + tenancy + version → metadata.json
    d. Sheet inventory + interaction tiers → manifest.json
3.  WS3 packages all files into tar.gz → {data_uuid}.hlx
4.  WS3 stores .hlx in HeartBeat blob (POST /api/blob/write)
5.  HeartBeat creates hlx_versions entry (version_number=1, status='active')
6.  Core returns blob_uuid in process_preview response
7.  Float SDK downloads .hlx from HeartBeat (GET /api/blob/{blob_uuid})
8.  SDK caches .hlx locally
9.  SDK validates tenancy (company_id match)
10. SDK validates bundle integrity (all sheets same data_uuid)
11. SDK opens ReviewPage
12. ReviewPage renders tabs from manifest sheet list
13. Each tab renders from the corresponding .hlm file
14. Action buttons appear based on interaction_tier of active tab
```

### Finalize (ReviewPage → Float SDK → Core)

```
1. User reviews sheets in ReviewPage
2. User makes edits (optional) to submission.hlm rows
3. User clicks "Finalize & Submit"
4. Float SDK extracts ONLY the edited rows as .hlm:
   POST /api/v1/finalize
   {
       "queue_id": "...",
       "data_uuid": "...",
       "hlx_id": "...",
       "hlm_edits": {
           "submission": {
               "hlm_version": "2.0",
               "data_type": "invoice",
               "rows": [
                   {"_row_index": 2, "hsn_code": "1905.90"},
                   {"_row_index": 15, "buyer_tin": "12345678-001"}
               ]
           }
       }
   }
5. Core receives .hlm (NOT .hlx) — applies edits, validates, finalizes
```

### Re-Finalize Failed Invoices

```
1. User opens .hlx → ReviewPage → navigates to "Failed Invoices" tab
2. User edits rows in failed.hlm (fixes errors)
3. User clicks "Re-finalize"
4. Float sends edited failed rows as .hlm to Core:
   POST /api/v1/finalize
   {
       "queue_id": "...",
       "data_uuid": "...",
       "hlx_id": "...",
       "is_refinalize": true,
       "hlm_edits": {
           "failed": {
               "hlm_version": "2.0",
               "data_type": "invoice",
               "rows": [
                   {"_row_index": 0, "buyer_tin": "98765432-001"},
                   {"_row_index": 3, "total_amount": 15000.00}
               ]
           }
       }
   }
5. Core re-validates corrected rows
6. Passing rows move to submission pipeline
7. Core generates new .hlx (version_number + 1)
8. HeartBeat stores new version, marks old as 'superseded'
9. Float receives updated .hlx, re-renders ReviewPage
```

### Sharing Within Tenancy

```
1. User A opens .hlx in ReviewPage
2. User A clicks "Share" on submission sheet
3. Float presents team member list (same company_id)
4. User A selects User B
5. SDK sends notification (via HeartBeat notifications)
6. User B receives notification with hlx_id + blob_uuid
7. User B clicks notification → SDK downloads .hlx from HeartBeat
8. SDK validates company_id match → opens ReviewPage
```

### Opening from File

```
1. User double-clicks {data_uuid}.hlx file (OS file association)
2. Float launches (or focuses if running)
3. Float reads manifest.json → extracts company_id
4. If company_id != session company_id → "Access Denied" error
5. Float validates bundle integrity
6. Float opens ReviewPage with .hlx contents
```

---

## 14. FILE NAMING

```
{data_uuid}.hlx

Example:
  0193f5a0-7e8f-7abc-9012-3456789abcde.hlx
```

Inside the archive, files are at fixed paths:
```
manifest.json
report.json
metadata.json
sheets/{sheet_id}.hlm
```

---

## 15. WORKSTREAM RESPONSIBILITIES

| Workstream | .hlx Responsibility |
|-----------|-------------------|
| **WS-HLX** (helium_formats) | Pydantic models (Manifest, SheetEntry, interaction tiers). Pack/unpack utilities. Validation (tenancy, integrity). Rename from .hle → .hlx throughout. |
| **WS0: FOUNDATION** | No .hlx involvement (infrastructure only) |
| **WS1: INGESTION** | **Route finalized .hlm differently.** When a `.hlm` arrives with a `finalized` flag, skip Transforma and route directly to WS5 for validation and committal. See `WS1_HLX_NOTE.md`. |
| **WS2: PROCESSING** | **Generate per-field provenance metadata.** Each enrichment step (HIS, address validation, classification) must tag fields with their source (`ORIGINAL`, `HIS`, `MISSING`, `MANUAL`, `DERIVED`, `TENANT`). Provenance flows into the .hlx via WS3. See `WS2_HLX_NOTE.md`. |
| **WS3: ORCHESTRATOR** | **Primary producer.** PreviewGenerator assembles .hlm files + report + metadata into .hlx. **Must include `customers.hlm` and `inventory.hlm` pages** with `__IS_NEW__` flags. Must carry per-field provenance from WS2 into .hlm `__provenance__` objects. See `WS3_HLX_NOTE.md`. |
| **WS4: ENTITY CRUD** | No .hlx involvement (customer/inventory edits happen through entity CRUD, not ReviewPage) |
| **WS5: FINALIZE** | Receives .hlm edits (NOT .hlx) from Float. **Validates edits against provenance metadata** — only `HIS`, `MISSING`, `MANUAL`, or low-confidence fields may differ from preview. Handles re-finalize flow. Triggers new .hlx version generation. |
| **WS6: OBSERVABILITY** | No .hlx involvement |
| **HeartBeat** | Stores .hlx files in blob. Maintains `blob.hlx_versions` table. Serves .hlx to SDK. |
| **Float SDK** | Downloads, caches, validates (tenancy + integrity), opens .hlx. Renders ReviewPage. **Maintains cross-sheet entity index for shared data consistency** (Section 12). Handles sharing. |

---

## 16. NOMENCLATURE ALIGNMENT

| Old Name | New Name | Reason |
|----------|----------|--------|
| `.hle` (Helium Envelope) | **`.hlx` (Helium Exchange)** | Reflects expanded role: shareable, versioned, interactive document |
| `HLE_FORMAT.md` | **`HLX_FORMAT.md`** | This file |
| `HLEManifest` | **`HLXManifest`** | Code rename |
| `pack_hle()` / `unpack_hle()` | **`pack_hlx()` / `unpack_hlx()`** | Code rename |
| `application/x-helium-envelope` | **`application/x-helium-exchange`** | MIME type |
| ResultPage | **ReviewPage** | "Result" conflates with "Report". Review is what the user does. |
| PreviewPage | **ReviewPage** | Consistent naming |
| report.json (ambiguous) | report.json (inside .hlx) | Unambiguous — inside the .hlx, not a standalone report |
| Reports (analytics) | Reports (in REPORT_ENGINE.md) | Separate concept entirely |

---

## 17. RELATIONSHIP DIAGRAM

```
┌──────────────────────────────────────────────────────┐
│                  .HLX EXCHANGE DOCUMENT                │
│                    (tar.gz archive)                     │
│                                                        │
│  manifest.json ─── sheets + interaction tiers          │
│  report.json ──── statistics, red flags, compliance    │
│  metadata.json ── tenancy, versions, trace IDs         │
│                                                        │
│  sheets/                                               │
│    submission.hlm ──┐  interaction_tier: primary        │
│    duplicate.hlm ───┤  interaction_tier: informational  │
│    late.hlm ────────┤  interaction_tier: informational  │
│    foc.hlm ─────────┤  interaction_tier: informational  │
│    unusual.hlm ─────┤  interaction_tier: informational  │
│    possible_b2b.hlm ┤  interaction_tier: actionable     │
│    failed.hlm ──────┤  interaction_tier: actionable     │
│    customers.hlm ───┤  interaction_tier: informational  │
│    inventory.hlm ───┘  interaction_tier: informational  │
│                                                        │
│  company_id: tenant_001  ← tenancy-bound               │
│  data_uuid: 0193f5a0... ← bundle integrity             │
│  version: 2             ← versioned in HeartBeat       │
└──────────────────────────────────────────────────────┘
             │                         ▲
             ▼                         │
    Float SDK unpacks           Core WS3 packages
    validates tenancy           at end of Phase 7
    renders ReviewPage
             │
             ├── User clicks Finalize → sends .hlm edits → Core
             ├── User clicks Share → same-tenancy colleague receives
             └── User clicks Export → Excel download
```

---

**Last Updated:** 2026-03-24
**Version:** 1.1 — CANONICAL (supersedes v1.0)

**v1.1 Changes (2026-03-24):**
- Added `customers.hlm` and `inventory.hlm` sheets with `__IS_NEW__` flags (Sections 2, 3)
- Added per-field provenance metadata system (Section 10)
- Added editability rules — source data is sacred (Section 11)
- Added shared data model for cross-sheet consistency (Section 12)
- Updated WS1/WS2/WS3 responsibilities with new requirements (Section 15)
- IRN format corrected to FIRS spec: `{INVOICE_NUMBER}-{SERVICE_ID}-{YYYYMMDD}`
