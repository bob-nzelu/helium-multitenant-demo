# WS3 ORCHESTRATOR — HLX Integration Response

**Date:** 2026-03-24
**From:** WS3 Implementation Team (Opus session)
**To:** Bob (Architect)
**Re:** WS3_HLX_NOTE.md — Three changes for HLX v1.1 compliance

---

## STATUS: Two items ready to implement, one blocked on clarification

We've read the note. Changes 1 and 3 are clear. Change 2 has a dependency. Below is our implementation plan and two questions that need answers before we proceed.

---

## CHANGE 1: Entity Sheets — READY

**Plan:** Add `_build_customers_sheet()` and `_build_inventory_sheet()` to `preview_generator.py`. Both consume existing `ResolveResult.customers` and `ResolveResult.inventory` lists. Both get `__IS_NEW__` column derived from `match_type == "NEW"`.

**Computed columns:**
- `invoice_count` / `total_amount` on customers.hlm — cross-reference `ResolveResult.invoices` by `customer_id`
- `invoice_count` on inventory.hlm — cross-reference line items by `product_id`

**Manifest entries** will use `category: "entity"` and `interaction_tier: "informational"` exactly as specified. Sort orders 8 and 9 after the 7 existing invoice sheets.

### Question A: `customer_type` inference

The note says `customer_type` (B2B / B2G / B2C) is "Inferred". `ResolvedCustomer` has no `customer_type` field. `ResolvedInvoice` has `transaction_type` (B2B / B2C).

**Our proposed inference:**
- Group invoices by `customer_id`
- If ANY invoice referencing this customer has `transaction_type == "B2B"` → customer is `B2B`
- If ANY has `transaction_type == "B2G"` → `B2G`
- Else → `B2C`
- Priority: B2G > B2B > B2C (a customer appearing in both B2B and B2G invoices is B2G)

**Alternative:** Default to `"B2B"` for all and defer real inference to Transforma/HIS. Simpler but less useful for Float.

**Which approach do you want?**

### Question B: `vat_treatment` inference

The note says `vat_treatment` (STANDARD / ZERO_RATED / EXEMPT) is "Inferred". `ResolvedProduct` has `item_type` (GOODS / SERVICE) and `hs_code` but no `vat_treatment`.

**Our proposed approach for v1:**
- Default ALL products to `STANDARD`
- Log a TODO: proper VAT treatment inference requires HS code lookup table (which codes are zero-rated under Nigerian VAT Act) and service exemption rules — this is HIS territory
- When HIS enrichment populates `vat_treatment` on `ResolvedProduct` (future), WS3 just maps it through

**Reason:** Getting VAT treatment right is a compliance concern. Better to default to STANDARD (overpays VAT, safe) than to guess wrong and under-report. The real logic belongs in HIS/Transforma where tax rules live.

**Do you agree with defaulting to STANDARD for v1?**

---

## CHANGE 2: Per-Field Provenance — BLOCKED ON WS2

**Dependency:** `ResolvedInvoice.field_provenance` does not exist yet. It's a `dict[str, str]` mapping field names to sources (ORIGINAL / PARSED / TRANSFORMED / ENRICHED / RESOLVED / MANUAL).

**Bob confirmed** this comes from Transforma (WS2 pipeline). Until that attribute exists on the dataclass, we cannot serialize `__provenance__` objects.

**Our plan:**
1. **Now:** Add `field_provenance: dict[str, str] = field(default_factory=dict)` to `ResolvedInvoice` as a stub. This is safe — empty dict means no provenance written, existing code unaffected.
2. **Now:** Write the serialization logic in `preview_generator.py` that checks `invoice.field_provenance` and emits `__provenance__` when non-empty (exactly as shown in the note's code example).
3. **Now:** Add `provenance_default` to column definitions for known enrichable fields (`hsn_code`, `buyer_lga_code`, `buyer_state_code`, `seller_lga_code`, `seller_state_code`, `category`, `subcategory`).
4. **Later (Transforma):** Transforma populates `field_provenance` during transform/enrich phases. WS3 code picks it up automatically — no further changes needed.

**This approach means WS3 is v1.1 compliant on day one.** The `__provenance__` section will be empty until Transforma populates it, but the .hlx structure is correct. Float SDK can code against the spec immediately.

**Do you approve this stub approach?**

---

## CHANGE 3: Shared Data Awareness — NO ACTION NEEDED

Confirmed: WS3 packages from a single `ResolveResult`, so cross-sheet consistency is guaranteed by construction. If we ever build re-finalize (new .hlx version), we'll re-package ALL sheets from the full result set.

No code changes required for this.

---

## SUMMARY

| Change | Status | Blocker |
|--------|--------|---------|
| 1. Entity sheets (customers.hlm + inventory.hlm) | Ready to implement | Questions A & B above |
| 2. Per-field provenance (__provenance__) | Stub ready, full depends on Transforma | Approve stub approach? |
| 3. Cross-sheet consistency | No action needed | None |

**We need answers to Questions A, B, and the stub approval before proceeding.**

---

## FILES WE'LL MODIFY

| File | Change |
|------|--------|
| `src/processing/models.py` | Add `field_provenance: dict[str, str]` stub to `ResolvedInvoice` |
| `src/orchestrator/preview_generator.py` | Add `_build_customers_sheet()`, `_build_inventory_sheet()`, `__provenance__` serialization, `provenance_default` on columns |
| `src/orchestrator/models.py` | Add customer/inventory counts to `StatisticsModel` |
| `tests/orchestrator/test_preview_generator.py` | Add tests for new sheets, provenance, `__IS_NEW__` flag |
