# WS3 — Architecture Response to HLX Integration Questions

**Date:** 2026-03-24
**From:** Bob (Architect) + Opus
**To:** WS3 Implementation Team
**Re:** `WS3_HLX_RESPONSE.md` — Answers to Questions A, B, and stub approval

---

## Answer A: `customer_type` Inference — APPROVED (Your Proposed Approach)

Use the invoice `transaction_type` inference with priority B2G > B2B > B2C.

```
B2G > B2B > B2C
```

- Group invoices by `customer_id`
- If ANY invoice has `transaction_type == "B2G"` → customer is `B2G`
- Else if ANY has `transaction_type == "B2B"` → customer is `B2B`
- Else → `B2C`

**Reasoning:** This is a view-only informational sheet. The inference is good enough for display. If it's wrong, the user corrects it through the Customer List tab (WS4 entity update), not from the ReviewPage.

---

## Answer B: `vat_treatment` Default — REJECTED (Do NOT default to STANDARD)

**Do NOT default to STANDARD.** Transforma handles VAT treatment — both initial extraction from source and enrichment. The source document may explicitly specify VAT treatment, and Transforma evaluates this confidently. By the time WS3 sees the data, Transforma has already set `vat_treatment` (with provenance `ORIGINAL` if from source, `HIS` if enriched, or `MISSING` if neither could determine it).

**WS3's job:** Just map through whatever value Transforma/WS2 put on `vat_treatment`. Do NOT override or default. If the field is null, leave it null — the provenance will be `MISSING` and the user can edit it on the ReviewPage.

---

## Answer C: Stub Approach — PARTIALLY APPROVED

**Steps 2 and 3 approved. Step 1 reassigned. Step 4 clarified.**

1. ~~Add `field_provenance` stub to `ResolvedInvoice`~~ — **Reassigned to Transforma.** Transforma owns provenance. The `field_provenance: dict[str, str]` attribute belongs on `TransformedInvoice`, `EnrichedInvoice`, and `ResolvedInvoice` — but Transforma's framework adds it, not WS3. See `WS_TRANSFORMA/WS_TRANSFORMA_PROVENANCE_NOTE.md`.
2. Write serialization logic for `__provenance__` that emits when non-empty — **approved, this IS your job**
3. Add `provenance_default` to column definitions — **approved, this IS your job**
4. ~~Populate later when Transforma delivers~~ — **Transforma populates `field_provenance` at transform time.** By the time WS3 runs, the data already has provenance. No "later" — it should be there from the start. If Transforma hasn't been updated yet, the dict will be empty and your serialization safely emits nothing.

**Bottom line:** WS3 does NOT create or stub `field_provenance`. WS3 reads it (from Transforma → WS2 pipeline output) and serializes it into .hlm `__provenance__` objects. If the dict is empty (Transforma not yet updated), WS3 emits no provenance — safe and correct.

---

## SUMMARY

| Question | Decision |
|----------|----------|
| A. `customer_type` inference | B2G > B2B > B2C priority. Approved. |
| B. `vat_treatment` default | **REJECTED.** Do NOT default. Transforma handles extraction + enrichment. WS3 maps through. |
| C. Stub `field_provenance` | **Reassigned to Transforma.** WS3 only serializes — does not create or stub. Serialization logic + `provenance_default` on columns approved. |

**You are clear to proceed with Change 1 (entity sheets) and the serialization parts of Changes 2-3. Do NOT add `field_provenance` to models or default `vat_treatment` — those are Transforma's responsibility.**

---

## NEW REFERENCE

- **`WS_TRANSFORMA/WS_TRANSFORMA_PROVENANCE_NOTE.md`** — Provenance ownership moved to Transforma. WS3 reads `field_provenance`, does not create it.
