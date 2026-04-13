# TRANSFORMA — Provenance Ownership Note

**Date:** 2026-03-24
**From:** Architecture Session (Bob + Opus)
**To:** Transforma Implementation Team
**References:** `HLX_FORMAT.md` v1.1 (Sections 10-11), `HLM_FORMAT.md` v2.0, `WS2_HLX_NOTE.md`
**Priority:** Required for HLX v1.1 provenance compliance

---

## YOUR NEW RESPONSIBILITY

Transforma is the **owner of field provenance AND enrichment**. You are the only component that sees the raw source document, and you are the component that enriches missing/incomplete fields (via tenant-specific scripts and HIS calls within the Transforma pipeline).

There is **one pipeline, not two passes**. Transforma does extraction + enrichment in a single run. By the time Transforma outputs data, all fields should be populated where possible — either from source extraction or from enrichment. Downstream workstreams (WS2, WS3) do NOT re-enrich data. WS2 only stamps mechanical provenance labels (`TENANT`, `DERIVED`) on system fields — it does not call HIS or fill in missing values.

Every field in Transforma's output must carry a provenance tag. This tag travels through the pipeline (WS2 provenance stamping → WS3 packaging → .hlx → Float ReviewPage) and determines which fields the user can edit at preview time.

**The rule is simple: source data is sacred. Only enriched, missing, or low-confidence fields are editable.**

---

## WHAT PROVENANCE IS

A per-field tag that declares where the value came from:

| Value | Meaning | Set By | Editable? |
|-------|---------|--------|-----------|
| `ORIGINAL` | Value extracted from the source document | **Transforma** | No |
| `MISSING` | Field was not present in source (null/empty) | **Transforma** | Yes |
| `HIS` | Value enriched by HIS after Transforma | WS2 (mechanical) | Yes |
| `DERIVED` | Value computed by Core (normalized names, match scores) | WS2 (mechanical) | No |
| `TENANT` | Value from tenant config.db (seller/buyer party) | WS2 (mechanical) | No |
| `MANUAL` | Value set by user in a previous edit | WS5 (at re-finalize) | Yes |

**Transforma sets only two values: `ORIGINAL` and `MISSING`.** Downstream components (WS2) mechanically update `MISSING` → `HIS` when enrichment fills a gap, and stamp `TENANT`/`DERIVED` on known system fields. Transforma does NOT need to know about HIS, TENANT, or DERIVED.

**Transforma owns extraction AND enrichment.** Fields like `vat_treatment`, `customer_type`, and classification codes are Transforma's responsibility — both extracting them from the source document and enriching them when the source is incomplete. When the source specifies VAT treatment, set the value and tag provenance as `ORIGINAL`. When Transforma infers or enriches the value (e.g., via HIS calls within the Transforma pipeline), set the value and tag provenance as `HIS`. Do NOT leave these for downstream defaulting — if Transforma can determine a value, it should. WS3 and WS5 expect these fields to be populated by the time Transforma is done.

**Transforma owns the `field_provenance` attribute** on all output dataclasses (`TransformedInvoice`, `ExtractedCustomer`, `ExtractedProduct`). No other workstream creates or stubs this attribute — they read and update it.

---

## HOW TO IMPLEMENT

### Option A: Framework-Level (Recommended)

The Transforma **framework** (not individual scripts) handles provenance automatically. Script authors do not manually tag provenance.

```python
class TransformaFramework:
    def execute(self, script, raw_data, config) -> TransformResult:
        # 1. Build empty template with all possible fields set to None
        template = self._build_empty_invoice_template()

        # 2. Run the Transforma script — it populates fields from source
        result = script.transform(raw_data, config)

        # 3. Framework diffs result vs template to determine provenance
        provenance = {}
        for field_name in ALL_INVOICE_FIELDS:
            value = getattr(result, field_name, None)
            if value is not None and value != "":
                provenance[field_name] = "ORIGINAL"
            else:
                provenance[field_name] = "MISSING"

        result.field_provenance = provenance
        return result
```

**Advantages:**
- Script authors never touch provenance — zero chance of forgetting
- Provenance is always complete and consistent
- New fields automatically get provenance tracking

### Option B: Script-Level (Not Recommended)

Each Transforma script manually tags provenance:

```python
def transform(raw_data, config):
    invoice = TransformedInvoice()
    invoice.invoice_number = raw_data["Invoice No"]
    invoice.field_provenance["invoice_number"] = "ORIGINAL"

    # HS code not in source
    invoice.hsn_code = None
    invoice.field_provenance["hsn_code"] = "MISSING"
```

**Disadvantages:** Every script author must remember. One script forgets → broken editability for that tenant. Violates DRY.

### Our Recommendation: Option A

Framework-level provenance is safer, simpler, and requires zero changes to existing scripts.

---

## OUTPUT FORMAT

Transforma's output (`TransformResult`) gains a `field_provenance` dict on each invoice, customer, and product:

```python
@dataclass
class TransformedInvoice:
    # ... existing fields ...
    field_provenance: dict[str, str] = field(default_factory=dict)
    # e.g., {"invoice_number": "ORIGINAL", "hsn_code": "MISSING", "buyer_lga_code": "MISSING"}
```

Same for `ExtractedCustomer` and `ExtractedProduct`:

```python
@dataclass
class ExtractedCustomer:
    # ... existing fields ...
    field_provenance: dict[str, str] = field(default_factory=dict)

@dataclass
class ExtractedProduct:
    # ... existing fields ...
    field_provenance: dict[str, str] = field(default_factory=dict)
```

---

## WHAT HAPPENS DOWNSTREAM

After Transforma outputs data with provenance, the data flows through:

```
Transforma output (ORIGINAL / MISSING / HIS on every field)
    │   Transforma does ALL extraction + enrichment in one pipeline.
    │   Fields are populated where possible. Provenance is set.
    │
    ↓ WS2 (mechanical provenance stamping ONLY — no enrichment)
    │   Known tenant party fields → TENANT
    │   Computed fields (normalized names, match scores) → DERIVED
    │   WS2 does NOT call HIS. WS2 does NOT fill missing values.
    │   WS2 does NOT re-enrich anything Transforma already handled.
    │
    ↓ WS3 Orchestrator (passthrough)
    │   Serializes field_provenance into .hlm __provenance__ objects
    │   Packages into .hlx
    │
    ↓ Float SDK (consumer)
    │   Reads __provenance__ to determine which fields user can edit
    │
    ↓ WS5 Finalize (validator)
        Diffs edited .hlm against preview .hlx
        Rejects changes to ORIGINAL/TENANT/DERIVED fields
```

Transforma's provenance is the foundation. Everything downstream depends on it being correct and complete.

---

## EDGE CASES

### Field has value in source but is clearly wrong

Example: Source has `hsn_code = "ABC"` (invalid format). Transforma should still tag as `ORIGINAL`. WS2's validation will flag it as a red flag. The field will have provenance `ORIGINAL` but will be marked with a validation error in the .hlx report — Float can show the user the error and suggest they re-upload.

### Field is partially extracted

Example: Source has `buyer_address = "123 Lagos"` but no postal code. Tag `buyer_address` as `ORIGINAL`, tag `buyer_postal_code` as `MISSING`.

### Multi-value fields

Example: Line items. Each line item is a separate entity with its own provenance. If the source has 5 line items, all line item fields on those 5 rows are `ORIGINAL`. If HIS enriches `hsn_code` on line item 3, only line item 3's `hsn_code` changes to `HIS`.

### Re-processed data

If the same file is re-uploaded and re-processed, Transforma runs fresh. All provenance resets to `ORIGINAL`/`MISSING`. Previous user edits (provenance: `MANUAL`) do not carry forward — the user must re-edit on the new preview. This is correct behavior.

---

## FILES TO MODIFY

| File | Change |
|------|--------|
| Transforma framework entry point | Add post-transform provenance diff (Option A) |
| `TransformedInvoice` dataclass | Add `field_provenance: dict[str, str]` |
| `ExtractedCustomer` dataclass | Add `field_provenance: dict[str, str]` |
| `ExtractedProduct` dataclass | Add `field_provenance: dict[str, str]` |
| Transforma tests | Verify provenance is complete for all output fields |

---

## REFERENCE DOCUMENTS

- **`HLX_FORMAT.md`** v1.1, Section 10 — Per-field provenance metadata spec
- **`HLX_FORMAT.md`** v1.1, Section 11 — Editability rules (how provenance drives editing)
- **`WS2_HLX_NOTE.md`** — WS2's mechanical provenance updates (downstream of you)
- **`HLM_FORMAT.md`** v2.0, Section 4 — `provenance_default` column property
