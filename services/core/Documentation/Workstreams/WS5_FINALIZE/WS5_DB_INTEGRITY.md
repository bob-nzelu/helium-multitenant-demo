# WS5 Database Integrity & Recovery

**Date:** 2026-03-25
**Status:** Tier A = IMPLEMENT NOW | Tiers B, C = FUTURE DEBT
**Owner:** WS5 (Finalize)
**Scope:** All DB writes originating from `record_creator.py` (invoices, line_items, customers, inventory)

---

## Context

WS5 is the **only workstream that commits invoice data to the database**. The `RecordCreator` performs `INSERT` into `invoices` and `invoice_line_items`, and `UPSERT` into `customers` and `inventory` — all within a single PostgreSQL transaction per batch.

If this transaction succeeds partially, corrupts data, or is retried unexpectedly, the consequences propagate to:
- Float SDK (stale/duplicate data via SSE)
- Edge (duplicate FIRS submissions)
- HeartBeat (preview/final state mismatch)
- Customer and inventory aggregates (double-counted)

This document defines three tiers of integrity safeguards, ordered by urgency.

---

## TIER A: IMPLEMENT NOW

### A1. Idempotency Keys on Invoice Insert

**Problem:** If the finalize HTTP request times out and the client retries, `RecordCreator` will attempt to re-insert the same invoices. Currently, the `INVOICE_INSERT` has no `ON CONFLICT` guard — the retry will either:
- Fail with a duplicate PK error (if `invoice_id` is the same) — **noisy but safe**
- Succeed with a different `invoice_id` (if SDK regenerates it) — **silent data duplication**

**Fix:** Add an idempotency key to the finalize request. The key is a hash of `(batch_id, company_id, version_number)` — same finalize request always produces the same key.

```sql
-- Add idempotency tracking table
CREATE TABLE IF NOT EXISTS finalize_idempotency (
    idempotency_key  TEXT PRIMARY KEY NOT NULL,
    batch_id         TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    result_json      TEXT,           -- cached FinalizeResult for replay
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       TEXT NOT NULL   -- TTL: 24 hours after creation
);

CREATE INDEX idx_finalize_idemp_expires ON finalize_idempotency(expires_at);
```

**Flow:**
1. Before running the pipeline, check `finalize_idempotency` for the key
2. If found and not expired: return the cached `result_json` (replay, no DB writes)
3. If not found: proceed with pipeline, then INSERT the key + result on success
4. Garbage collect expired keys on a schedule (or lazily on read)

**Implementation location:** `pipeline.py` — wrap `finalize()` with idempotency check.

### A2. ON CONFLICT Guard on Invoice Insert

**Problem:** Even with idempotency keys, belt-and-suspenders: the `INVOICE_INSERT` should not fail catastrophically on duplicate `invoice_id`.

**Fix:** Change the invoice INSERT to:

```sql
INSERT INTO invoices (...) VALUES (...)
ON CONFLICT (invoice_id) DO NOTHING
```

If the invoice already exists, skip silently. The idempotency layer handles the response. Same for `invoice_line_items`:

```sql
INSERT INTO invoice_line_items (...) VALUES (...)
ON CONFLICT (line_item_id) DO NOTHING
```

**Implementation location:** `record_creator.py` — modify `INVOICE_INSERT` and `LINE_ITEM_INSERT`.

### A3. Finalize Audit Log

**Problem:** When things go wrong, we have no record of what was attempted vs what succeeded. The only evidence is application logs (ephemeral) and the DB state (post-fact).

**Fix:** Add a lightweight audit table that records every finalize attempt:

```sql
CREATE TABLE IF NOT EXISTS finalize_audit_log (
    id               SERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    company_id       TEXT NOT NULL,
    idempotency_key  TEXT,
    action           TEXT NOT NULL,    -- 'VALIDATE', 'IRN_GENERATE', 'COMMIT', 'EDGE_SUBMIT'
    status           TEXT NOT NULL,    -- 'STARTED', 'SUCCEEDED', 'FAILED'
    detail           TEXT,             -- Error message or summary
    invoice_count    INTEGER,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_finalize_audit_batch ON finalize_audit_log(batch_id);
CREATE INDEX idx_finalize_audit_created ON finalize_audit_log(created_at);
```

**Flow:** Pipeline logs an entry at each stage:
1. `VALIDATE / STARTED`
2. `VALIDATE / SUCCEEDED` (or FAILED with violation details)
3. `IRN_GENERATE / SUCCEEDED`
4. `COMMIT / STARTED`
5. `COMMIT / SUCCEEDED` (with invoice_count)
6. `EDGE_SUBMIT / SUCCEEDED` (or FAILED with Edge error)

**Implementation location:** New `audit_logger.py` in `src/finalize/`, called from `pipeline.py`.

### A4. Transaction Isolation Level

**Problem:** Default PostgreSQL isolation is `READ COMMITTED`. Concurrent finalize requests for different batches are fine, but the same batch finalized concurrently (race condition from retry) could interleave.

**Fix:** The idempotency key (A1) is the primary guard. As a secondary measure, the `commit_batch` transaction should use `SERIALIZABLE` isolation for the idempotency check + insert:

```python
async with conn.transaction(isolation_level="serializable"):
    # Check idempotency key
    # If not found, proceed with writes
    # Insert idempotency key at the end
```

This ensures two concurrent retries of the same batch are serialized — one wins, the other replays.

**Implementation location:** `record_creator.py` or `pipeline.py` transaction wrapper.

---

## TIER B: FUTURE DEBT (Phase 2)

### B1. Reconciliation Endpoint

**What:** An API endpoint that compares DB records against HeartBeat preview data for a given batch:

```
POST /api/v1/finalize/{batch_id}/reconcile
```

**How it works:**
1. Fetch preview .hlx rows from HeartBeat
2. Fetch committed invoices from DB for that batch_id
3. Diff field-by-field: are committed values consistent with preview + accepted edits?
4. Return a reconciliation report:
   - `CONSISTENT` — all good
   - `DRIFT_DETECTED` — specific fields differ unexpectedly
   - `MISSING_RECORDS` — preview had N invoices, DB has fewer
   - `EXTRA_RECORDS` — DB has invoices not in preview (duplication)

**When to run:** Manually, or on a schedule for recently finalized batches.

### B2. Batch-Level Checksums

**What:** After committing a batch, compute a checksum (SHA-256) over the committed data and store it alongside the batch record.

```sql
ALTER TABLE core_queue ADD COLUMN commit_checksum TEXT;
```

The checksum is computed from:
- All invoice rows (sorted by invoice_id, serialized to JSON)
- All line item rows
- Customer upsert results
- Inventory upsert results

**Purpose:** The reconciliation endpoint (B1) can compare the stored checksum against a freshly computed one. If they differ, something changed post-commit (manual DB edit, migration bug, etc.).

### B3. Edge Dispatch Tracking

**What:** Track which invoices were actually dispatched to Edge vs which failed:

```sql
CREATE TABLE IF NOT EXISTS edge_dispatch_log (
    id               SERIAL PRIMARY KEY,
    batch_id         TEXT NOT NULL,
    invoice_id       TEXT NOT NULL,
    edge_status      TEXT NOT NULL,    -- 'QUEUED', 'SUBMITTED', 'ACCEPTED', 'REJECTED'
    edge_response    TEXT,             -- Raw Edge response
    dispatched_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, invoice_id)
);
```

**Problem this solves:** Currently, if Edge accepts 48 of 50 invoices, we log a warning but don't track which 2 failed. Retry logic needs to know exactly which invoices to re-submit.

### B4. Real-Time Veracity APIs

**What:** APIs that validate a single corrected invoice in real-time during the preview/review phase:

```
POST /api/v1/veracity/check
Body: { single invoice row }
Response: { valid: true/false, errors: [...] }
```

**Purpose:** Enables the failed-invoice-to-submissions auto-upgrade described in HLX_FORMAT.md Section 12.3. When a user corrects a failed invoice on the ReviewPage, Float calls this API. If the invoice now passes, Float visually moves it to the submissions sheet.

**Dependency:** Requires lightweight validation logic extracted from the full pipeline — just schema + business rule checks, no Transforma, no DB writes.

---

## TIER C: FUTURE DEBT (Long-term)

### C1. Event Sourcing

**What:** Replace direct `INSERT`/`UPDATE` with an append-only event log. Current state is derived by replaying events.

```
Event: InvoiceFinalizedEvent(invoice_id, batch_id, data={...}, timestamp)
Event: CustomerUpsertedEvent(customer_id, fields_changed={...}, timestamp)
Event: InventoryUpdatedEvent(product_id, fields_changed={...}, timestamp)
```

**Advantages:**
- Perfect audit trail — every state change is recorded
- Time-travel queries (what was the state at time T?)
- Easy replay/recovery — rebuild tables from events
- Natural fit for SSE (events ARE the SSE payload)

**Disadvantages:**
- Significant architectural change
- Read path becomes more complex (materialized views or CQRS)
- Storage grows unboundedly (compaction needed)

**When:** Only if Helium scales to multi-tenant high-volume where audit compliance requires provable state history.

### C2. Automated Rollback Procedures

**What:** Given a batch_id, automatically undo all DB changes made by that batch:

```
POST /api/v1/finalize/{batch_id}/rollback
```

**How:**
1. Delete all invoices where `batch_id = X`
2. Delete all line items for those invoices
3. Decrement customer aggregates
4. Decrement inventory aggregates (total_times_invoiced, total_revenue, etc.)
5. Mark Edge submissions as cancelled
6. Generate a new .hlx version reflecting the rollback

**Risk:** Aggregate reversal is error-prone (what if other batches also affected those aggregates between commit and rollback?). Event sourcing (C1) makes this trivial — just exclude the batch's events from replay.

**When:** Only needed if batches must be fully reversible after finalization (regulatory requirement or client demand).

### C3. Cross-Database Consistency Checks

**What:** Helium uses separate databases for invoices, customers, and inventory. Cross-DB foreign keys are not enforced by PostgreSQL. A periodic job should verify referential integrity:

- Every `invoice_line_items.product_id` references a valid `inventory.product_id`
- Every invoice's counterparty TIN exists in the `customers` table
- Every `inventory.top_customer` references a valid customer

**When:** When multi-database architecture is formalized and cross-DB drift becomes a real risk.

---

## Implementation Priority

| Item | Effort | Risk Mitigated | Priority |
|------|--------|----------------|----------|
| A1. Idempotency keys | 2-3 hours | Duplicate invoices from retry | **NOW** |
| A2. ON CONFLICT guards | 30 min | Crash on duplicate insert | **NOW** |
| A3. Audit log | 2-3 hours | No visibility into failures | **NOW** |
| A4. Transaction isolation | 30 min | Race condition on concurrent retry | **NOW** |
| B1. Reconciliation endpoint | 1-2 days | Undetected data drift | Phase 2 |
| B2. Batch checksums | 2-3 hours | Silent post-commit corruption | Phase 2 |
| B3. Edge dispatch tracking | 3-4 hours | Can't retry specific failures | Phase 2 |
| B4. Veracity APIs | 2-3 days | Failed invoice auto-upgrade | Phase 2 |
| C1. Event sourcing | 2-3 weeks | Full state history + replay | Long-term |
| C2. Automated rollback | 1 week | Batch reversal | Long-term |
| C3. Cross-DB checks | 2-3 days | Referential integrity across DBs | Long-term |

---

## Reference Documents

- **`record_creator.py`** — Current DB write implementation
- **`pipeline.py`** — Orchestrator where idempotency + audit hooks go
- **`HLX_FORMAT.md`** Section 12.3 — Failed invoice auto-upgrade (requires B4)
- **`HLX_FORMAT.md`** Section 6 — .hlx versioning (rollback creates new version)
