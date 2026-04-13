# WS5 Part 2 — Master Implementation Checklist

**Date:** 2026-03-26
**Purpose:** NOTHING GETS LOST. Every decision from the painstaking audit mapped to a session, a file, and a checkbox.
**Cross-reference:** `WS5_PART2_RECONCILIATION.md` for full discussion context.

---

## SESSION PLAN (5 Sessions)

| Session | Focus | Key Deliverables |
|---------|-------|-----------------|
| **2A** | Record Creator Reconciliation | record_creator.py (invoices 41→89 cols, inventory 18→27 cols, customers 14→21 cols), field_helpers.py, line_items audit, edit validator editability, pipeline wiring, tests |
| **2B** | Aggregate Worker + Metrics | aggregate_worker.py (Helium Debounce), per-entry aggregates (customer/inventory), tenant_invoice_metrics table, DataCount minimums, SSE events |
| **3** | WS-ERROR Cross-Pipeline Error Handling | Error audit (every except block WS1-5), retry policies, timeout hierarchy, status_notes impl, WS-SCANNER, graceful shutdown, idempotency, integration tests |
| **4** | Schema Bump + Cross-Service Alignment | Canonical SQL v2.1.3.0, invoice_line_items TABLE 2 update, SDK schema.py, sync.db migration, HLX alignment, Fixed PDF stamper, Relay x_trace_id wiring, SDK finalize context, Pydantic inter-service schemas, PRO- prefix docs |
| **5** | Documentation + Metrics Wiring | SWDB field reference (all fields → who/when/where/FIRS), naming glossary, P0-P4 display spec, source_id documentation, Flash Card real data wiring |

---

## SESSION 2A: Record Creator Reconciliation

### Invoices — Bug Fixes
- [ ] `status` → `workflow_status`, value `"FINALIZED"` → `"COMMITTED"` (IF-023)
- [ ] `currency_code` → `document_currency_code` (IF-010)
- [ ] Remove `reference_irn` from INSERT (IF-059)
- [ ] Remove `reference_issue_date` from INSERT (IF-059)
- [ ] `category` → set NULL (scrapped at invoice level) (IF-058)

### Invoices — Add 53 Missing Fields to INSERT
- [ ] `helium_invoice_no` — `f"PRO-{company_id}-{invoice_id}"` (IF-001)
- [ ] `invoice_trace_id` — `str(uuid7())` per invoice (IF-002)
- [ ] `user_trace_id` — from request_context, NULL until SDK wired (IF-003)
- [ ] `x_trace_id` — from request_context, NULL until Relay wired (IF-004)
- [ ] `config_version_id` — from request_context (IF-005)
- [ ] `schema_version_applied` — cached from schema_version table (IF-006)
- [ ] `document_type` — `row.get("document_type", "COMMERCIAL_INVOICE")` (IF-007)
- [ ] `issue_time` — `row.get("issue_time")` (IF-008)
- [ ] `payment_due_date` — computed from payment_terms_note (IF-009)
- [ ] `tax_currency_code` — `row.get("tax_currency_code", "NGN")` (IF-011)
- [ ] `exchange_rate` — `row.get("exchange_rate")` (IF-012)
- [ ] `has_discount` — computed from line items (IF-013)
- [ ] `wht_amount` — `row.get("wht_amount")` (IF-014)
- [ ] `discount_amount` — computed sum (IF-015)
- [ ] `adjustment_type` — `row.get("adjustment_type")` (IF-016)
- [ ] `payment_means` — `row.get("payment_means")` (IF-017)
- [ ] `firs_payment_means_code` — `row.get("firs_payment_means_code")` (IF-018)
- [ ] `delivery_date` — `row.get("delivery_date")` (IF-019)
- [ ] `delivery_address` — `row.get("delivery_address")` (IF-020)
- [ ] `purchase_order_number` — `row.get("purchase_order_number")` (IF-021)
- [ ] `contract_number` — `row.get("contract_number")` (IF-022)
- [ ] `transmission_status` — `"NOT_REQUIRED"` (IF-024)
- [ ] `payment_status` — `"UNPAID"` (IF-025)
- [ ] `helium_user_id` — from `created_by` param (IF-028)
- [ ] `user_email` — from request_context, NULL (IF-029)
- [ ] `user_name` — from request_context, NULL (IF-030)
- [ ] `created_by` — same as helium_user_id (IF-031)
- [ ] `queue_id` — from request body (IF-032)
- [ ] `file_id` — from request_context (IF-033)
- [ ] `blob_uuid` — from core_queue lookup (IF-034)
- [ ] `original_filename` — from core_queue lookup (IF-035)
- [ ] `source` — `"BULK_UPLOAD"` default (IF-060)
- [ ] `source_id` — from request_context, NULL for now (IF-060)
- [ ] `product_summary` — computed string (IF-037)
- [ ] `line_items_count` — `len(line_items)` (IF-038)
- [ ] `foc_line_count` — computed count (IF-039)
- [ ] `document_source` — `row.get("document_source")` (IF-040)
- [ ] `other_taxes` — `row.get("other_taxes", 0)` (IF-041)
- [ ] `custom_duties` — `row.get("custom_duties")` (IF-042)
- [ ] `finalized_at` — now (IF-049)
- [ ] `processing_started_at` — from request_context (IF-050)
- [ ] `processing_completed_at` — now (IF-051)
- [ ] `processing_duration_ms` — computed (IF-052)
- [ ] `machine_guid` — from request_context, NULL (IF-053)
- [ ] `mac_address` — from request_context, NULL (IF-054)
- [ ] `computer_name` — from request_context, NULL (IF-055)
- [ ] `float_id` — from request_context, NULL (IF-056)
- [ ] `session_id` — from request_context, NULL (IF-057)
- [ ] `seller_id`, `seller_business_id`, `seller_tax_id`, `seller_rc_number`, `seller_email`, `seller_phone` (IF-043)
- [ ] `buyer_id`, `buyer_business_id`, `buyer_tax_id`, `buyer_rc_number`, `buyer_email`, `buyer_phone` (IF-044)

### Invoices — Pipeline Logic
- [ ] COMMITTED→QUEUED two-step after Edge accepts (IF-026)
- [ ] core_queue.status → FINALIZED after commit (IF-027)
- [ ] Add REFERENCE_INSERT for invoice_references table (IF-059)

### Inventory — Add 9 Fields to UPSERT INSERT
- [ ] `helium_sku` — auto-generate `f"HEL-{product_id[:8]}"` (IF-061)
- [ ] `oem_sku` — `item.get("oem_sku")` (IF-062)
- [ ] `is_tax_exempt` — `item.get("is_tax_exempt", 0)` (IF-063)
- [ ] `currency` — `item.get("currency", "NGN")` (IF-064)
- [ ] `hs_codes`, `service_codes`, `product_categories`, `service_categories` — JSON PDP arrays (IF-065)
- [ ] `updated_by` — same as `created_by` (IF-066)

### Inventory — ON CONFLICT Changes
- [ ] Remove `total_times_invoiced + 1` (IF-067)
- [ ] Remove `last_invoice_date = EXCLUDED.updated_at` (IF-067)
- [ ] Add COALESCE for 4 PDP JSON fields (IF-065)
- [ ] Add `updated_by = EXCLUDED.updated_by` (IF-066)
- [ ] Add `helium_sku = inventory.helium_sku` — never overwrite (IF-061)

### Customers — Add 7 Fields to UPSERT INSERT
- [ ] `rc_number`, `tax_id`, `email`, `phone`, `business_id` — from row via prefix (IF-068)
- [ ] `updated_by`, `created_by` — from `created_by` param (IF-068)

### Customers — ON CONFLICT Changes
- [ ] Add 5 COALESCE fields (rc_number, tax_id, email, phone, business_id) (IF-069)
- [ ] Add `updated_by = EXCLUDED.updated_by` (IF-069)

### Invoice Line Items — Full Audit (DF-029)
- [ ] Verify ghost columns (unit_of_measure, discount_amount, vat_treatment) against canonical TABLE 2
- [ ] Add missing: `line_item_type`, `tax_rate`, `product_code`, `product_name`
- [ ] Add self-containment fields: `customer_sku`, `oem_sku`, `helium_sku`, `full_description`, `classification_confidence`, `classification_source`, `vat_rate`

### Edit Validator — Editability Enforcement (DF-026)
- [ ] Define whitelist of editable fields between preview and finalize
- [ ] Reject modifications to immutable fields (IRN, invoice_id, seller_tin, direction, etc.)

### New Files
- [ ] `field_helpers.py` — all computed field functions
- [ ] Tests: `test_field_helpers.py`, `test_record_creator.py` (extended), `test_edit_validator_editability.py`

### Pipeline + Router Wiring
- [ ] `pipeline.py` — add `request_context` param, pass through to record_creator
- [ ] `router.py` — build request_context from body + core_queue lookup (DF-014)
- [ ] `router.py` — record `processing_started_at` at request receipt

---

## SESSION 2B: Aggregate Worker + Metrics

### Aggregate Worker Module
- [ ] `aggregate_worker.py` — new file with Helium Debounce pattern (DF-021)
- [ ] Debounce guards: don't start if running, minimum 4-5s interval, keep latest trigger only
- [ ] Entity-agnostic: runs all aggregation logic, not tracking affected entities

### Per-Entry Aggregates (Non-Critical Path)
- [ ] Customer aggregates: `total_invoices`, `total_pending`, `total_lifetime_value`, `total_lifetime_tax`, `average_invoice_size`, `last_invoice_date`, `last_active_date`, `last_purchased_date`, `last_inbound_date` (9 fields)
- [ ] Inventory aggregates: `total_times_invoiced`, `last_invoice_date`, `total_revenue`, `avg_unit_price`, `top_customer` (5 fields)
- [ ] Per-invoice lightweight snapshots: `customer_total_invoices_at_commit`, `customer_lifetime_value_at_commit`, `customer_compliance_score_at_commit` (3 fields)
- [ ] Error handling: same connection, new transaction per entity, individual try/except (DF-030)

### Tenant-Level Metrics Table (DF-016)
- [ ] `tenant_invoice_metrics` CREATE TABLE — volume, status, financial, quality, drift, payment fields
- [ ] `tenant_invoice_metrics_history` CREATE TABLE — daily snapshots for sparklines
- [ ] Compliance score computation (5-component weighted: first_pass 25%, rejection 25%, timeliness 20%, data_quality 15%, payment 15%)
- [ ] DataCount minimums: total_invoices, total_customers, total_inventory_items (DF-022)

### SSE Events
- [ ] `customer.aggregates_updated` event factory
- [ ] `product.aggregates_updated` event factory
- [ ] `metrics.updated` event factory (for Flash Card/DataCount)

### Wiring
- [ ] Wire aggregate worker into pipeline.py (asyncio.create_task post-commit)
- [ ] `errors.py` — add `AggregateUpdateError`
- [ ] Tests: `test_aggregate_worker.py`, `test_tenant_metrics.py`

---

## SESSION 3: WS-ERROR Cross-Pipeline Error Handling

### From WS_ERROR_HANDLING_KICKSTARTER.md (DF-027)
- [ ] Error audit: read every `except` block in WS1-WS5, document gaps
- [ ] Retry policy document (per phase, per service) — walk through with architect
- [ ] Timeout configuration (overall + per-phase + per-service)
- [ ] Dead letter handling for permanently failed items
- [ ] Graceful shutdown implementation (SIGTERM handling)
- [ ] Idempotency guards (prevent double-processing)
- [ ] User-facing error messages (what Float shows for each failure class)
- [ ] Resource limits (max file size, max invoices, memory guards)
- [ ] Integration tests: simulate each failure mode and verify recovery
- [ ] Error handling documentation (DECISIONS update + runbook)

### WS5-Specific Error Handling
- [ ] `status_notes` JSON column implementation (DF-009) — lifecycle error journal
- [ ] Set at commit from pipeline warnings, append on edge_update failures
- [ ] SWDB display: show when status=ERROR, hide when resolved

### WS-SCANNER (DF-017)
- [ ] Universal Core Scanner — generalizes queue_scanner.py
- [ ] Scan: core_queue stuck PROCESSING, invoices stuck COMMITTED, invoices stuck QUEUED
- [ ] Committed Invoice Scanner (DF-007) — Edge retry for stuck invoices

### WS5 Finalize Error Scenarios
- [ ] IRN collision handling (regenerate? fail?)
- [ ] Partial finalize (50/100 succeed — atomic or partial?)
- [ ] Edge queue rejection handling
- [ ] Re-finalize max attempts

---

## SESSION 4: Schema Bump + Cross-Service Alignment

### Canonical Schema SQL Updates — v2.1.3.0 (DF-024)
- [ ] `06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql`:
  - [ ] Add `status_notes TEXT`
  - [ ] Add `fixed_invoice_blob_uuid TEXT` (DF-010)
  - [ ] Add `finalize_trace_id TEXT` (DF-033)
  - [ ] Add `firs_submitted_payload TEXT` (DF-034) — outbound AND inbound payloads
  - [ ] Add `payment_updated_at TEXT`
  - [ ] Add `payment_updated_by TEXT`
  - [ ] Add `customer_total_invoices_at_commit INTEGER`
  - [ ] Add `customer_lifetime_value_at_commit REAL`
  - [ ] Add `customer_compliance_score_at_commit INTEGER`
  - [ ] REMOVE `category` column
  - [ ] Bump version to v2.1.3.0
  - [ ] Add schema_version INSERT for v2.1.3.0
- [ ] `invoice_line_items` TABLE 2 updates:
  - [ ] Add `customer_sku TEXT`
  - [ ] Add `oem_sku TEXT`
  - [ ] Add `helium_sku TEXT`
  - [ ] Add `full_description TEXT`
  - [ ] Add `classification_confidence REAL`
  - [ ] Add `classification_source TEXT`
  - [ ] Add `vat_rate REAL`
- [ ] `01_CANONICAL_FIELD_LIST_V1.md` — update to reflect all changes
- [ ] `tenant_invoice_metrics` table — add to Core schema init

### HLX Format Alignment (DF-025)
- [ ] Verify `.hlm` data dict carries all new fields pipeline needs
- [ ] Update `HLX_FORMAT.md` if format changes needed
- [ ] Version bump if required

### SDK schema.py + sync.db (DF-011, DF-035)
- [ ] Update `Float/App/src/sdk/database/schema.py` INVOICES_TABLE → match v2.1.3.0
- [ ] Update LINE_ITEMS_TABLE if new fields added
- [ ] Create migration for existing sync.db (ALTER TABLE for new columns)
- [ ] Verify live sync.db at `Float/App/data/sync.db`

### PRO- Prefix (DF-005)
- [ ] `Documentation/Schema/invoice/01_CANONICAL_FIELD_LIST_V1.md` — WM → PRO
- [ ] `Documentation/Schema/invoice/generate_sample_invoices_db.py` — WM → PRO
- [ ] `Float/App/src/sdk/database/models.py` — WM → PRO
- [ ] `06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` comments — WM → PRO

### Cross-Service Wiring
- [ ] Relay x_trace_id into metadata (DF-001) — 3 insertion points in ingestion.py
- [ ] SDK finalize request extension (DF-002) — core_client.py, finalize_worker.py, upload_flow.py, result_page.py
  - [ ] Include `finalize_trace_id` (SDK generates fresh UUIDv7 at finalize gesture) (DF-033)
  - [ ] Include data traces: `user_trace_id`, `x_trace_id` (immutable from upload)
  - [ ] Include finalizer identity: `helium_user_id`, `user_email`, `user_name`, `machine_guid`, `mac_address`, `computer_name`, `float_id`, `session_id`
- [ ] Core core_queue trace columns (DF-003) — `user_trace_id`, `x_trace_id` on core_queue
- [ ] HeartBeat blob processing stats (DF-004) — DEFER to HeartBeat session

### Pydantic Inter-Service Schemas (DF-018)
- [ ] Decide location: `helium_contracts/` shared package or per-service?
- [ ] SDK → Relay ingest payload model
- [ ] Relay → Core enqueue payload model
- [ ] SDK → Core finalize payload model (including finalize_context)
- [ ] Core → Edge submit payload model
- [ ] Edge → Core callback payload model

### Fixed PDF Stamper (DF-028)
- [ ] `helium_formats/pdf_stamper/stamper.py` — FixedPDFStamper class
- [ ] `helium_formats/pdf_stamper/models.py` — StampPlacement dataclass
- [ ] `helium_formats/pdf_stamper/placement.py` — EIC-driven placement resolver
- [ ] `helium_formats/pdf_stamper/defaults.py` — default placement (no EIC)
- [ ] Wire into WS5 pipeline (post-finalize, PDF source only)
- [ ] Tests
- [ ] Dependencies: pypdf, reportlab, Pillow

---

## SESSION 5: Documentation + Metrics Wiring

### SWDB Field Reference (DF-015)
- [ ] Build comprehensive table: Field | Type | Set By | When Set | SWDB Column | FIRS Mapping
- [ ] Cover ALL 121+ invoice fields
- [ ] Cover customer fields visible in SWDB
- [ ] Cover inventory fields visible in SWDB

### Naming Glossary (DF-023)
- [ ] `batch_id` = `data_uuid` = multi-file upload group
- [ ] `file_id` = individual file within batch
- [ ] `blob_uuid` = HeartBeat storage identifier
- [ ] `queue_id` = Core staging entry
- [ ] `source_id` = registered source system (float_id / connection_id)
- [ ] `original_filename` = `sourcefile_name` = human-readable source file

### Source System Documentation (DF-019)
- [ ] Define what a "source" is and how sources are registered
- [ ] Document source_id assignment: float_id for BULK_UPLOAD, connection_id for ERPs
- [ ] Document pipeline wiring from HeartBeat registration → invoice commit

### P0-P4 Line Item Display Spec (DF-020)
- [ ] Document priority system: P0 (description) > P1 (full_description) > P2 (customer_sku) > P3 (oem_sku) > P4 (helium_sku)
- [ ] Document invoice popup 2-line rendering rule
- [ ] Verify all 5 fields present in invoice_line_items + inventory schemas
- [ ] Update SWDB documentation

### Flash Card Metrics Wiring (DF-032)
- [ ] Core aggregate worker → SSE events with updated metrics
- [ ] SDK receives SSE → updates local metrics cache
- [ ] Flash Card reads from SDK cache (replace mock data in stats_flash_card.py)
- [ ] DataCount widgets read from SDK cache
- [ ] `tenant_revenue_buckets` materialized view for VIEW 1 histogram

### Additional Documentation
- [ ] Invoice approval journey design (DF-006) — for future multi-step approval
- [ ] Transforma/edit_validator mandatory field enforcement spec (DF-008)
- [ ] Source pipeline wiring spec (DF-012)

---

## TEAM NOTES (Copy-paste ready — from reconciliation session)

### Note 1: SDK Team — Finalize Request Extension
**File:** `Float/App/src/sdk/clients/core_client.py`
**Priority:** High — blocks trace/audit coverage
**Change:** Extend finalize payload with traces (user_trace_id, x_trace_id, finalize_trace_id) + identity (helium_user_id, user_email, user_name, machine_guid, mac_address, computer_name, float_id, session_id)
**Key insight:** Traces are IMMUTABLE from upload. Identity is RE-EVALUATED at finalize (maker-checker pattern).

### Note 2: Relay Team — x_trace_id Propagation
**File:** `Relay/src/services/ingestion.py`
**Priority:** Medium — trace chain gap
**Change:** Merge `request.state.trace_id` into metadata dict before HeartBeat/Core calls (3 insertion points, 1 line each)

### Note 3: Core WS1 — Queue Trace Columns
**File:** `Core/src/database/schemas/core.sql`, `queue_repository.py`
**Priority:** Low — belt-and-suspenders
**Change:** Add `user_trace_id TEXT`, `x_trace_id TEXT` to core_queue. Store from enqueue metadata.
