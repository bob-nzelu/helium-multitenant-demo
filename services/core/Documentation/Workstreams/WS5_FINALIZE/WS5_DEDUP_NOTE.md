# WS5 Deduplication Note: Align with `helium_formats`

**Date:** 2026-03-25
**From:** WS5 Architecture Session
**To:** WS5 Part 2 Team
**Priority:** P1 — must be resolved before WS5 merges to main

---

## CONTEXT

WS5 was built before the team discovered that `helium_formats` (at `Helium/Services/helium_formats/`) already contains shared implementations for IRN generation, QR data, field editability, and the finalize edit payload model.

**`helium_formats` is the single source of truth.** WS5 must import from it, not maintain parallel implementations.

---

## OVERLAP TABLE

| WS5 File | helium_formats Equivalent | Action |
|---|---|---|
| `src/finalize/irn_generator.py` | `helium_formats.iqc.irn.generate_irn()` | **DELETE.** Import from `iqc.irn`. Add validation wrapper if needed. |
| `src/finalize/qr_generator.py` | `helium_formats.iqc.qr.generate_qr_data()` | **RENAME to `qr_stamper.py`.** Keep — ours generates PNG image for visual stamping. Theirs generates TLV data for FIRS. Different purposes, both needed. Wire ours to call theirs for the data, then render to PNG. |
| `src/finalize/edit_validator.py` | `helium_formats.hlm.editability.is_editable()` | **REFACTOR.** Replace `NEVER_EDITABLE_INVOICE_FIELDS`, `ALWAYS_EDITABLE_INVOICE_FIELDS`, and custom `is_field_editable()` with calls to `helium_formats.hlm.editability.is_editable("invoice", field)`. Keep the diff engine logic — just swap the field-set lookups. |
| `src/finalize/provenance.py` | (none — unique to WS5) | **KEEP.** Provenance constants and the `is_field_editable()` logic with confidence thresholds is WS5-specific. BUT: the field sets (`NEVER_EDITABLE`, `ALWAYS_EDITABLE`) should be removed and replaced with `helium_formats` lookups. |
| `src/finalize/models.py` → `FinalizeRequest` | `helium_formats.hlm.edits.HLMEditPayload` | **REPLACE.** Use `HLMEditPayload` as the finalize request body. It already defines `queue_id`, `data_uuid`, and `hlm_edits` (dict of sheet ID → `HLMSheetEdits`). Delete our custom `FinalizeRequest`. |

---

## SPECIFIC CHANGES

### 1. IRN — Delete and Import

**Before (WS5):**
```python
from src.finalize.irn_generator import generate_irn
irn = generate_irn(invoice_number, service_id, issue_date)
```

**After:**
```python
from helium_formats.iqc.irn import generate_irn, compute_irn_hash
irn = generate_irn(invoice_number, service_id, issue_date)
irn_hash = compute_irn_hash(irn)  # Bonus: dedup hash for free
```

Note: `helium_formats` version lacks our input validation (alphanumeric check, length check, future-date check). Either:
- Add validation to `helium_formats.iqc.irn` (preferred — benefits all consumers), OR
- Wrap with a thin validator in WS5 before calling

### 2. QR — Rename and Wire

**Before (WS5):**
```python
from src.finalize.qr_generator import generate_qr_code, QRInput
qr_base64 = generate_qr_code(QRInput(irn=irn, ...))
```

**After:**
```python
from helium_formats.iqc.qr import generate_qr_data  # TLV data
from src.finalize.qr_stamper import render_qr_png     # Visual PNG

qr_tlv = generate_qr_data(seller_name, seller_tin, ...)  # FIRS data
qr_png_base64 = render_qr_png(qr_tlv)                    # Visual for PDF stamping
```

The TLV data goes into `invoices.qr_code_data`. The PNG goes into the fixed PDF stamp.

### 3. Edit Validator — Use helium_formats Editability

**Before (WS5):**
```python
# provenance.py maintains its own field sets
NEVER_EDITABLE_INVOICE_FIELDS = frozenset({...})
ALWAYS_EDITABLE_INVOICE_FIELDS = frozenset({...})
```

**After:**
```python
from helium_formats.hlm.editability import is_editable

# In the diff engine:
if is_editable("invoice", field_name):
    # Accept change (subject to provenance/confidence checks)
else:
    # Violation
```

**Important:** `helium_formats` editability is a binary yes/no lookup. WS5's provenance-gated logic (ORIGINAL fields blocked, HIS/MISSING fields allowed, low-confidence override) is ADDITIONAL logic on top. The refactored flow:

```
1. is_editable("invoice", field) → False? → VIOLATION (never editable)
2. is_editable("invoice", field) → True? → Check provenance:
   a. provenance in {MISSING, HIS, MANUAL} → ACCEPTED
   b. provenance == ORIGINAL and confidence < 0.6 → ACCEPTED
   c. provenance == ORIGINAL and confidence >= 0.6 → VIOLATION
   d. provenance == TENANT → VIOLATION
```

### 4. Finalize Request Model — Use HLMEditPayload

**Before (WS5):**
```python
class FinalizeRequest(BaseModel):
    queue_id: str
    data_uuid: str
    ...
```

**After:**
```python
from helium_formats.hlm.edits import HLMEditPayload

# In router:
@router.post("/finalize")
async def finalize(payload: HLMEditPayload):
    ...
```

The `HLMEditPayload` already has:
- `queue_id: str`
- `data_uuid: str`
- `hlm_edits: dict[str, HLMSheetEdits]` — keyed by sheet ID ("submission", etc.)

Each `HLMSheetEdits` has:
- `hlm_version: str`
- `data_type: str` (invoice, customer, inventory)
- `rows: list[dict]` — each row has `_row_index` + changed field values

---

## HELIUM_FORMATS SOURCE FILES TO READ

| File | What It Contains |
|---|---|
| `helium_formats/iqc/irn.py` | `generate_irn()`, `compute_irn_hash()` |
| `helium_formats/iqc/qr.py` | `generate_qr_data()`, `encode_tlv()` — TLV format |
| `helium_formats/hlm/editability.py` | `is_editable(entity_type, field_name)` — single source of truth |
| `helium_formats/hlm/edits.py` | `HLMEditPayload`, `HLMSheetEdits` — finalize data contract |
| `helium_formats/hlm/invoice.py` | `INVOICE_EDITABLE_FIELDS`, `INVOICE_FULL_COLUMNS` |
| `helium_formats/hlm/customer.py` | `CUSTOMER_EDITABLE_FIELDS`, `CUSTOMER_FULL_COLUMNS` |
| `helium_formats/hlm/inventory.py` | `INVENTORY_EDITABLE_FIELDS`, `INVENTORY_FULL_COLUMNS` |
| `helium_formats/constants.py` | Version strings, schema versions |

---

## CRITICAL: EDITABILITY FIELD MISALIGNMENT

`helium_formats` editable field sets are **incomplete and partially wrong** relative to the agreed editability rules from the architecture session. Part 2 must fix these before relying on `is_editable()`.

### Invoice — `INVOICE_EDITABLE_FIELDS` needs updates:

**Currently has (correct):** `due_date`, `payment_terms_note`, `notes_to_firs`, `reference`, `category`

**Currently has (WRONG — must be provenance-gated, not always-editable):**
- `buyer_tin` — NOT editable if from source (ORIGINAL provenance). Only editable if MISSING/HIS.
- `buyer_name` — same rule as buyer_tin.

**MISSING (must add):**
- `transaction_type` — always editable (B2B↔B2G free swap; B2C→B2B/B2G requires counterparty details)
- `buyer_lga_code`, `buyer_postal_code`, `buyer_state_code`, `buyer_country_code` — conditionally editable (enriched)
- `firs_invoice_type_code` — conditionally editable (enriched)
- `terms` — always editable (user metadata)
- `buyer_address`, `buyer_city` — already present, correct as conditionally editable

**MISSING (line-item-level — need separate set or annotation):**
- `hsn_code`, `service_code`, `product_category`, `service_category`, `vat_treatment` — conditionally editable on `invoice_line_items` (enriched or low confidence)

### Customer — `CUSTOMER_EDITABLE_FIELDS` needs updates:

**WRONG — must be provenance-gated, not always-editable:**
- `tin` — NOT editable if from source
- `company_name` — NOT editable if from source
- `rc_number` — NOT editable if from source (code)
- `tax_id` — NOT editable if from source (code)

**The fix:** These fields should remain in the "potentially editable" set, but the editability check must be two-layer:
1. `is_editable("customer", "tin")` → True (it CAN be edited)
2. WS5 provenance check: provenance == ORIGINAL → BLOCKED

This means `helium_formats` editability is correct as "universe of possible edits" — but consumers MUST also check provenance. **Add a docstring to `editability.py` making this explicit.**

### Inventory — Looks correct, no changes needed.

### Summary for Part 2:

| Entity | Fields to Add | Fields to Annotate |
|---|---|---|
| Invoice | `transaction_type`, `buyer_lga_code`, `buyer_postal_code`, `buyer_state_code`, `buyer_country_code`, `firs_invoice_type_code`, `terms` | `buyer_tin`, `buyer_name` (provenance-gated) |
| Customer | (none to add) | `tin`, `company_name`, `rc_number`, `tax_id` (provenance-gated) |
| Inventory | (none) | (none) |
| Line Items | New set needed: `hsn_code`, `service_code`, `product_category`, `service_category`, `vat_treatment` | All provenance-gated |

---

## TEST IMPACT

After refactoring, existing WS5 tests should still pass — the behavior is the same, only the import paths change. However:
- Tests that directly import from `src.finalize.irn_generator` need updating
- Tests that mock `NEVER_EDITABLE_INVOICE_FIELDS` need to mock `is_editable()` instead
- Add integration tests that verify WS5 editability matches `helium_formats` definitions

---

**Last Updated:** 2026-03-25
