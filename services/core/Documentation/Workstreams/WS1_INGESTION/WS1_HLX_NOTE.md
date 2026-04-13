# WS1 INGESTION — HLX/HLM Integration Note

**Date:** 2026-03-24
**From:** Architecture Session (Bob + Opus)
**References:** `HLX_FORMAT.md` v1.1 (Section 15), `HLM_FORMAT.md` v2.0
**Priority:** Must be implemented before WS5 can function end-to-end

---

## WHAT YOU NEED TO KNOW

WS1 currently receives files from Relay, detects their type, parses them, and queues them for processing. A new file type must be handled: **finalized `.hlm` files returning from Float SDK**.

---

## THE NEW FLOW

```
Normal flow (current):
  Relay sends file → WS1 detects type → parse → queue for WS2/WS3 (full pipeline)

New finalized flow:
  Float SDK sends .hlm with finalized flag → WS1 detects .hlm + finalized flag
  → SKIP Transforma (data is already .hlm-shaped)
  → Route directly to WS5 for validation and DB committal
```

---

## WHAT TO IMPLEMENT

### 1. Detect Finalized .hlm

When a `.hlm` file arrives (already handled by `hlm_parser.py`), check for the `finalized` flag in the HLM metadata:

```json
{
    "hlm_version": "2.0",
    "data_type": "invoice",
    "metadata": {
        "source": "finalize_request",
        "finalized": true,
        "hlx_id": "0193f5c0-...",
        "version_number": 1,
        "data_uuid": "0193f5a0-...",
        "queue_id": "0193f5a1-..."
    }
}
```

### 2. Route to WS5

When `metadata.finalized == true`:
- Do NOT run Transforma (the data is already structured)
- Do NOT run WS2 enrichment (already done in the preview pass)
- Queue with status `FINALIZE_READY` instead of `PENDING`
- WS5 picks up entries with `FINALIZE_READY` status

### 3. Queue Status Extension

Add a new queue status value:

| Status | Meaning |
|--------|---------|
| `FINALIZE_READY` | Finalized .hlm received, skip pipeline, route to WS5 |

This sits alongside existing statuses: `PENDING` → `PROCESSING` → `PREVIEW_READY` → `PROCESSED`

### 4. Validation Before Routing

Even for finalized .hlm, WS1 should still validate:
- File is valid JSON / valid .hlm structure
- `hlm_version` is supported
- `data_uuid` and `queue_id` are present
- `company_id` matches the authenticated tenant

Do NOT validate field values — that's WS5's job (diff against preview .hlx).

---

## FILES TO MODIFY

| File | Change |
|------|--------|
| `src/ingestion/parsers/hlm_parser.py` | Check for `metadata.finalized` flag |
| `src/ingestion/router.py` | Route finalized .hlm to `FINALIZE_READY` queue status |
| `schemas/postgres/core_queue.sql` | Add `FINALIZE_READY` to status CHECK constraint |
| `src/ingestion/models.py` | Add `FINALIZE_READY` to `QueueStatus` enum |

---

## REFERENCE DOCUMENTS

- **`HLX_FORMAT.md`** v1.1, Section 13 (Lifecycle → Finalize flow)
- **`HLM_FORMAT.md`** v2.0, Section 2 (Data Flow — finalized .hlm path)
- **`WS5_FINALIZE/MENTAL_MODEL.md`** — WS5 consumes `FINALIZE_READY` entries
