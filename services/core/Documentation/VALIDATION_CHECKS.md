# CORE SERVICE — CANONICAL VALIDATION CHECKS

**Version:** 1.0
**Date:** 2026-03-18
**Status:** CANONICAL — Any workstream implementing validation MUST reference and update this document
**Update Rule:** If a WS adds, modifies, or removes a check, update THIS file. This is the single source of truth.

---

## HOW TO READ THIS DOCUMENT

Each check has:
- **ID**: `V-{TYPE}-{NNN}` (V-INV for invoice, V-CUST for customer, V-PROD for inventory)
- **Check**: What is validated
- **Severity**: `error` (blocks finalization), `warning` (flags for review), `info` (informational)
- **Phase**: Which pipeline phase runs this check (2=Parse, 3=Transform, 4=Enrich, 5=Resolve, 8=Finalize)
- **Red Flag Type**: Maps to the red_flags taxonomy in report.json
- **Auto-resolvable**: Can Core fix this without user intervention?

---

## SECTION 1: INVOICE CHECKS (V-INV)

### 1A. Required Fields (Phase 2: Parse)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-001 | `invoice_number` must not be empty | error | `missing_required_field` | No |
| V-INV-002 | `issue_date` must not be empty | error | `missing_required_field` | No |
| V-INV-003 | `issue_date` must be valid ISO date (YYYY-MM-DD) | error | `invalid_date_format` | Sometimes (fuzzy parse) |
| V-INV-004 | `total_amount` must not be empty or zero | error | `missing_required_field` | No |
| V-INV-005 | At least 1 line item must exist | error | `missing_required_field` | No |
| V-INV-006 | `seller_name` must not be empty | error | `missing_required_field` | Yes (from tenant config) |
| V-INV-007 | `seller_tin` must not be empty | error | `missing_supplier_tin` | Yes (from tenant config) |
| V-INV-008 | `currency` must be present (default NGN) | warning | `unsupported_currency` | Yes (default NGN) |

### 1B. Format Validation (Phase 2: Parse + Phase 3: Transform)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-010 | `issue_date` must not be in the future | warning | `invalid_date_format` | No |
| V-INV-011 | `due_date` must be >= `issue_date` if present | warning | `invalid_date_format` | No |
| V-INV-012 | `currency` must be `NGN` (only supported currency) | error | `unsupported_currency` | No |
| V-INV-013 | `seller_tin` must match format `\d{8}-\d{3,4}` or 13-digit tax_id | error | `invalid_tin_format` | No |
| V-INV-014 | `buyer_tin` must match TIN format (if B2B/B2G) | error | `invalid_tin_format` | No |
| V-INV-015 | `firs_invoice_type_code` must be one of: 380, 381, 383, 389, 261 | error | `invalid_type_code` | Yes (from InvoiceTypeMapping) |
| V-INV-016 | `direction` must be OUTBOUND or INBOUND | error | `invalid_direction` | Yes (default OUTBOUND) |
| V-INV-017 | `document_type` must be valid enum value | error | `invalid_document_type` | Yes (from type code) |
| V-INV-018 | `transaction_type` must be B2B, B2G, or B2C | error | `invalid_transaction_type` | Yes (from B2BConfig) |

### 1C. Mathematical Validation (Phase 2: Parse + Phase 3: Transform)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-020 | Sum of line_item.line_total must equal `subtotal` (±1% tolerance) | error | `amount_mismatch` | No |
| V-INV-021 | `subtotal` + `tax_amount` must equal `total_amount` (±1 NGN tolerance) | error | `tax_calculation_error` | No |
| V-INV-022 | Each line_item: `quantity × unit_price` must equal `line_total` (±0.01 tolerance) | warning | `amount_mismatch` | Yes (recalculate) |
| V-INV-023 | `tax_amount` must be reasonable for VAT rate (e.g., 7.5% of taxable amount ±1%) | warning | `tax_calculation_error` | No |
| V-INV-024 | `total_amount` must be positive | error | `suspicious_amount` | No |
| V-INV-025 | `total_amount` must not exceed 999,999,999,999.99 (max FIRS amount) | error | `suspicious_amount` | No |
| V-INV-026 | `wht_amount` if present must not exceed `tax_amount` | warning | `tax_calculation_error` | No |
| V-INV-027 | `discount_amount` if present must not exceed `subtotal` | warning | `suspicious_amount` | No |

### 1D. Business Logic (Phase 3: Transform)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-030 | `invoice_number` must not already exist in invoices table | warning | `duplicate_invoice_number` | No |
| V-INV-031 | `total_amount` > 10× customer's average invoice size | warning | `suspicious_amount` | No |
| V-INV-032 | If `document_type` = CREDIT_NOTE, original invoice reference must exist | warning | `missing_reference` | No |
| V-INV-033 | If B2B, `buyer_tin` must not be empty | error | `missing_customer_details` | No |
| V-INV-034 | If B2G, `buyer_tin` must belong to government entity | warning | `invalid_transaction_type` | No |
| V-INV-035 | Seller and buyer must not be the same entity (TIN check) | warning | `suspicious_self_invoice` | No |
| V-INV-036 | If `direction` = INBOUND, `inbound_received_at` must be set | error | `missing_required_field` | Yes (default now()) |

### 1E. FIRS Compliance (Phase 3: Transform)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-040 | Seller address fields must include `city` (FIRS mandatory) | error | `incomplete_address` | No |
| V-INV-041 | Buyer address fields must include `city` (FIRS mandatory for B2B) | warning | `incomplete_address` | No |
| V-INV-042 | `seller_state_code` must be valid (1-37, FIRS format) | warning | `invalid_address` | Yes (from postal validation) |
| V-INV-043 | `notes_to_firs` must not exceed 500 characters | warning | `field_too_long` | Yes (truncate) |
| V-INV-044 | `payment_terms_note` must not exceed 500 characters | warning | `field_too_long` | Yes (truncate) |
| V-INV-045 | Every GOODS line item must have `hsn_code` in XXXX.XX format | error | `missing_hsn_code` | No (HIS enrichment needed) |
| V-INV-046 | Every SERVICE line item must have `service_code` | error | `missing_service_code` | No (HIS enrichment needed) |
| V-INV-047 | `line_item_type` must be GOODS or SERVICE for each line | error | `invalid_line_type` | Yes (from HS/service code) |

### 1F. Enrichment Validation (Phase 4: Enrich — HIS/IntelliCore)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-050 | HIS HSN code confidence >= 85% | info | — (auto-accepted) | Yes |
| V-INV-051 | HIS HSN code confidence 60-84% | warning | `enrichment_uncertain` | No (user review) |
| V-INV-052 | HIS HSN code confidence < 60% | error | `missing_hsn_code` | No (manual entry) |
| V-INV-053 | HIS VAT treatment must match line item's tax_category | warning | `tax_calculation_error` | Yes (override) |
| V-INV-054 | Postal validation: seller address state/LGA must be consistent | warning | `invalid_address` | Yes (from HIS) |
| V-INV-055 | IntelliCore overall confidence >= 90% (for PDF invoices) | info | — | — |
| V-INV-056 | IntelliCore confidence 85-89% | warning | `low_ocr_confidence` | No (user review) |
| V-INV-057 | IntelliCore confidence < 85% | error | `low_ocr_confidence` | No (user correction) |
| V-INV-058 | IntelliCore multi-invoice detection | warning | `multi_invoice_detected` | No (user review) |
| V-INV-059 | IntelliCore proforma/draft detection | warning | `proforma_detected` | No (user decision) |

### 1G. Deduplication (Phase 2: Parse)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-INV-060 | File SHA256 must not exist in `processed_files` | warning | `duplicate_file` | No (skip or force) |
| V-INV-061 | IRN must be unique in `invoices` table | error | `duplicate_irn` | Yes (idempotent return) |

---

## SECTION 2: CUSTOMER CHECKS (V-CUST)

### 2A. Required Fields (Phase 5: Resolve)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-CUST-001 | `company_name` must not be empty | error | `missing_customer_details` | No |
| V-CUST-002 | At least one identifier: `tin` OR `rc_number` OR `tax_id` | warning | `missing_customer_details` | No |
| V-CUST-003 | `primary_identifier` must be set if TIN or RC present | warning | `missing_customer_details` | Yes (default to TIN if tin present) |

### 2B. Format Validation (Phase 5: Resolve)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-CUST-010 | `tin` format: `\d{8}-\d{3,4}` or empty | error | `invalid_tin_format` | No |
| V-CUST-011 | `rc_number` format: `RC\d{6,7}` or 13-digit CAC or empty | error | `invalid_rc_format` | No |
| V-CUST-012 | `tax_id` format: 13-digit FIRS Tax ID or empty | warning | `invalid_tax_id_format` | No |
| V-CUST-013 | `email` format: valid email pattern if present | warning | `invalid_email` | No |
| V-CUST-014 | `phone` format: Nigerian phone format if present | warning | `invalid_phone` | No |
| V-CUST-015 | `country_code` must be valid ISO 3166-1 alpha-2 | warning | `invalid_country_code` | Yes (default NG) |

### 2C. Entity Resolution (Phase 5: Resolve)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-CUST-020 | TIN match found but company_name differs significantly (>30% Levenshtein distance) | warning | `customer_tin_mismatch` | No |
| V-CUST-021 | Multiple customers match by fuzzy name (ambiguous) | warning | `ambiguous_customer_match` | No |
| V-CUST-022 | No existing customer found — new customer will be created | info | `new_customer_created` | Yes |
| V-CUST-023 | Matched customer has `compliance_score` < 30 | warning | `customer_risk_profile` | No |
| V-CUST-024 | Matched customer is missing required details for FIRS (Porto Bello scenario) | warning | `customer_incomplete` | No |

### 2D. Address Validation (Phase 4: Enrich via HIS)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-CUST-030 | `state` must be a valid Nigerian state name | warning | `invalid_address` | Yes (from HIS) |
| V-CUST-031 | `state_code` must be valid (1-37, FIRS format) | warning | `invalid_address` | Yes (from HIS) |
| V-CUST-032 | `lga_code` must be valid (1-774) and consistent with `state_code` | warning | `invalid_address` | Yes (from HIS) |
| V-CUST-033 | `address` should not be empty for B2B customers | warning | `incomplete_address` | No |

---

## SECTION 3: INVENTORY/PRODUCT CHECKS (V-PROD)

### 3A. Required Fields (Phase 5: Resolve)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-PROD-001 | `product_name` must not be empty | error | `missing_product_details` | No |
| V-PROD-002 | `type` must be GOODS or SERVICE | error | `invalid_product_type` | Yes (infer from HS/service code) |
| V-PROD-003 | If type=GOODS, `hsn_code` should be present (after enrichment) | warning | `missing_hsn_code` | No (HIS enrichment needed) |
| V-PROD-004 | If type=SERVICE, `service_code` should be present (after enrichment) | warning | `missing_service_code` | No (HIS enrichment needed) |

### 3B. Format Validation (Phase 3: Transform + Phase 4: Enrich)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-PROD-010 | `hsn_code` format: `\d{4}\.\d{2}` (XXXX.XX) | error | `invalid_hsn_format` | No |
| V-PROD-011 | `hsn_code` must exist in HIS master list (6,940 valid codes) | warning | `invalid_hsn_code` | No |
| V-PROD-012 | `vat_treatment` must be STANDARD, ZERO_RATED, or EXEMPT | error | `invalid_vat_treatment` | Yes (default STANDARD) |
| V-PROD-013 | `vat_rate` must be 0, 5, or 7.5 (valid Nigerian rates) | warning | `invalid_vat_rate` | Yes (from HIS) |
| V-PROD-014 | `unit_of_measure` should follow UN/ECE Recommendation 20 codes | info | `nonstandard_uom` | No |

### 3C. Entity Resolution (Phase 5: Resolve)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-PROD-020 | Multiple products match by fuzzy name (ambiguous) | warning | `ambiguous_product_match` | No |
| V-PROD-021 | No existing product found — new product will be created | info | `new_product_created` | Yes |
| V-PROD-022 | Matched product has different `type` than line item | warning | `product_type_mismatch` | No |
| V-PROD-023 | Matched product has different `hsn_code` than enrichment result | warning | `hsn_code_conflict` | No |

### 3D. Classification Validation (Phase 4: Enrich via HIS)

| ID | Check | Severity | Red Flag Type | Auto-resolvable |
|----|-------|----------|---------------|-----------------|
| V-PROD-030 | HIS classification confidence >= 85% | info | — (auto-accepted) | Yes |
| V-PROD-031 | HIS classification confidence 60-84% — top 3 candidates returned | warning | `enrichment_uncertain` | No (user picks) |
| V-PROD-032 | HIS classification confidence < 60% | error | `classification_failed` | No (manual entry) |
| V-PROD-033 | GOODS product must have both `hsn_code` and `product_category` | warning | `incomplete_classification` | No |
| V-PROD-034 | SERVICE product must have both `service_code` and `service_category` | warning | `incomplete_classification` | No |

---

## SECTION 4: CROSS-ENTITY CHECKS (V-CROSS)

| ID | Check | Severity | Phase | Red Flag Type | Auto-resolvable |
|----|-------|----------|-------|---------------|-----------------|
| V-CROSS-001 | Invoice seller_tin must match tenant's TIN from config | error | 3 | `seller_tin_mismatch` | Yes (override from config) |
| V-CROSS-002 | Invoice buyer must resolve to a valid customer record | warning | 5 | `customer_incomplete` | Yes (create new) |
| V-CROSS-003 | All line item products must resolve to inventory records | warning | 5 | `product_unresolved` | Yes (create new) |
| V-CROSS-004 | Customer TIN on invoice must match customer.tin in database (if matched) | warning | 5 | `customer_tin_mismatch` | No |
| V-CROSS-005 | Line item HSN code must match product's canonical hsn_code (if matched) | info | 5 | `hsn_code_conflict` | No |

---

## SUMMARY STATISTICS

| Data Type | Total Checks | Errors | Warnings | Info |
|-----------|-------------|--------|----------|------|
| Invoice | 40 | 18 | 18 | 4 |
| Customer | 14 | 3 | 10 | 1 |
| Inventory | 14 | 3 | 9 | 2 |
| Cross-Entity | 5 | 1 | 3 | 1 |
| **Total** | **73** | **25** | **40** | **8** |

---

## UPDATE PROTOCOL

When a workstream implements or modifies a validation check:
1. Find the check in this document by ID
2. Add `[Implemented: WS{N}]` marker to the check
3. If adding a new check: Assign the next sequential ID for that data type
4. If removing a check: Mark as `[DEPRECATED: reason]`, do not delete
5. Commit changes to this file in the same PR as the implementation

---

**Last Updated:** 2026-03-18
**Version:** 1.0 — CANONICAL
