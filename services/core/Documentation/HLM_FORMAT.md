# .HLM FORMAT — Helium Lingua Franca

**Version:** 2.0
**Date:** 2026-03-19
**Status:** CANONICAL — Cross-cutting spec. All workstreams that produce or consume .hlm MUST follow this.
**Scope:** Core, Float SDK, Transforma, any future service that exchanges data within Helium

---

## 1. WHAT .HLM IS

`.hlm` is **Helium's canonical internal data interchange format** — the lingua franca of the platform.

It is NOT just an export format. It is the **target schema** that Helium intrinsically understands for invoices, customers, and inventory. Every piece of data flowing between services is either already in .hlm format or being transformed toward it.

### Key Principles

1. **Target schema**: .hlm defines the exact structure Helium understands. It maps 1:1 to the canonical database schemas (invoice v2.1.1.0, customer v1.2.0, inventory v1.0.0).

2. **Transformation cost = distance from .hlm**: The closer incoming data already is to .hlm format, the less work Core has to do. Data already in .hlm format requires **only validation**, not transformation.

3. **Bidirectional**: Core sends .hlm to Float (preview). Float sends .hlm back to Core (finalize with edits). Same format in both directions — no conversion needed.

4. **Transforma's job**: Transforma scripts exist solely to transform external data (Excel, CSV, PDF extracts) **into .hlm format**. Transforma output IS .hlm-shaped data.

5. **Core's job with .hlm data**: When data arrives already in .hlm format, Core's work reduces to: validate (73 checks) → enrich (HIS) → resolve (entities) → IRN/QR → DB insert. No structural transformation.

---

## 2. DATA FLOW

```
External Data (Excel, PDF, CSV, JSON, XML)
    │
    ↓ [Transforma: heavy transformation]
    │
.hlm-shaped data (3 data classes: invoice, customer, inventory)
    │
    ↓ [Core WS2: validate + enrich + resolve]
    │
Validated .hlm data
    │
    ↓ [Core WS3: serialize to .hlm files]
    │
invoices.hlm + customers.hlm + inventory.hlm  ──→  Float SDK (preview)
    │
    ↓ [User reviews, makes edits in Float]
    │
invoices.hlm (with edits)  ←──  Float SDK (finalize request)
    │
    ↓ [Core WS5: validate edits → IRN → QR → DB insert → Edge queue]
    │
PostgreSQL records + Edge submission
```

**When data arrives AS .hlm** (e.g., from a partner or API that speaks Helium format):
```
.hlm data arrives directly
    │
    ↓ [Core WS2: validate only — NO transformation needed]
    │
    ↓ [Core WS3/WS5: enrich → resolve → IRN → QR → DB]
```

---

## 3. FILE FORMAT

### 3A. Plain JSON (`.hlm`) — Under 1MB

A `.hlm` file is a JSON file with this structure:

```json
{
    "hlm_version": "2.0",
    "data_type": "invoice",
    "schema_version": "2.1.1.0",
    "generated_at": "2026-03-19T10:30:00Z",
    "generated_by": "core",
    "company_id": "tenant_001",

    "metadata": {
        "total_rows": 150,
        "total_columns": 115,
        "source": "process_preview",
        "data_uuid": "0193f5a0-...",
        "queue_id": "0193f5a1-...",
        "compressed": false
    },

    "columns": [
        {
            "name": "invoice_id",
            "type": "text",
            "nullable": false,
            "display_name": "Invoice ID",
            "display_order": 1,
            "visible": true,
            "width": 120,
            "editable": false,
            "category": "identity"
        }
    ],

    "rows": [
        {
            "invoice_id": "0193f5a2-...",
            "helium_invoice_no": "WM-TENANT-0001",
            "invoice_number": "INV-001",
            "issue_date": "2026-03-19",
            "total_amount": 53750.00
        }
    ]
}
```

### 3B. Compressed (`.hlmz`) — Over 1MB

When serialized JSON exceeds 1MB, the file is gzip-compressed:

- Extension: `.hlmz`
- Content: gzip-compressed `.hlm` JSON
- Detection: Check first 2 bytes for gzip magic number `\x1f\x8b`
- Decompression: Standard gzip → yields identical `.hlm` JSON

**Threshold rule**: Producer checks serialized JSON size. If > 1MB → compress to `.hlmz`. If ≤ 1MB → plain `.hlm`.

**Consumer rule**: Check file extension OR magic bytes. `.hlmz` or gzip magic → decompress first. Otherwise → parse as plain JSON.

---

## 4. COLUMN DEFINITION

Each column in the `columns` array describes a field:

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | string | yes | Field name matching canonical schema (e.g., `invoice_number`) |
| `type` | string | yes | One of: `text`, `integer`, `currency`, `date`, `datetime`, `boolean`, `enum`, `json` |
| `nullable` | boolean | yes | Whether NULL/empty is allowed |
| `display_name` | string | yes | Human-readable label for Float UI |
| `display_order` | integer | yes | Column position in default display (1-indexed) |
| `visible` | boolean | yes | Whether column is visible by default in Float SWDB |
| `width` | integer | no | Default column width in pixels |
| `editable` | boolean | no | Whether user can edit this field at finalize time (default: false). Actual editability is provenance-gated — see `HLX_FORMAT.md` Section 11. |
| `category` | string | no | Field category: `identity`, `operational`, `financial`, `compliance`, `audit`, `system` |
| `enum_values` | string[] | no | Valid values (only for `type: "enum"`) |
| `validation` | string | no | Regex or rule name for client-side validation |
| `provenance_default` | string | no | Default provenance for this field: `ORIGINAL`, `HIS`, `MISSING`, `DERIVED`, `TENANT`, `MANUAL`. Per-row provenance in `__provenance__` overrides this. See `HLX_FORMAT.md` Section 10. |

### Type Mapping

| HLM Type | PostgreSQL | Python | Float Display | Notes |
|----------|-----------|--------|---------------|-------|
| `text` | TEXT | str | String label | General text fields |
| `integer` | INTEGER/BIGINT | int | Number | Counts, codes |
| `currency` | NUMERIC(15,2) | Decimal/float | ₦ formatted | All monetary values |
| `date` | DATE | str (ISO) | Date picker | YYYY-MM-DD |
| `datetime` | TIMESTAMPTZ | str (ISO) | Timestamp | YYYY-MM-DDTHH:MM:SSZ |
| `boolean` | BOOLEAN | bool | Checkbox/icon | True/false flags |
| `enum` | TEXT + CHECK | str | Dropdown | Constrained values |
| `json` | JSONB | dict | Expandable | Complex nested data |

---

## 5. THE THREE .HLM SCHEMAS

### 5A. invoice.hlm

Maps to canonical invoice schema v2.1.1.0 (invoices table, 115 fields).

**Profiles:**
- **Display profile** (17 columns): For Float SWDB rendering. Maps to `vw_invoice_display`.
- **FIRS profile** (32 columns): FIRS-mandatory fields. Maps to `vw_invoice_firs`.
- **Full profile** (115 columns): Complete record. Maps to `invoices` table.

The `metadata.profile` field indicates which profile: `"display"`, `"firs"`, or `"full"`.

**Always-editable fields** (user metadata): `reference`, `category`, `notes_to_firs`, `payment_terms_note`, `terms`. Credit/debit note references (`reference_irn`, `reference_issue_date`).

**Provenance-gated editable fields** (only if provenance is `HIS`, `MISSING`, or `MANUAL`; or confidence < 0.60): Counterparty address (`buyer_lga_code`, `buyer_postal_code`, `buyer_state_code`, `buyer_country_code`, `buyer_address`, `buyer_city`), classification (`firs_invoice_type_code`), `due_date`. Line item classification: `hsn_code`, `service_code`, `product_category`, `service_category`, `vat_treatment`.

**NEVER editable:** `invoice_id`, `helium_invoice_no`, `irn`, `invoice_number`, all `seller_*` (provenance: TENANT), `buyer_tin`/`buyer_name`/`buyer_rc_number` (provenance: ORIGINAL — unless TIN was MISSING), all amounts (`subtotal`, `tax_amount`, `total_amount`, `wht_amount`, `discount_amount`), line item amounts/descriptions (`quantity`, `unit_price`, `line_total`, `description`), all dates except provenance-gated `due_date`, all timestamps, trace IDs, status fields, machine fingerprint. See `HLX_FORMAT.md` Section 11 for full rules.

### 5B. customer.hlm

Maps to canonical customer schema v1.2.0 (customers table, 54 fields).

**Profiles:**
- **Display profile** (17 columns): Maps to `vw_customer_display`.
- **FIRS profile** (18 columns): Maps to `vw_customer_firs`.
- **Full profile** (54 columns): Maps to `customers` table.

**Editable fields**: `company_name`, `trading_name`, `tin`, `rc_number`, `tax_id`, `email`, `phone`, `website`, `address`, `city`, `state`, `customer_type`, `tax_classification`, `industry`, `business_unit`, `default_due_date_days`.

**System-only fields**: `customer_id`, `customer_code`, `company_name_normalized`, `compliance_score`, all 15 aggregates, `created_at`, `updated_at`.

### 5C. inventory.hlm

Maps to canonical inventory schema v1.0.0 (inventory table, 35 fields).

**Profiles:**
- **Display profile** (13 columns): Maps to `vw_inventory_display`.
- **FIRS profile** (14 columns): Maps to `vw_inventory_firs`.
- **Full profile** (35 columns): Maps to `inventory` table.

**Editable fields**: `product_name`, `description`, `unit_of_measure`, `customer_sku`, `oem_sku`, `hsn_code`, `service_code`, `product_category`, `service_category`, `type`, `vat_treatment`, `vat_rate`, `is_tax_exempt`, `currency`.

**System-only fields**: `product_id`, `helium_sku`, `product_name_normalized`, all PDP intelligence fields, all aggregates.

---

## 6. FILE NAMING CONVENTION

```
{data_type}_{data_uuid}_{timestamp}.hlm      (plain)
{data_type}_{data_uuid}_{timestamp}.hlmz     (compressed)

Examples:
  invoices_0193f5a0_20260319T103000.hlm       (< 1MB)
  invoices_0193f5a0_20260319T103000.hlmz      (> 1MB, gzipped)
  customers_0193f5a0_20260319T103000.hlm
  inventory_0193f5a0_20260319T103000.hlm
```

---

## 7. WHEN .HLM IS PRODUCED AND CONSUMED

### Core → Float (Production)

| Trigger | Producer | Consumer | Data Types | Profile |
|---------|----------|----------|------------|---------|
| Preview complete | WS3 (Orchestrator) | Float SDK | invoice, customer, inventory | display |
| Finalization complete | WS5 (Finalize) | Float SDK (SSE event) | invoice, customer, inventory | display |
| Bulk export | WS4 (Entity CRUD) | Float SDK (download) | any single type | full |
| Scheduled report | WS6 (Observability) | Float SDK (notification) | invoice (filtered) | firs |
| Default DataFrame | WS0 (Foundation) | Float SDK (startup) | all 3 types | display |

### Float → Core (Finalize)

| Trigger | Producer | Consumer | Data Types | Notes |
|---------|----------|----------|------------|-------|
| Finalize with edits | Float SDK | WS5 (Finalize) | invoice, customer, inventory | Only edited rows + changed fields |

### Transforma → Core (Pipeline)

| Trigger | Producer | Consumer | Notes |
|---------|----------|----------|-------|
| Transformation complete | Transforma script | WS2/WS3 (Pipeline) | Output is .hlm-shaped data (in-memory, not necessarily a file) |

---

## 8. DEFAULT DATAFRAMES

Core serves empty .hlm files that define column structure for Float startup:

```
GET /api/v1/schema/invoice.hlm    → invoice .hlm with rows: [], display profile columns
GET /api/v1/schema/customer.hlm   → customer .hlm with rows: [], display profile columns
GET /api/v1/schema/inventory.hlm  → inventory .hlm with rows: [], display profile columns
```

These define what Float's SWDB renders before any data arrives. Column definitions (name, type, display_name, display_order, visible, width, editable) come from these defaults.

**WS0 implements these endpoints.** Column definitions must align with Float's `column_config.py`.

---

## 9. FINALIZE ROUND-TRIP

The round-trip flow ensures minimal work at finalize:

```
1. Core WS3 generates preview:     invoices.hlm (150 rows, display profile)
2. Float SDK renders in SWDB
3. User edits 3 rows in Float UI
4. Float SDK sends finalize request:
   POST /api/v1/finalize
   {
       "queue_id": "...",
       "data_uuid": "...",
       "hlm_edits": {
           "invoices": {
               "hlm_version": "2.0",
               "data_type": "invoice",
               "rows": [
                   {"_row_index": 0, "buyer_tin": "12345678-001"},
                   {"_row_index": 5, "due_date": "2026-04-19"},
                   {"_row_index": 12, "notes_to_firs": "Corrected amount"}
               ]
           },
           "customers": { ... },
           "inventory": { ... }
       }
   }
5. Core WS5 receives edits in .hlm format
6. Core applies edits to preview data (already .hlm-shaped)
7. Core validates (73 checks) → IRN → QR → DB insert → Edge queue
8. No transformation needed — data is already in target format
```

**The `_row_index` field** maps the edit back to the original preview row. Only changed fields are sent — unchanged fields are not included.

---

## 10. WORKSTREAM RESPONSIBILITIES

| Workstream | .hlm Responsibility |
|-----------|-------------------|
| **WS0: FOUNDATION** | Defines .hlm Pydantic models (shared library). Serves default DataFrames via `/schema/{type}.hlm`. Implements serialization/deserialization utilities. |
| **WS1: INGESTION** | No .hlm involvement (raw file bytes only) |
| **WS2: PROCESSING** | Validates .hlm-shaped data against 73 checks. Enriches via HIS. Resolves entities. |
| **WS3: ORCHESTRATOR** | Serializes ProcessingResult → `.hlm` files for preview. Stores in HeartBeat blob. |
| **WS4: ENTITY CRUD** | Bulk export generates `.hlm`/`.hlmz` files. Statistics endpoint returns .hlm-shaped aggregates. |
| **WS5: FINALIZE** | Receives `.hlm` edits from Float. Applies edits to preview data. Generates final `.hlm` after record creation. |
| **WS6: OBSERVABILITY** | Scheduled reports may output `.hlm` files. |
| **WS-PREREQ: TRANSFORMA** | Output MUST be .hlm-shaped data (TransformedInvoice, TransformedCustomer, TransformedProduct). |

---

## 11. VERSIONING

| Field | Current | Rule |
|-------|---------|------|
| `hlm_version` | `"2.0"` | Incremented on structural changes to the .hlm wrapper format |
| `schema_version` | Varies per data type | Matches canonical schema version (e.g., `"2.1.1.0"` for invoices) |

**Backward compatibility:** Consumers must handle `hlm_version` they don't recognize by checking `schema_version` and falling back to known column definitions.

---

## 12. UPDATE PROTOCOL

When any workstream modifies .hlm behavior:
1. Update THIS document
2. If column definitions change: Update canonical schema SQL first (Schema Governance)
3. If serialization changes: Update WS0's Pydantic models
4. If profiles change: Update relevant `vw_*` views in canonical SQL

---

**Last Updated:** 2026-03-19
**Version:** 2.0 — CANONICAL
