# WS2 PROCESSING — HLX/HLM Integration Note

**Date:** 2026-03-24 (updated)
**From:** Architecture Session (Bob + Opus)
**References:** `HLX_FORMAT.md` v1.1 (Sections 10-11), `HLM_FORMAT.md` v2.0, `WS_TRANSFORMA_PROVENANCE_NOTE.md`
**Priority:** Must be implemented before WS3 can produce provenance-aware .hlx files

---

## YOUR ROLE: MECHANICAL PROVENANCE STAMPER (NOT ENRICHER)

**Transforma owns BOTH provenance AND enrichment.** Transforma does extraction + enrichment in a single pipeline — it reads the source document, extracts fields, and enriches missing/incomplete fields (via tenant-specific scripts and HIS calls). By the time Transforma outputs data, fields like `vat_treatment`, `hsn_code`, and `customer_type` are already populated where possible.

**WS2 does NOT enrich data. WS2 does NOT call HIS. WS2 does NOT fill in missing values.** All enrichment is Transforma's job.

**WS2's only provenance job is stamping two mechanical labels** on fields that Transforma cannot know about:

1. **Tenant party fields → `TENANT`** (requires knowing which party is the tenant, based on invoice direction + config.db)
2. **Computed fields → `DERIVED`** (normalized names, match scores — produced by WS2's resolver)

That's it. Two mechanical sweeps. No analytical decisions, no enrichment.

---

## WHAT TO IMPLEMENT

### 1. Tenant Config Stamp

After loading tenant details from config.db, stamp provenance on tenant party fields:

```python
def stamp_tenant_provenance(invoice, direction):
    """Mechanical: mark tenant party fields as TENANT."""
    if direction == "OUTBOUND":
        # Tenant is seller
        for field in SELLER_FIELDS:
            invoice.field_provenance[field] = "TENANT"
    elif direction == "INBOUND":
        # Tenant is buyer
        for field in BUYER_FIELDS:
            invoice.field_provenance[field] = "TENANT"

SELLER_FIELDS = [
    "seller_tin", "seller_name", "seller_address", "seller_city",
    "seller_email", "seller_phone", "seller_postal_code",
    "seller_lga_code", "seller_state_code", "seller_country_code",
    "seller_rc_number", "seller_tax_id", "seller_business_id",
]
BUYER_FIELDS = [
    "buyer_tin", "buyer_name", "buyer_address", "buyer_city",
    "buyer_email", "buyer_phone", "buyer_postal_code",
    "buyer_lga_code", "buyer_state_code", "buyer_country_code",
    "buyer_rc_number", "buyer_tax_id", "buyer_business_id",
]
```

### 2. Post-Resolver Stamp (Phase 5: RESOLVE)

After entity resolution, stamp computed fields:

```python
def stamp_derived_provenance(invoice):
    """Mechanical: mark system-computed fields as DERIVED."""
    DERIVED_FIELDS = [
        "company_name_normalized", "product_name_normalized",
        "customer_match_type", "customer_match_confidence",
        "product_match_type", "product_match_confidence",
        "overall_confidence",
    ]
    for field in DERIVED_FIELDS:
        if field in invoice.field_provenance:
            invoice.field_provenance[field] = "DERIVED"
```

### 3. Confidence Tracking

For HIS-enriched fields, track confidence alongside provenance. This is already partially done via `classification_confidence` on line items. Extend to other enriched fields:

```python
@dataclass
class EnrichmentDetail:
    provenance: str  # "HIS", "ORIGINAL", etc.
    confidence: float | None  # 0.0-1.0, only for HIS-enriched
    his_source: str | None  # "hsn_mapping", "address_validation", etc.
```

**Editability rule:** Fields with `confidence < 0.60` are editable even if provenance is `ORIGINAL`. This handles cases where the source data exists but is probably wrong.

---

## FILES TO MODIFY

| File | Change |
|------|--------|
| `src/processing/enricher.py` | Add `stamp_tenant_provenance()` — mark tenant party fields as TENANT based on direction + config.db |
| `src/processing/resolver.py` | Add `stamp_derived_provenance()` — mark computed fields (normalized names, match scores) as DERIVED |

**Note:** `field_provenance: dict[str, str]` on all dataclasses is created and populated by Transforma (see `WS_TRANSFORMA_PROVENANCE_NOTE.md`). WS2 only stamps `TENANT` and `DERIVED` — it does NOT create `field_provenance`, does NOT call HIS, does NOT fill missing values.

---

## REFERENCE DOCUMENTS

- **`WS_TRANSFORMA_PROVENANCE_NOTE.md`** — Transforma owns provenance initialization (ORIGINAL/MISSING)
- **`HLX_FORMAT.md`** v1.1, Section 10 (Per-Field Provenance Metadata) — full provenance spec
- **`HLX_FORMAT.md`** v1.1, Section 11 (Editability Rules) — how provenance drives editability
- **`HLM_FORMAT.md`** v2.0, Section 4 (Column Definition) — `provenance_default` column property
