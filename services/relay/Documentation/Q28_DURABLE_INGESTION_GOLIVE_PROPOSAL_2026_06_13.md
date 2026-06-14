# Q28 — Go-Live Durable Ingestion Record (`relay.db` write-first): RELAY Design Proposal

**Seat:** RELAY · **Date:** 2026-06-13 · **For:** ARCH + Bob **vet** — `relay.db` schema is in RELAY's
sensitive scope-class, so this is **propose-then-build** (no unilateral schema merge).
**Ratified scope:** Q28 (round 9 — durable ingestion record in go-live scope); round 10 confirms Relay
durable-ingestion is **path-independent** of the Core fix-vs-rewrite feasibility hold → proceeds.

---

## §0. What Q28 is (the go-live subset)

Q28 ratifies a **durable ingestion record** in go-live scope: every message crossing Relay's ingress gets a
**write-first** durable row in `relay.db` (per-tenant SQLite) so an ingest **survives a crash** and is
**idempotent on replay**. This lands the `relay.db` durable ledger on the **current `/api/ingest` path NOW**
— it is the go-live **precursor** to the full Frontdoor Path A (`FRONTDOOR_ARCHITECTURE.md` §3, PR #19),
**without** the `relay_queue` / AMQP machinery (that stays Frontdoor **Phase 2 / S3**, post-Monday).

## §1. Grounding (`FRONTDOOR_ARCHITECTURE.md`, PR #19)
- `relay.db` = per-tenant local **SQLite**, the durable idempotency/audit ledger. **SQLite is LOCKED** (§2.3 —
  intentional all-PG exception; do NOT migrate to PG).
- Write-first ordering (§3.2 / §3.4): in the full Frontdoor it is "queue-write **then** DB-row." For go-live
  (no `relay_queue` yet) the durable record is the **`relay.db` row written first**, around the existing pipeline.
- Idempotency key: Path A uses the ERP `message_id`. The current Reader/Float path has no ERP `message_id`, so
  the natural key is **`file_sha256` + `tenant_id`** — consistent with the existing dedup (R5/L3 hashing).

## §2. Proposed go-live schema (additive; SQLite; per-tenant volume)

```sql
CREATE TABLE IF NOT EXISTS ingest_ledger (
  idempotency_key TEXT PRIMARY KEY,   -- file_sha256 + ':' + tenant_id  (ERP message_id in Phase 2)
  tenant_id       TEXT NOT NULL,
  trace_id        TEXT,
  blob_uuid       TEXT,               -- HB blob uuid, set once the commit-point write lands
  file_hash       TEXT,
  received_at     TEXT NOT NULL,      -- ISO-8601; WRITE-FIRST (durable record before the pipeline)
  processed_at    TEXT,               -- ISO-8601; set on pipeline success (NULL = in-flight / crashed)
  result          TEXT,               -- 'ingested' | 'finalized' | 'duplicate' | 'failed' | NULL
  call_type       TEXT,               -- 'bulk' | 'external'
  created_by      TEXT                -- caller_source (api_key / user_id)
);
CREATE INDEX IF NOT EXISTS idx_ingest_ledger_received ON ingest_ledger(received_at);
```

**Additive + reversible** (go-live criterion — additive/reversible migrations only): this is a **brand-new**
SQLite file + table — there is **no existing live data to migrate**, no DROP/TRUNCATE on anything. Rollback =
remove the file / drop the table. Forward-compatible: `idempotency_key` generalizes from `file_sha256` to the
ERP `message_id` when Path A (Phase 2) lands.

## §3. Write-first integration into the current `/api/ingest`
1. Compute `idempotency_key` (`file_sha256` + `tenant_id`). **INSERT the ledger row FIRST**
   (`received_at=now`, `processed_at=NULL`, `result=NULL`) — the durable in-flight record.
   - Existing row with `processed_at` NOT NULL → **replay** → return the prior `result` (idempotent; no re-pipeline).
   - Existing row with `processed_at` NULL (in-flight / crashed) → recovery path (see §5.2; default: re-run — the
     HB blob-write commit point is itself idempotent on hash).
2. Run the pipeline unchanged: validate → dedup → **HB blob write = commit point**.
3. On success: `UPDATE` the row → `processed_at=now`, `result`, `blob_uuid`.
4. On failure: leave `processed_at` NULL (recovery sweep retries) or set `result='failed'` per policy.

## §4. Reconciliation with the locked contracts
- **AMQP-first (L15) — unchanged.** L15 ("write `core_queue` AMQP before the Core HTTP trigger") governs the
  **`core_queue` producer (S3, post-Monday)**. Q28's `relay.db` row is a **separate durability layer** (the
  Frontdoor ledger), not the `core_queue` write. For Monday (Relay→Core over HTTP) the `relay.db` row **is** the
  go-live durability; when S3 lands, AMQP-first layers on top. **No conflict.**
- **Commit-point = HB-blob-write — unchanged.** The `relay.db` row is written first as the durable in-flight
  record; the HB blob write remains **the** commit point; the ledger's `processed_at` reflects post-commit
  success. The ledger sits **around** the commit point, it does not replace it.
- **Frontdoor Path A (Phase 2) — forward-compatible.** Q28's go-live ledger is the schema's first realization;
  Phase 2 adds `relay_queue` (queue-write-then-row), the ERP `message_id` key, and AMQP ingress.

## §5. Open questions for ARCH / Bob (vet)
1. **Schema ratification (sensitive):** approve the `ingest_ledger` shape (§2), or amend?
2. **In-flight (`processed_at` NULL) recovery policy:** re-run the pipeline (HB commit is hash-idempotent) vs an
   explicit retry/DLQ. **Proposed default: re-run** (simplest + safe; HB dedups).
3. **Idempotency key:** `file_sha256` + `tenant_id` for the current path (proposed), vs requiring a
   client-supplied `message_id` even for Reader/Float.
4. **Retention sweep:** FRONTDOOR §1 notes a retention sweep; go-live default = keep N days on a cadenced sweep
   (low-pri, follow-up).

## §6. Build plan (post-vet)
Once the schema is ratified: implement `src/storage/relay_db.py` (SQLite WAL + the ledger CRUD), wire the
write-first into `src/services/{ingestion,bulk,external}.py`, the additive migration, and tests — ~1 chip,
submitted as a PR for ARCH diff-vet (**no self-merge** — sensitive schema). Redis (A1) is orthogonal to this
(the ledger is SQLite, not Redis).
