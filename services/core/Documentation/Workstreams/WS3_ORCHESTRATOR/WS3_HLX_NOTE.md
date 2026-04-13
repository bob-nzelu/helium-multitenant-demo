# WS3 ORCHESTRATOR — HLX/HLM Integration Note

**Date:** 2026-03-24
**From:** Architecture Session (Bob + Opus)
**References:** `HLX_FORMAT.md` v1.1 (Sections 2, 3, 10, 12), `HLM_FORMAT.md` v2.0
**Priority:** Must be implemented for .hlx v1.1 compliance

---

## WHAT YOU NEED TO KNOW

WS3 is the **primary .hlx producer** — it packages WS2's output into .hlx files for Float. Three new requirements have been added to HLX_FORMAT v1.1:

1. **Two new sheets:** `customers.hlm` and `inventory.hlm` with `__IS_NEW__` flags
2. **Per-field provenance:** Carry WS2's `field_provenance` into .hlm `__provenance__` objects
3. **Shared data model awareness:** Entity data must be consistent across sheets

---

## CHANGE 1: New Entity Sheets

### customers.hlm

Add a `customers.hlm` sheet to every .hlx. This sheet lists all customers detected in the upload batch.

**Content:** One row per resolved customer from `ResolveResult.customers`

**Required columns:**
| Column | Source | Description |
|--------|--------|-------------|
| `customer_id` | ResolvedCustomer.customer_id | UUIDv7 (or provisional ID for new) |
| `company_name` | ResolvedCustomer.company_name | Company name |
| `tin` | ResolvedCustomer.tin | Tax ID (may be null) |
| `rc_number` | ResolvedCustomer.rc_number | RC number (may be null) |
| `customer_type` | Inferred | B2B / B2G / B2C |
| `city` | ResolvedCustomer address fields | City |
| `state` | ResolvedCustomer address fields | State |
| `match_type` | ResolvedCustomer.match_type | NEW / EXACT / FUZZY |
| `match_confidence` | ResolvedCustomer.match_confidence | 0.0-1.0 |
| `invoice_count` | Computed | How many invoices in this batch reference this customer |
| `total_amount` | Computed | Sum of total_amount for invoices referencing this customer |
| `__IS_NEW__` | `match_type == "NEW"` | Boolean flag for visual highlighting |

**Manifest entry:**
```json
{
    "id": "customers",
    "filename": "sheets/customers.hlm",
    "display_name": "Extracted Customers",
    "category": "entity",
    "interaction_tier": "informational",
    "icon": "people",
    "sort_order": 8,
    "description": "Customers detected in this upload (NEW flag for first-time customers)"
}
```

### inventory.hlm

Add an `inventory.hlm` sheet to every .hlx. Lists all products/services detected.

**Content:** One row per resolved product from `ResolveResult.inventory`

**Required columns:**
| Column | Source | Description |
|--------|--------|-------------|
| `product_id` | ResolvedProduct.product_id | UUIDv7 (or provisional ID for new) |
| `product_name` | ResolvedProduct.product_name | Product/service name |
| `hsn_code` | ResolvedProduct.hs_code | HS code (may be null) |
| `service_code` | ResolvedProduct.service_code | Service code (may be null) |
| `item_type` | ResolvedProduct.item_type | GOODS / SERVICE |
| `vat_treatment` | Inferred | STANDARD / ZERO_RATED / EXEMPT |
| `match_type` | ResolvedProduct.match_type | NEW / EXACT / FUZZY |
| `match_confidence` | ResolvedProduct.match_confidence | 0.0-1.0 |
| `invoice_count` | Computed | How many line items reference this product |
| `__IS_NEW__` | `match_type == "NEW"` | Boolean flag for visual highlighting |

**Manifest entry:**
```json
{
    "id": "inventory",
    "filename": "sheets/inventory.hlm",
    "display_name": "Extracted Products",
    "category": "entity",
    "interaction_tier": "informational",
    "icon": "inventory_2",
    "sort_order": 9,
    "description": "Products/services detected in this upload (NEW flag for first-time items)"
}
```

### __IS_NEW__ Flag

Both sheets include a `__IS_NEW__` boolean column:
- `true` if the entity was NOT found in the existing database (`match_type == "NEW"`)
- `false` if the entity matched an existing record (`match_type == "EXACT"` or `"FUZZY"`)

Float uses this flag to visually highlight new entities (e.g., badge, background color, "NEW" chip).

**Both sheets are `interaction_tier: informational`** — view-only in ReviewPage. Users cannot edit customer/inventory data from these sheets. Edits go through the Customer List and Inventory tabs in Float (WS4).

---

## CHANGE 2: Per-Field Provenance in .hlm Sheets

WS2 now outputs `field_provenance` on each resolved invoice (see `WS2_HLX_NOTE.md`). WS3 must carry this into the .hlm files.

### Implementation

When serializing invoice rows into .hlm `rows` array, include the `__provenance__` object:

```python
def serialize_invoice_row(invoice: ResolvedInvoice) -> dict:
    row = {
        "invoice_number": invoice.invoice_number,
        "hsn_code": invoice.line_items[0].hsn_code,  # simplified
        "buyer_lga_code": invoice.buyer_lga_code,
        # ... all fields ...
    }

    # Add provenance — only fields that differ from ORIGINAL
    provenance = {}
    for field_name, source in invoice.field_provenance.items():
        if source != "ORIGINAL":
            provenance[field_name] = source

    if provenance:  # Only include if there are non-ORIGINAL fields
        row["__provenance__"] = provenance

    return row
```

### Column Definition Update

For columns that are commonly enriched, set `provenance_default` in the column definition:

```python
# In the columns array of the .hlm
{
    "name": "hsn_code",
    "type": "text",
    "editable": True,  # Will be dynamically checked against provenance
    "provenance_default": "HIS"
},
{
    "name": "invoice_number",
    "type": "text",
    "editable": False,
    "provenance_default": "ORIGINAL"
}
```

---

## CHANGE 3: Shared Data Awareness

When the same counterparty appears in multiple sheets (e.g., `submission.hlm` and `failed.hlm`), the counterparty data must be identical across sheets. WS3 already packages from a single `ResolveResult`, so this is naturally consistent at generation time.

**The cross-sheet consistency requirement is primarily a Float SDK concern** (when users edit shared data at preview time). WS3's job is to ensure the initial .hlx has consistent data, which it does by construction.

If WS3 generates a **new .hlx version** (after re-finalize), it must re-package ALL sheets — not just the changed ones — to maintain consistency.

---

## FILES TO MODIFY

| File | Change |
|------|--------|
| `src/orchestrator/preview_generator.py` | Add `_build_customers_sheet()` and `_build_inventory_sheet()` methods |
| `src/orchestrator/preview_generator.py` | Serialize `__provenance__` objects from `ResolvedInvoice.field_provenance` |
| `src/orchestrator/preview_generator.py` | Add `provenance_default` to column definitions for enrichable fields |
| `src/orchestrator/pipeline.py` | Include new sheets in manifest generation |
| `helium_formats/hlx/models.py` | Add `"entity"` to valid `SheetEntry.category` values |
| `helium_formats/hlx/packer.py` | Pack new sheets into .hlx archive |

---

## REFERENCE DOCUMENTS

- **`HLX_FORMAT.md`** v1.1, Section 2 (Physical Format) — archive structure with new sheets
- **`HLX_FORMAT.md`** v1.1, Section 3 (Manifest) — customers/inventory manifest entries
- **`HLX_FORMAT.md`** v1.1, Section 10 (Per-Field Provenance) — `__provenance__` spec
- **`HLX_FORMAT.md`** v1.1, Section 11 (Editability Rules) — what provenance means for editing
- **`HLX_FORMAT.md`** v1.1, Section 12 (Shared Data Model) — cross-sheet consistency
- **`HLM_FORMAT.md`** v2.0, Section 4 (Column Definition) — `provenance_default` property
