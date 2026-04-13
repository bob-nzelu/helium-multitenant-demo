# WS3 ORCHESTRATOR — Arch Team Responses

**Date:** 2026-03-23
**From:** Bob (Architect) + Opus (Architecture Session)
**To:** WS3 Implementation Team

---

## Q1: Single .hlx vs 6 separate blob files — APPROVED: Single .hlx

**Decision: Option B — single .hlx archive.**

Your analysis is correct. API_CONTRACTS.md (2026-03-18) is stale. HLX_FORMAT.md (2026-03-21) supersedes it. The entire WS-HLX team built `pack_hlx()` specifically for this purpose. Float SDK is designed to download ONE .hlx, decrypt, unpack, render.

**Your proposed response shape is approved:**
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

Update API_CONTRACTS.md to reflect this. The 6-URL `preview_data` structure is dead.

---

## Q2: Failed sheets — APPROVED: Unified `failed.hlm`

**Decision: Option B — one unified `failed.hlm` with `__STREAM__` column.**

This was decided in the WS-HLM session. The team specifically consolidated 3 per-stream failed sheets into 1 unified sheet. MENTAL_MODEL's 3-sheet version is an earlier draft — ignore it. Float groups by the `__STREAM__` column within the single "Failed Invoices" tab.

---

## Q3: HeartBeatBlobClient upload — APPROVED: Add `upload_blob()`

**Decision: Option A — add `upload_blob()` to the existing `HeartBeatBlobClient`.**

**HeartBeat already has the upload endpoint.** You do NOT need to add one to HeartBeat:

- `POST /api/blobs/write` — multipart form upload (blob_uuid, filename, file bytes, optional metadata)
- `POST /api/blobs/register` — register metadata in blob.db after upload

**Your proposed signature is approved.** Wire `upload_blob()` to call `POST /api/blobs/write`:

```python
async def upload_blob(
    self,
    blob_uuid: str,
    filename: str,
    data: bytes,
    content_type: str = "application/x-helium-exchange",
    company_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Upload file bytes to HeartBeat blob store via POST /api/blobs/write.
    Returns blob_uuid on success.
    """
```

After `write`, call `POST /api/blobs/register` to record metadata (file_size, file_hash, identity fields). This is a two-step process matching how Relay's HeartBeatClient works.

**Document this addition** so the HeartBeat team knows Core is now calling both the download endpoint (WS1) and the write+register endpoints (WS3).

---

## Q4: Queue status casing — APPROVED: UPPERCASE + new statuses

**Decision: Option A — keep UPPERCASE, add new statuses.**

Don't break existing conventions. Add the new statuses to the CHECK constraint:

```sql
CHECK (status IN (
    'PENDING', 'PROCESSING',
    'PREVIEW_READY',    -- WS3 sets this after .hlx generated
    'FINALIZED',        -- WS5 sets this after FIRS submission
    'FAILED',
    'CANCELLED',
    'EXPIRED'           -- Cleanup job sets this after 7-day retention
))
```

**Ownership note:**
- WS3 sets: `PENDING → PROCESSING → PREVIEW_READY` (or `FAILED`)
- WS5 sets: `PREVIEW_READY → FINALIZED` (or `FAILED`)
- Cleanup job sets: `PREVIEW_READY → EXPIRED` (after 7 days)
- User action: `→ CANCELLED`

---

## Q5: process_entry() integration — APPROVED: WS3 replaces it

**Decision: Option A — WS3's `PipelineOrchestrator` replaces `process_entry()` for the `/process_preview` path.**

**Sub-question answer: YES — remove the fire-and-forget `process_entry()` call from `/enqueue`.**

The flow is now:

```
1. Relay calls POST /api/v1/enqueue → creates queue entry (PENDING). No processing.
2. Relay calls POST /api/v1/process_preview → WS3 PipelineOrchestrator runs full pipeline.
3. QueueScanner (60s safety net) → picks up orphaned PENDING entries → calls PipelineOrchestrator.
```

`/enqueue` becomes a pure record-creation endpoint. WS3 owns all processing.

**Keep `process_entry()` as a private helper called by QueueScanner** for the safety-net path. Or better: have QueueScanner call `PipelineOrchestrator.process()` directly — same entry point as `/process_preview`, just triggered by the scanner instead of Relay.

**HeartBeat reconciliation note:** HeartBeat also runs reconciliation that catches blobs uploaded but never enqueued (Relay→Core call lost). This is a separate, broader safety net. The QueueScanner catches jobs that were enqueued but never processed. They're complementary:

| Failure | QueueScanner catches | HeartBeat reconciliation catches |
|---|---|---|
| Core crashed mid-processing | ✅ (entry stuck PROCESSING) | ✅ (blob stuck in `processing`) |
| `asyncio.create_task` dropped | ✅ (entry stays PENDING) | ✅ (blob stays `uploaded`) |
| Enqueue call never reached Core | ❌ (no core_queue entry) | ✅ (blob exists, no queue entry) |

**Document this for the HeartBeat team** so they can wire their reconciliation to check against `core_queue` entries.

---

## Q6: HLX encryption — APPROVED: Add to `helium_formats`

**Decision: Put encrypt/decrypt in `helium_formats` package. WS3 team adds it.**

`helium_formats` is a real Python package that ships with both Core (server-side) and Float SDK (client-side). It's not just documentation — it's implementation code with 248 tests.

**Why `helium_formats`:** Both Core (encrypt before storing) and Float SDK (decrypt after downloading) need the same crypto logic. Putting it in the shared library means one implementation, no duplication.

**Add to:** `helium_formats/hlx/crypto.py`

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import os

def derive_hlx_key(company_id: str) -> bytes:
    """Derive 256-bit AES key from company_id using HKDF."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"helium-hlx-v1",
        info=b"hlx-encryption",
    ).derive(company_id.encode("utf-8"))

def encrypt_hlx(data: bytes, company_id: str) -> bytes:
    """Encrypt .hlx archive bytes. Returns: [12-byte nonce][ciphertext][16-byte tag]."""
    key = derive_hlx_key(company_id)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext

def decrypt_hlx(data: bytes, company_id: str) -> bytes:
    """Decrypt .hlx archive bytes. Raises InvalidTag if wrong company_id."""
    key = derive_hlx_key(company_id)
    nonce = data[:12]
    ciphertext = data[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)
```

**Add `cryptography>=42.0` to `helium_formats` dependencies.**

**Add tests:** encrypt/decrypt round-trip, wrong company_id raises `InvalidTag`, empty data, large data.

**WS3 usage:**
```python
from helium_formats.hlx.crypto import encrypt_hlx
from helium_formats.hlx.packer import pack_hlx

hlx_bytes = pack_hlx(manifest, report, metadata, sheets)
encrypted = encrypt_hlx(hlx_bytes, context.company_id)
await blob_client.upload_blob(blob_uuid, f"{data_uuid}.hlx", encrypted)
```

---

## Q7: WS2 interface signatures — ACKNOWLEDGED

**Decision: Use actual code signatures (with `PipelineContext`).**

Code is authoritative. Spec predates implementation. No action needed. Good catch flagging it.

---

## Q8: data_uuid in core_queue — APPROVED: Add column + fix INSERT

**Decision: Option A — add `data_uuid` to the INSERT.**

The column already exists in the DDL (WS0 created it). The INSERT in `QueueRepository` is simply missing it. Fix the INSERT to include `data_uuid` from the `EnqueueRequest`.

This is a bug, not a design question. Fix it.

---

## Q9: Batch processing — APPROVED: Phase-barrier

**Decision: Option A — phase-barrier with per-batch progress at Phase 7.**

MENTAL_MODEL §5.2 is explicit: phases are sequential barriers. All batches through Phase 3 → collect → all through Phase 4 → collect → etc.

**Your proposed approach is approved:**
- Phases 3-5: barrier (all batches complete before next phase starts)
- Phase 7 (BRANCH): per-batch, emitting `invoices_ready` as each batch gets branched
- Result: monotonically increasing counter, responsive for Float's progress bar

This is simpler, avoids race conditions on shared resources (HIS rate limits, DB connections), and matches the spec.

---

## Q10: WS6 PDF overlay — APPROVED: Stub it

**Decision: Stub the call. Return `None`. Log a warning.**

WS6 isn't built. The .hlx works perfectly without the PDF overlay.

**Context for what PDF overlay is:** When a customer uploads a scanned PDF invoice, Core extracts the data and generates an IRN + QR code for FIRS compliance. PDF overlay = stamping that IRN/QR visually onto the original PDF (like a digital compliance stamp). Only relevant for PDF-source invoices. Not blocking for .hlx generation.

**Stub implementation:**
```python
async def overlay_irn_qr(self, pdf_bytes: bytes, irn: str, qr_data: str) -> bytes | None:
    """Stub — WS6 not implemented. Returns None."""
    logger.warning("PDF overlay not available (WS6 not implemented). Skipping.")
    return None
```

---

## SUMMARY

| # | Topic | Decision |
|---|-------|----------|
| Q1 | Preview output format | Single .hlx archive. API_CONTRACTS is stale — update it. ✅ |
| Q2 | Failed sheets | Unified `failed.hlm` with `__STREAM__` column ✅ |
| Q3 | Blob upload | Add `upload_blob()` to HeartBeatBlobClient. Endpoint exists (`POST /api/blobs/write`). ✅ |
| Q4 | Queue status casing | UPPERCASE. Add `PREVIEW_READY`, `FINALIZED`, `CANCELLED`, `EXPIRED`. ✅ |
| Q5 | process_entry() | WS3 replaces it. Remove fire-and-forget from `/enqueue`. Scanner calls PipelineOrchestrator directly. ✅ |
| Q6 | Encryption location | `helium_formats/hlx/crypto.py`. WS3 team adds it. Ships with both Core and SDK. ✅ |
| Q7 | WS2 signatures | Use actual code (PipelineContext). Acknowledged. ✅ |
| Q8 | data_uuid in core_queue | Fix the INSERT bug. Column exists, just not being persisted. ✅ |
| Q9 | Batch processing | Phase-barrier. Per-batch progress at Phase 7. ✅ |
| Q10 | PDF overlay | Stub it. WS6 not built. ✅ |

**You are clear to begin implementation. Go full scope.**

**Remember:** Document any HeartBeat interaction points you discover (blob write, blob register, reconciliation) so the HeartBeat team can track what Core depends on.
