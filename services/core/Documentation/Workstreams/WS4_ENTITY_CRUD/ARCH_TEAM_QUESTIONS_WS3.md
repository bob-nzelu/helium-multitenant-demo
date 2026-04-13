# WS3 ORCHESTRATOR — Architecture Team Questions

**Date:** 2026-03-23
**From:** WS3 Implementation Team (Sonnet session, Opus context)
**To:** Bob (Architect)

---

## Q1: Preview output — 6 separate blob files vs single .hlx archive?

**CONFLICT:** API_CONTRACTS.md (Section 1.3) defines `preview_data` with 6 separate blob URLs:
```json
{
    "firs_invoices_url": "/api/blob/.../firs_invoices.json",
    "report_url": "/api/blob/.../report.json",
    "customers_url": "/api/blob/.../customers.json",
    "inventory_url": "/api/blob/.../inventory.json",
    "failed_invoices_url": "/api/blob/.../failed_invoices.xlsx",
    "fixed_pdf_url": "/api/blob/.../fixed.pdf"
}
```

But HLX_FORMAT.md (dated 2026-03-21, newer), MENTAL_MODEL.md §6.1, and the actual `pack_hlx()` function in `helium_formats` all describe a **single .hlx tar.gz archive** containing .hlm sheets + report.json + metadata.json + manifest.json.

OPTIONS:
A) 6 separate blob files as per API_CONTRACTS.md
B) Single .hlx archive as per HLX_FORMAT.md

**MY RECOMMENDATION:** Option B — single .hlx archive.

**WHY:**
- HLX_FORMAT.md is dated 2026-03-21 (newer than API_CONTRACTS 2026-03-18). It explicitly says "Core packages .hlm → .hlx for delivery."
- `pack_hlx()` already exists in `helium_formats.hlx.packer` and produces the exact output structure.
- The HANDOFF_NOTE §TWO-STAGE HLX GENERATION (added 2026-03-21) confirms: blob key is `blob/{company_id}/{data_uuid}.hlx` — one file.
- Float SDK is specced to download and unpack one .hlx, not 6 separate files.
- API_CONTRACTS predates the .hlx spec and should be updated to reflect a single `hlx_blob_uuid` in the response.

**PROPOSED RESPONSE SHAPE (200):**
```json
{
    "queue_id": "...",
    "data_uuid": "...",
    "status": "preview_ready",
    "statistics": { ... },
    "red_flags": [ ... ],
    "hlx_blob_uuid": "01JQ..."
}
```

---

## Q2: Failed invoice sheets — unified `failed.hlm` or three stream-specific sheets?

**CONFLICT:** MENTAL_MODEL.md §6.1 shows three separate failed sheets:
```
till_failed.hlm    — Till stream failures
b2b_failed.hlm    — B2B stream failures
rebate_failed.hlm — Rebate stream failures
```

But HLX_FORMAT.md §2 (PHYSICAL FORMAT) shows a single unified sheet:
```
failed.hlm — All failed invoices (unified, with __STREAM__ column)
```

And HANDOFF_NOTE line 128 says: `failed — any error-severity red flag (unified, with __STREAM__ column)`.

OPTIONS:
A) Three separate sheets (till_failed, b2b_failed, rebate_failed)
B) One unified `failed.hlm` with `__STREAM__` column

**MY RECOMMENDATION:** Option B — unified `failed.hlm` with `__STREAM__` column.

**WHY:**
- HLX_FORMAT.md §2 is the canonical physical format spec and it shows one `failed.hlm`.
- The `__STREAM__` column preserves stream identity without fragmenting sheets.
- Float's ReviewPage renders tabs from the manifest — one "Failed Invoices" tab is cleaner UX than three separate failure tabs.
- HANDOFF_NOTE explicitly says "unified, with `__STREAM__` column".
- MENTAL_MODEL §6.1 appears to be an earlier draft that was superseded.

---

## Q3: HeartBeatBlobClient has no upload method

**ISSUE:** The existing `HeartBeatBlobClient` (WS1) only has `fetch_blob()`. There is no `upload()` or `store_blob()` method. WS3 needs to **upload** the .hlx archive to HeartBeat blob store.

OPTIONS:
A) Add `upload_blob(filename, data, content_type) -> str` to the existing `HeartBeatBlobClient`
B) Create a separate `BlobUploader` class
C) Add a generic `post()` method to `HeartBeatBlobClient` and let WS3 construct the upload request

**MY RECOMMENDATION:** Option A — add `upload_blob()` to `HeartBeatBlobClient`.

**WHY:**
- HeartBeatBlobClient is already the blob store abstraction. Adding upload is the natural extension.
- The DEPENDENCIES.md specifies `BlobClient.upload(filename, data, content_type) -> str` as the WS3 interface.
- One class for both directions keeps the API surface minimal.
- The upload endpoint is likely `POST /api/blobs/write` based on HLX_FORMAT.md §10 ("POST /api/blob/write").

**PROPOSED SIGNATURE:**
```python
async def upload_blob(
    self,
    filename: str,
    data: bytes,
    content_type: str = "application/x-helium-exchange",
    company_id: str | None = None,
) -> str:
    """Upload file bytes to HeartBeat blob store. Returns blob_uuid."""
```

---

## Q4: Queue status values — UPPERCASE code vs lowercase spec

**CONFLICT:** The existing `QueueRepository` uses uppercase statuses:
- `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`

The MENTAL_MODEL §10.1 uses lowercase with new values:
- `queued`, `processing`, `preview_ready`, `finalized`, `failed`, `timeout_continuing`, `expired`, `cancelled`

The statuses also differ semantically — the code has `COMPLETED` but the spec has `preview_ready` and `finalized` as separate post-processing states.

OPTIONS:
A) Keep UPPERCASE, add new statuses: `PREVIEW_READY`, `FINALIZED`, `EXPIRED`, `CANCELLED`
B) Switch to lowercase per MENTAL_MODEL
C) Keep code as-is, use `COMPLETED` for `preview_ready`

**MY RECOMMENDATION:** Option A — keep UPPERCASE (matches existing code), add the new statuses.

**WHY:**
- Existing code consistently uses UPPERCASE. Breaking that convention would require updating WS0/WS1/WS2 code.
- The new statuses (`PREVIEW_READY`, `FINALIZED`, etc.) are real state transitions that `COMPLETED` doesn't capture.
- WS3 needs to distinguish between "preview generated, awaiting user review" (`PREVIEW_READY`) and "submitted to FIRS" (`FINALIZED`).
- I will NOT rename existing `PENDING`/`PROCESSING`/`FAILED` — only add new statuses.

**PROPOSED STATUS FLOW:**
```
PENDING → PROCESSING → PREVIEW_READY → (WS5) → FINALIZED
                    ↘ FAILED
                    ↘ CANCELLED
    PREVIEW_READY → (after 7 days) → EXPIRED
```

---

## Q5: WS3 integration with existing `process_entry()` function

**ISSUE:** WS1's `process_entry()` function (in `src/ingestion/router.py` and `queue_scanner.py`) already runs the full pipeline inline: fetch → detect → dedup → parse → transform → enrich → resolve → update queue. This is the same pipeline that WS3's `PipelineOrchestrator` is supposed to manage.

OPTIONS:
A) WS3 replaces `process_entry()` entirely — the `/enqueue` endpoint and QueueScanner both call `PipelineOrchestrator.process()` instead
B) WS3 wraps `process_entry()` — adding timeout management, batching, and preview generation around it
C) Keep `process_entry()` for the simple case, WS3 adds a separate `process_preview` path

**MY RECOMMENDATION:** Option A — WS3's `PipelineOrchestrator` replaces `process_entry()`.

**WHY:**
- `process_entry()` was built as a temporary inline pipeline for WS1/WS2 testing. WS3 is the proper orchestrator.
- The HANDOFF_NOTE says: "POST /api/v1/process_preview" is a separate endpoint called by Relay AFTER `/enqueue`.
- The flow is: Relay calls `/enqueue` (creates queue entry) → Relay calls `/process_preview` (WS3 runs the pipeline).
- `process_entry()` can remain as-is for the QueueScanner safety-net path (fire-and-forget for PENDING entries that weren't picked up). WS3's `process_preview` is the primary path.
- BUT: `/enqueue` currently fire-and-forgets `process_entry()`. With WS3, `/enqueue` should ONLY create the queue entry. Relay then separately calls `/process_preview`.

**QUESTION:** Should I modify `/enqueue` to remove the fire-and-forget `process_entry()` call? Or leave it as a belt-and-suspenders safety net alongside the QueueScanner?

---

## Q6: HLX encryption — is `cryptography` package already a dependency?

**ISSUE:** HLX_FORMAT.md §HLX ENCRYPTION specifies AES-256-GCM encryption with HKDF key derivation. This requires the `cryptography` Python package.

**CHECKED:** Core's `requirements.txt` / `pyproject.toml` — I don't see `cryptography` listed.

OPTIONS:
A) Add `cryptography` as a dependency and implement encryption in WS3
B) Defer encryption to a later session (generate unencrypted .hlx for now)
C) Put encryption in `helium_formats` package instead of WS3

**MY RECOMMENDATION:** Option A — add `cryptography` and implement in WS3.

**WHY:**
- The HLX_FORMAT.md encryption spec is clear and simple (AES-256-GCM, HKDF from company_id).
- `cryptography` is a standard, well-maintained package. It's lightweight and likely needed elsewhere (HeartBeat JWT, etc.).
- The encryption is a simple 20-line function — not worth deferring.
- I would implement it as a utility in `src/orchestrator/crypto.py` (or in `helium_formats.hlx.encryption`) that WS3 calls after `pack_hlx()`.

**QUESTION:** Should the encrypt/decrypt functions live in `helium_formats` (shared library, Float SDK also needs decrypt) or in Core's `src/orchestrator/`?

---

## Q7: WS2 interface signatures — code vs spec mismatch

**OBSERVATION (not blocking):** The actual WS2 code uses `PipelineContext` as a parameter:
```python
# Actual code (transformer.py, enricher.py, resolver.py):
async def transform(self, parse_result: ParseResult, context: PipelineContext) -> TransformResult
async def enrich(self, transform_result: TransformResult, context: PipelineContext) -> EnrichResult
async def resolve(self, enrich_result: EnrichResult, context: PipelineContext) -> ResolveResult
```

But the DEPENDENCIES.md and API_CONTRACTS.md show simpler signatures:
```python
# Spec:
async def transform(self, parse_result: ParseResult, company_id: str) -> TransformResult
async def enrich(self, transform_result: TransformResult) -> EnrichResult
async def resolve(self, enrich_result: EnrichResult) -> ResolveResult
```

**MY RECOMMENDATION:** Use the **actual code signatures** (with `PipelineContext`). The spec is aspirational; the code is what runs. I'll construct a `PipelineContext` from the queue entry data and pass it through.

**NOT A QUESTION — just flagging the discrepancy so you're aware.**

---

## Q8: `data_uuid` column in `core_queue` table

**ISSUE:** The queue_repository INSERT statement does NOT include `data_uuid`:
```python
INSERT INTO core_queue (
    queue_id, blob_uuid, original_filename,
    immediate_processing, batch_id,
    company_id, uploaded_by,
    status, priority
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
```

But WS3 needs `data_uuid` (the Relay per-request group ID) to:
1. Emit SSE events keyed by `data_uuid`
2. Name the .hlx file (`{data_uuid}.hlx`)
3. Return it in the process_preview response

The `EnqueueRequest` model in WS1's router DOES include `data_uuid`, but it's not being persisted.

OPTIONS:
A) Add `data_uuid` column to `core_queue` DDL and update the repository INSERT
B) Use `queue_id` as the de-facto identifier everywhere (ignore `data_uuid`)

**MY RECOMMENDATION:** Option A — add `data_uuid` to `core_queue`.

**WHY:**
- `data_uuid` is a Relay-assigned, cross-service identifier. It's the correlation key for SSE events, blob naming, and the SDK's local tracking.
- `queue_id` is Core-internal. The SDK doesn't know it until Core responds.
- The canonical identity model (from MEMORY) says `data_uuid` = per-request group, always present.
- I'll add the column and update the `enqueue()` method to persist it.

---

## Q9: Batch processing scope — per-phase or full-pipeline per batch?

**CLARIFICATION:** The MENTAL_MODEL §5.2 describes batching as:
```
Phase 3: Process all 16 batches through TRANSFORM → collect
Phase 4: Process all 16 batches through ENRICH → collect
Phase 5: Process all 16 batches through RESOLVE → collect
```

This means: split invoices into batches, run ALL batches through Phase 3, collect ALL results, THEN run ALL batches through Phase 4, etc. Phases are barriers.

But the HANDOFF_NOTE §BATCH PROCESSING and SSE section says `invoices_ready` increments when a batch exits Phase 7 (BRANCH). This implies each batch runs through ALL phases independently (pipeline parallelism, not batch parallelism).

OPTIONS:
A) **Phase-barrier:** All batches through Phase 3 → barrier → all through Phase 4 → barrier → etc.
B) **Pipeline-parallel:** Each batch runs through Phases 3-7 independently. Counter updates as each batch finishes Phase 7.

**MY RECOMMENDATION:** Option A — Phase-barrier, but emit progress per-batch-per-phase.

**WHY:**
- MENTAL_MODEL §5.2 is explicit: "Phases themselves are sequential — Phase 4 cannot start until Phase 3 completes for ALL batches."
- Phase-barrier is simpler to implement and reason about (no race conditions on shared resources like HIS rate limits).
- For SSE progress: `invoices_ready` increments when ALL phases complete for a batch. Since phases are barriers, this happens at the end of Phase 7 for all batches at once (or we track per-batch completion within Phase 7).

**PROPOSED APPROACH:** Run Phases 3-5 with barriers (all batches per phase). Phase 7 (BRANCH) runs per-batch and increments `invoices_ready` as each batch is branched. This matches both specs.

---

## Q10: Observability/PDF overlay — WS6 dependency status

**ISSUE:** WS3 DELIVERABLES reference `fixed.pdf` generation via WS6's `overlay_irn_qr()`. The HANDOFF_NOTE lists WS6 as a dependency.

**QUESTION:** Is WS6 (Observability) implemented? If not, should I stub the `overlay_irn_qr()` call?

**MY RECOMMENDATION:** Stub it. Return `None` for `fixed_pdf_url` and log a warning. The preview works fine without the PDF overlay — it's optional (API_CONTRACTS says `fixed_pdf_url` can be `null`).

---

## Summary

| # | Topic | Blocking? | Recommendation |
|---|-------|-----------|----------------|
| Q1 | Single .hlx vs 6 blob files | **YES** | Single .hlx (HLX_FORMAT.md is newer) |
| Q2 | Unified vs split failed sheets | No | Unified `failed.hlm` with `__STREAM__` |
| Q3 | BlobClient upload method | **YES** | Add `upload_blob()` to HeartBeatBlobClient |
| Q4 | Queue status casing | **YES** | Keep UPPERCASE, add PREVIEW_READY/FINALIZED |
| Q5 | process_entry integration | **YES** | WS3 replaces it for the /process_preview path |
| Q6 | Encryption dependency | No | Add `cryptography`, implement in WS3 |
| Q7 | WS2 signatures | No | Use actual code signatures (PipelineContext) |
| Q8 | data_uuid in core_queue | **YES** | Add column to DDL + repository |
| Q9 | Batch processing scope | **YES** | Phase-barrier with per-batch progress at Phase 7 |
| Q10 | WS6 PDF overlay status | No | Stub it |
