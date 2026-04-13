# WS5 Part 2 — Schema Reconciliation & Aggregate Worker

**Date:** 2026-03-25
**Status:** HANDOFF — To be implemented in a dedicated session
**Predecessor:** WS5 Part 1 (finalize pipeline, edit validator, IRN/QR, record creator, Edge client)
**Owner:** WS5 (Finalize)

---

## WHY THIS EXISTS

WS5 Part 1 built the finalize pipeline but wrote only ~41 of ~115 invoice fields at commit time. Customer aggregates (17 fields), inventory aggregates (3 incomplete), blob batch stats (7 fields), and invoice lifecycle/telemetry fields (~40) are not being updated. This document is the complete field inventory and architecture spec for Part 2.

**Root cause:** Part 1 was designed from the flow (validate → IRN → QR → commit → Edge) without a field-by-field audit against the canonical schemas. The schemas are the contracts. Every field is a write obligation.

---

## CANONICAL SCHEMA LOCATIONS

| Entity | Schema File |
|--------|-------------|
| Invoice | `Helium/Documentation/Schema/invoice/06_INVOICES_DB_CANONICAL_SCHEMA_V2.sql` |
| Customer | `Helium/Documentation/Schema/customer/02_CUSTOMER_DB_CANONICAL_SCHEMA_V1.sql` |
| Inventory | `Helium/Documentation/Schema/inventory/02_INVENTORY_DB_CANONICAL_SCHEMA_V1.sql` |
| Blob | `Helium/Documentation/Schema/blob/04_BLOB_DB_CANONICAL_SCHEMA_V1.sql` |

**These are the single source of truth.** Every field in these files must have a known writer.

---

## PART 2 SCOPE: FOUR WORKSTREAMS

### Stream A: Invoice INSERT Completion

The `record_creator.py` `INVOICE_INSERT` currently writes 41 fields. The canonical invoice schema has ~115 fields. The following must be added to the INSERT (or set immediately after):

#### A1. Fields WS5 Must Set at Commit Time

| Field | Type | Value Source | Notes |
|-------|------|-------------|-------|
| `helium_invoice_no` | TEXT | Generated: `WM-{tenant_id}-{sequential}` | Display identifier. Core generates at COMMITTED transition. Needs a sequence generator. |
| `workflow_status` | TEXT | `"COMMITTED"` (not "FINALIZED") | Schema state machine starts at COMMITTED. "FINALIZED" is the user action, COMMITTED is the DB state. |
| `payment_status` | TEXT | `"UNPAID"` | Default for new invoices. |
| `finalized_at` | TEXT | `datetime.utcnow().isoformat()` | When the user clicked Finalize. |
| `processing_started_at` | TEXT | Passed from pipeline | When Core started processing this batch. |
| `processing_completed_at` | TEXT | `datetime.utcnow().isoformat()` | When commit completed. |
| `processing_duration_ms` | INTEGER | `completed - started` in ms | Computed from telemetry. |
| `queue_id` | TEXT | From finalize request | Links back to core_queue entry. |
| `file_id` | TEXT | From finalize request / queue entry | Source file identifier. |
| `blob_uuid` | TEXT | From finalize request / queue entry | Object storage identifier. |
| `original_filename` | TEXT | From queue entry | User's uploaded filename. |
| `source` | TEXT | `"BULK_UPLOAD"` or `"MANUAL"` | How the invoice entered the system. |
| `source_id` | TEXT | data_uuid | Batch-level source identifier. |
| `config_version_id` | TEXT | From tenant config | Snapshot of active config version at commit time. |
| `schema_version_applied` | TEXT | Hardcoded current version | Which invoice schema version was active. |
| `line_items_count` | INTEGER | `len(row["line_items"])` | Denormalized count at insert. |
| `foc_line_count` | INTEGER | Count of line items with unit_price == 0 | Free-of-charge items. |
| `has_discount` | INTEGER | 1 if any discount > 0, else 0 | Convenience boolean. |
| `wht_amount` | REAL | From row data or 0 | Withholding tax amount. |
| `discount_amount` | REAL | From row data or 0 | Total discount amount. |
| `payment_means` | TEXT | From row data | Payment method (if specified in source). |
| `firs_payment_means_code` | TEXT | From row data | FIRS payment means code. |
| `product_summary` | TEXT | Derived from line items | Brief summary of products (e.g., "Biscuits, Palm Oil, +3 more"). |

#### A2. Fields WS5 Must Set from SDK Request Context (HELIUM_SECURITY_SPEC)

These come from the Float SDK finalize request, not from invoice data:

| Field | Type | Value Source | Notes |
|-------|------|-------------|-------|
| `invoice_trace_id` | TEXT | Generated UUIDv7 | Per-invoice trace. |
| `user_trace_id` | TEXT | From SDK request | Per-user session trace. |
| `x_trace_id` | TEXT | From SDK request | Cross-service correlation ID. |
| `helium_user_id` | TEXT | From SDK request (`created_by`) | Who finalized. |
| `user_email` | TEXT | From SDK request | Email at time of action. |
| `user_name` | TEXT | From SDK request | Display name at time of action. |
| `machine_guid` | TEXT | From SDK request | Machine fingerprint. |
| `mac_address` | TEXT | From SDK request | Network fingerprint. |
| `computer_name` | TEXT | From SDK request | Machine name. |
| `float_id` | TEXT | From SDK request | Float app instance ID. |
| `session_id` | TEXT | From SDK request | SDK session identifier. |

**Implication for router.py:** The finalize request body must be extended to include trace/security fields. The SDK must send these.

#### A3. Fields Set by Other Services (NOT WS5)

| Field | Owner | When |
|-------|-------|------|
| `transmission_status` + related (10 fields) | Edge | On FIRS submission lifecycle events |
| `csid`, `csid_status`, `sign_date` | Edge (signing service) | On invoice signing |
| `firs_confirmation`, `firs_response_data` | Edge | On FIRS response |
| `acknowledgement_date` | Edge | On counterparty acknowledgement |
| `retry_count`, `last_retry_at`, `next_retry_at` | Edge | On retry scheduling |
| `payment_status` updates | Float SDK (user action) | User marks as paid |
| `inbound_*` (7 fields) | Inbound handler (future) | On received invoice events |
| `reminder_count` | Scheduled job | On 72-hour deadline reminders |
| `attachment_count` | PostgreSQL trigger | On invoice_attachments INSERT/DELETE |
| `deleted_at`, `deleted_by` | WS4 or admin | On soft delete |

#### A4. Fields for Inbound Invoices (Deferred)

| Field | Notes |
|-------|-------|
| `inbound_received_at` | Not in scope until inbound invoice processing is built |
| `inbound_status` | Same |
| `inbound_action_at` | Same |
| `inbound_action_by_user_id` | Same |
| `inbound_action_by_user_email` | Same |
| `inbound_action_reason` | Same |
| `inbound_payload_json` | Same |

These are future scope. Document but do not implement.

#### A5. Optional/Nullable Fields (Set If Available)

| Field | Notes |
|-------|-------|
| `delivery_date` | From source document if present |
| `delivery_address` | From source document if present |
| `purchase_order_number` | From source document if present |
| `contract_number` | From source document if present |
| `payment_due_date` | Computed: issue_date + payment terms (if terms specify days) |
| `exchange_rate` | For cross-currency transactions |
| `seller_id`, `buyer_id` | customer_id references for seller/buyer parties |
| `seller_business_id`, `buyer_business_id` | FIRS business registration IDs |

---

### Stream B: Customer Aggregate Updates

After committing invoices, update the counterparty customer's aggregate fields. **17 fields on the customers table.**

#### B1. Fields Updated at Finalize (Per-Invoice)

For each invoice committed, update the counterparty customer:

```sql
UPDATE customers SET
    -- Counters
    total_invoices = total_invoices + 1,
    total_pending = total_pending + 1,

    -- Financial aggregates
    total_lifetime_value = total_lifetime_value + $invoice_total,
    total_lifetime_tax = total_lifetime_tax + $invoice_tax,
    average_invoice_size = (total_lifetime_value + $invoice_total) / (total_invoices + 1),

    -- Date tracking
    last_invoice_date = $issue_date,
    last_active_date = CURRENT_TIMESTAMP,

    -- Direction-specific dates
    last_purchased_date = CASE WHEN $direction = 'OUTBOUND' THEN $issue_date ELSE last_purchased_date END,
    last_inbound_date = CASE WHEN $direction = 'INBOUND' THEN $issue_date ELSE last_inbound_date END,

    -- Timestamp
    updated_at = CURRENT_TIMESTAMP
WHERE customer_id = $customer_id;
```

#### B2. Fields Updated on Transmission Events (Edge Callbacks)

These are NOT WS5's responsibility at finalize time. They're updated when Edge reports back:

| Field | Trigger |
|-------|---------|
| `total_transmitted` | Edge: invoice transmitted to FIRS |
| `total_accepted` | Edge: FIRS accepted invoice |
| `receivables_rejected` | Edge: FIRS rejected OUTBOUND invoice |
| `payable_rejected` | Edge: FIRS rejected INBOUND invoice |
| `total_pending` | Decremented when invoice leaves PENDING |

#### B3. Fields Computed Periodically (Deferred Worker)

| Field | Computation | Frequency |
|-------|-------------|-----------|
| `payable_frequency` | Pattern analysis: average days between inbound invoices | Daily or on-demand |
| `receivables_frequency` | Pattern analysis: average days between outbound invoices | Daily or on-demand |
| `compliance_score` | Weighted score: TIN validation + address completeness + MBS status + activity + rejection rate | Daily or on customer update |
| `compliance_details` | JSON breakdown of compliance components | Same as compliance_score |

---

### Stream C: Inventory Aggregate Updates

After committing invoices, update inventory aggregates for each referenced product. **5 fields, 2 already partially handled.**

#### C1. Fix Existing Updates

Current `_upsert_inventory` does:
- `total_times_invoiced + 1` — correct
- `last_invoice_date = EXCLUDED.updated_at` — correct

Missing:
```sql
-- Add to INVENTORY_UPSERT ON CONFLICT clause:
total_revenue = inventory.total_revenue + $line_total,
avg_unit_price = (inventory.avg_unit_price * inventory.total_times_invoiced + $unit_price)
                 / (inventory.total_times_invoiced + 1),
```

#### C2. top_customer Recomputation

`top_customer` requires a subquery — can't be done inline in the upsert. Two options:

**Option 1 (deferred):** Post-commit async worker queries invoice_line_items grouped by customer, picks the max. Runs after each batch commit.

**Option 2 (inline):** After all line items are committed, run a separate UPDATE:
```sql
UPDATE inventory SET top_customer = (
    SELECT i.buyer_name FROM invoices i
    JOIN invoice_line_items ili ON ili.invoice_id = i.invoice_id
    WHERE ili.product_id = inventory.product_id
    GROUP BY i.buyer_name
    ORDER BY SUM(ili.line_total) DESC
    LIMIT 1
)
WHERE product_id IN ($committed_product_ids);
```

**Recommendation:** Option 2 at finalize time. It's one query per batch, not per invoice.

---

### Stream D: Blob Batch & File Entry Stats

After finalization, update the blob tables that track processing outcomes.

#### D1. blob_batches Updates

```sql
UPDATE blob_batches SET
    status = 'finalized',
    total_invoice_count = $total_invoices,
    total_submitted_count = $submitted_count,
    total_rejected_count = $rejected_count,
    total_duplicate_count = $duplicate_count,
    finalized_at_unix = $unix_timestamp,
    finalized_at_iso = $iso_timestamp,
    processing_time_seconds = $duration_seconds
WHERE batch_uuid = $batch_id;
```

#### D2. file_entries Updates

```sql
UPDATE file_entries SET
    status = 'finalized',
    extracted_invoice_count = $extracted_count,
    submitted_invoice_count = $submitted_count,
    rejected_invoice_count = $rejected_count,
    duplicate_count = $duplicate_count,
    finalized_at_unix = $unix_timestamp,
    finalized_at_iso = $iso_timestamp
WHERE batch_uuid = $batch_id;
```

**Note:** file_entries may have multiple rows per batch (one per file in the upload). Stats need to be split by source file, or aggregated at batch level. This depends on how WS1/WS3 track which invoices came from which file.

---

## DEFERRED COMPUTATION ARCHITECTURE

### The Problem

Updating 40+ aggregate fields synchronously during the finalize transaction would:
1. Slow down the real-time response to Float
2. Increase transaction lock time
3. Risk timeout on large batches

### The Solution: Two-Phase Commit

**Phase 1 (Synchronous — in transaction):**
- Invoice INSERT (all A1 + A2 fields)
- Line items INSERT
- Customer UPSERT (identity fields only — no aggregates)
- Inventory UPSERT (identity fields only — no aggregates)
- Return success to Float immediately

**Phase 2 (Asynchronous — post-commit worker):**
- Customer aggregate updates (B1 fields)
- Inventory aggregate updates (C1 + C2 fields)
- Blob batch/file stats (D1 + D2 fields)
- SSE events to Float (entity.updated with new aggregate values)

### Implementation Options

**Option 1: PostgreSQL NOTIFY/LISTEN**
```python
# After Phase 1 transaction commits:
await conn.execute("NOTIFY finalize_complete, $batch_id")

# Separate worker process:
async for notify in conn.notifies():
    if notify.channel == "finalize_complete":
        await update_aggregates(notify.payload)
```

**Option 2: Post-Commit Async Task**
```python
# In pipeline.py, after commit succeeds:
result.success = True
# Fire-and-forget aggregate update
asyncio.create_task(self._update_aggregates_async(batch_id, company_id, rows))
return result  # Return to Float immediately
```

**Option 3: core_queue Status-Driven**
```python
# After commit, update queue entry:
await queue_repo.mark_finalized(queue_id)

# Existing queue poller picks up FINALIZED entries and runs aggregate updates
```

**Recommendation:** Option 2 for simplicity. The aggregate worker runs in the same process, fires after the HTTP response is sent, and updates arrive at Float via SSE within seconds. No additional infrastructure needed.

### Latency Target

Float uses aggregate fields (e.g., `total_invoices`) for display. The async update must complete within **5 seconds** of the finalize response. For a typical batch of 50 invoices, the aggregate UPDATE queries should complete in <1 second on PostgreSQL.

---

## IMPLEMENTATION PLAN

| Phase | What | Effort | Priority |
|-------|------|--------|----------|
| 1 | Extend INVOICE_INSERT with A1 + A2 fields | 3-4 hours | **First** |
| 2 | Extend finalize request schema (router.py) for security/trace fields | 1-2 hours | **First** |
| 3 | Build async aggregate worker (customer B1 + inventory C1/C2) | 3-4 hours | **Second** |
| 4 | Add blob batch/file stats updates (D1/D2) | 2-3 hours | **Second** |
| 5 | Build `helium_invoice_no` sequence generator | 1-2 hours | **Second** |
| 6 | Build `product_summary` derivation from line items | 30 min | **Third** |
| 7 | Wire SSE events for aggregate updates | 1-2 hours | **Third** |
| 8 | Integration tests with real schema | 3-4 hours | **Third** |
| 9 | Edge callback handler for B2 fields (transmission events) | 2-3 days | **Future (Edge team)** |
| 10 | Periodic compliance_score + frequency computation (B3) | 1-2 days | **Future (scheduled job)** |
| 11 | Inbound invoice handler (A4) | Multi-day | **Future** |

**Total Part 2 estimate:** ~2 days for Phases 1-8. Phases 9-11 are cross-team or future scope.

---

## OPEN QUESTIONS FOR ARCHITECT

1. **`helium_invoice_no` format:** Is `WM-{tenant_id}-{sequential}` confirmed? What's the sequence scope — per-tenant or global? Reset annually?
2. **Security fields:** Does the SDK already send machine_guid, mac_address, session_id in its requests? If not, SDK needs updating first.
3. **`workflow_status` state machine:** Should WS5 set `COMMITTED` (and then Edge transitions to `QUEUED` → `TRANSMITTING`)? Or should WS5 set `QUEUED` directly since we immediately submit to Edge?
4. **Blob batch ownership:** Does WS5 own the blob_batches update, or does WS3 (which created the batch) own it?
5. **`product_summary` format:** What's the template? "Widget X, Palm Oil, +3 more"? Max length?
6. **`payment_due_date` computation:** If `payment_terms_note` says "Net 30", do we parse it and compute issue_date + 30 days? Or is this a Transforma responsibility?

---

## ALSO FIX

### WS3_HLX_ARCH_RESPONSE.md — Question A Inconsistency

Our arch response (line 10) says "APPROVED (Your Proposed Approach)" for customer_type B2G>B2B>B2C inference. But the architect later clarified that **Transforma handles customer_type** — it evaluates during extraction+enrichment. WS3 should map through, not infer.

**Action:** Update `WS3_HLX_ARCH_RESPONSE.md` Answer A to say: "Transforma handles customer_type evaluation and enrichment. WS3 maps through whatever Transforma outputs. The B2G>B2B>B2C priority is only a fallback if Transforma hasn't set customer_type (i.e., field is null)."

---

## REFERENCE DOCUMENTS

- **`WS5_DB_INTEGRITY.md`** — Tier A safeguards (idempotency, audit, ON CONFLICT)
- **`record_creator.py`** — Current INSERT statements to extend
- **`pipeline.py`** — Where async aggregate worker hooks in
- **`HLX_FORMAT.md`** v1.1 — Finalize flow spec
- **Canonical schemas** — The contracts (paths listed above)
- **`WS3_HLX_ARCH_RESPONSE.md`** — Needs Question A fix
