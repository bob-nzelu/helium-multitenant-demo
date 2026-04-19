# HeartBeat Canonical Schema Audit — 2026-03-29

## Overview

Full audit of HeartBeat service against:
- Canonical blob schema v1.4.0 (`Documentation/Schema/blob/`)
- Upgraded Core service (PostgreSQL, 7-phase pipeline)
- SDK/Float sync layer (schema.py v5.2)
- Relay ingestion pipeline

**Result**: 11 files changed in HeartBeat, 1 in SDK, 1 in Relay.
Core changes documented below for Core team handoff.

---

## Changes Made (This Session)

### HeartBeat Service (11 files)

| File | Change |
|---|---|
| `databases/migrations/blob/005_canonical_schema_migration.sql` | **NEW**. Migrates blob.db Phase 1 → canonical v1.4.0. Renames `blob_entries` → `file_entries`, adopts `file_display_id`/`batch_display_id` PKs, adds 20+ missing columns, `blob_downloads`, `blob_schema_version`, 8 category views. |
| `databases/schema.sql` | **REWRITTEN**. Fresh install schema now canonical v1.4.0. 12 tables + 5 views + version tracking. |
| `src/database/connection.py` | All queries use `file_entries`. `register_blob()` accepts dual identity + creates batch + junction. Added `get_file_by_display_id()`, `record_download()`, `_ensure_batch()`, `_create_batch_entry()`. Enhanced `update_blob_status()` with `error_message` + `processing_stats`. |
| `src/handlers/blob_handler.py` | `register_blob()` accepts `file_display_id`, `batch_display_id`, `connection_type`, `queue_mode`. Publishes `blob.uploaded` SSE event with canonical dual identity. |
| `src/handlers/status_handler.py` | 7-status canonical enum. `update_blob_status()` accepts `error_message` + `processing_stats`. Returns `file_display_id`. Publishes `blob.status_changed` SSE event. |
| `src/handlers/reconciliation_handler.py` | All `blob_entries` → `file_entries`. |
| `src/api/internal/blobs.py` | Request/response models include `file_display_id`, `batch_display_id`. Register extracts display IDs from metadata. Download records to `blob_downloads`. |
| `src/api/internal/blob_status.py` | Response includes dual identity + processing stats. Request accepts `error_message` + stats. |
| `src/api/internal/blob_outputs.py` | All `blob_entries` → `file_entries`. |
| `src/api/register.py` | Legacy endpoint updated for `file_entries` table. Health check uses `file_entries`. |
| `src/main.py` | Startup health check uses `file_entries`. |

### SDK/Float (1 file)

| File | Change |
|---|---|
| `Float/App/src/sdk/managers/upload_manager.py` | `build_upload_metadata()` now includes `batch_display_id`, `file_display_ids[]`, `queue_mode`, `connection_type`. Worker reads these from sync.db before upload. |

### Relay (1 file)

| File | Change |
|---|---|
| `Relay/src/services/ingestion.py` | `_register_blob()` enriches metadata with per-file `file_display_id` (from SDK's `file_display_ids` array) and `source_document_id = data_uuid`. |

---

## Required Changes — Core Team

The following changes are needed in Core to complete the integration. HeartBeat is ready to receive these calls.

### 1. Call HeartBeat Status Update at Pipeline Phase Transitions

**Why**: Core updates `core_queue.status` but never calls HeartBeat's `POST /api/v1/heartbeat/blob/{blob_uuid}/status`. This means HeartBeat's `file_entries` stays at "uploaded" forever.

**Where**: `src/orchestrator/pipeline.py`

**What**: After each phase transition, call HeartBeat:

```python
# At each phase boundary in PipelineOrchestrator.process():
await self._heartbeat.update_blob_status(
    blob_uuid=blob_uuid,
    status="processing",  # or "preview_pending", "finalized", "error"
    processing_stage="extraction",  # Phase-specific
)
```

**HeartBeat endpoint** (already updated):
```
POST /api/v1/heartbeat/blob/{blob_uuid}/status
{
    "status": "processing",          # Required
    "processing_stage": "extraction", # Optional
    "error_message": "...",           # Optional (for error status)
    "extracted_invoice_count": 5,     # Optional (Core pipeline stats)
    "rejected_invoice_count": 1,
    "submitted_invoice_count": 4,
    "duplicate_count": 0
}
```

**Phase → Status mapping**:

| Core Phase | HeartBeat file_entries.status | processing_stage |
|---|---|---|
| FETCH (Phase 1) | processing | fetch |
| PARSE (Phase 2) | processing | parse |
| TRANSFORM (Phase 3) | processing | transform |
| ENRICH (Phase 4) | processing | enrich |
| RESOLVE (Phase 5) | processing | resolve |
| PREVIEW (Phase 6-7) | preview_pending | preview |
| FINALIZE (WS5) | finalized | (null) |
| ERROR | error | (last stage) |

**Include stats** on the final status update (preview_pending or finalized):
```json
{
    "status": "preview_pending",
    "extracted_invoice_count": 12,
    "rejected_invoice_count": 2,
    "submitted_invoice_count": 10,
    "duplicate_count": 1
}
```

### 2. Add HeartBeat Status Client Method

**Where**: `src/ingestion/heartbeat_client.py`

**What**: Add `update_blob_status()` method:

```python
async def update_blob_status(
    self,
    blob_uuid: str,
    status: str,
    processing_stage: str = None,
    error_message: str = None,
    processing_stats: dict = None,
) -> dict:
    """Update blob status on HeartBeat."""
    payload = {"status": status}
    if processing_stage:
        payload["processing_stage"] = processing_stage
    if error_message:
        payload["error_message"] = error_message
    if processing_stats:
        payload.update(processing_stats)

    resp = await self._http.post(
        f"/api/v1/heartbeat/blob/{blob_uuid}/status",
        json=payload,
    )
    return resp.json()
```

### 3. Forward Processing Stats in SSE Events

Core's SSE events (`processing.progress`, `processing.complete`) should include the processing stats so SDK can display them in the Queue tab:

```python
await self._sse.publish("processing.complete", {
    "data_uuid": context.data_uuid,
    "blob_uuid": queue_entry.blob_uuid,
    "extracted_invoice_count": result.invoice_count,
    "rejected_invoice_count": result.rejected_count,
    "submitted_invoice_count": result.submitted_count,
    "duplicate_count": result.duplicate_count,
})
```

---

## Required Changes — Relay Team

### 1. Forward SDK Display IDs on write_blob (Step 4)

Currently, Relay's `write_blob()` passes metadata as-is. The SDK now includes `batch_display_id` and `file_display_ids[]` in metadata. Relay should enrich the per-file metadata with the correct `file_display_id` for each file:

**Where**: `src/services/ingestion.py`, Step 4 (write_blobs)

**What**: Before calling `self._heartbeat.write_blob()`, enrich the metadata:

```python
# In the Step 4 loop:
per_file_metadata = dict(metadata) if metadata else {}
file_display_ids = per_file_metadata.pop("file_display_ids", [])
if i < len(file_display_ids):
    per_file_metadata["file_display_id"] = file_display_ids[i]

blob_result = await self._heartbeat.write_blob(
    blob_uuid=blob_uuid,
    filename=filename,
    file_data=file_data,
    metadata=per_file_metadata,
    jwt_token=jwt_token,
)
```

### 2. Include data_uuid in IngestResponse

The SDK needs `data_uuid` to correlate with `batch_display_id`. This is already in `IngestResponse` — verify it maps to `source_document_id` in HeartBeat.

### 3. Map call_type to queue_mode

Relay's `call_type` (bulk/external) should map to the canonical `queue_mode` in metadata:

```python
metadata["queue_mode"] = call_type  # "bulk" or "api"
```

---

## Canonical Identity Flow (After All Changes)

```
SDK                     Relay                   HeartBeat               SDK (SSE)
─────                   ─────                   ─────────               ─────────
stage_files()
├─ batch_display_id ──→ metadata ──────────────→ blob_batches.batch_display_id
├─ file_display_id ───→ metadata ──────────────→ file_entries.file_display_id
│                       ├─ blob_uuid (gen) ────→ file_entries.blob_uuid
│                       ├─ data_uuid (gen) ────→ blob_batches.source_document_id
│                       └─ x_trace_id (gen) ──→ file_entries.x_trace_id
│                                               │
│                                               ├─ SSE: blob.uploaded
│                                               │  {blob_uuid, file_display_id,
│                                               │   batch_display_id, file_hash}
│                                               │                       │
│                                               │                       ├─ correlate by
│                                               │                       │  file_display_id
│                                               │                       └─ set pending_sync=0
│
│                       Core ──────────────────→ HeartBeat
│                       ├─ status: processing ─→ file_entries.status
│                       ├─ status: preview_pending → file_entries.status
│                       │                       │
│                       │                       ├─ SSE: blob.status_changed
│                       │                       │  {blob_uuid, file_display_id,
│                       │                       │   status, processing_stats}
│                       │                       │                       │
│                       │                       │                       └─ update Queue tab
```

---

## Schema Version Summary

| Component | Before | After |
|---|---|---|
| HeartBeat blob.db | Phase 1 (2026-01-31), `blob_entries` | Canonical v1.4.0, `file_entries` |
| SDK sync.db | v5.2, canonical `file_entries` | No change needed (already canonical) |
| Core schemas | PostgreSQL, separate tables | No schema change needed |
| Relay | Stateless | No schema (stateless) |

---

## Additional Changes (2026-03-30)

### Schema Registry Updates

| File | Change |
|---|---|
| `databases/schemas/blob_canonical_v1.sql` | **NEW**. Copied canonical blob schema v1.4.0 from Documentation/Schema/blob/. HeartBeat schema registry now serves blob schema via /api/schemas/blob. |
| `databases/schemas/invoices_canonical_v2.sql` | **UPDATED**. Replaced v2.0 (2026-02-25) with v2.1.3.0 (2026-03-26) from Documentation/Schema/invoice/. |

### Test Fixes

| File | Change |
|---|---|
| `tests/unit/test_reconciliation.py` | Fixed `_insert_blob()` helper: uses `file_entries` table, creates `blob_batches` row first, includes `file_display_id` and `batch_display_id`. |
| `tests/unit/test_coverage_gaps.py` | Fixed table existence assertion: `blob_entries` → `file_entries`. |

### Areas Audited — No Issues Found

| Area | Status |
|---|---|
| Auth contract (login, refresh, introspect, step-up) | Fully implemented, matches AUTH_SERVICE_CONTRACT.md |
| KeepAlive manager (health polling, restart policies) | Matches HEARTBEAT_LIFECYCLE_SPEC.md |
| Readiness endpoint | Uses get_blob_database() correctly |
| Platform services (Transforma config) | Database-driven, access-controlled |
| Config.db / tier limits | Properly structured |
| Hardcoded URLs/ports | Only in docs/examples; production uses config |

### Note on SDK/Relay/Core Changes

Changes were made to SDK (`upload_manager.py`) and Relay (`ingestion.py`) during this session. These are left in place — SDK team has been notified to verify. **Going forward, all SDK/Relay/Core changes will be documented as handoff specs only**, not edited directly.

---

## Testing Checklist

- [ ] HeartBeat starts with fresh blob.db (schema.sql creates canonical tables)
- [ ] HeartBeat starts with existing blob.db (migration 005 runs successfully)
- [ ] Legacy register endpoint (`/api/v1/heartbeat/blob/register`) works with file_entries
- [ ] Internal register endpoint (`/api/blobs/register`) accepts file_display_id in metadata
- [ ] Internal register endpoint creates batch + junction row
- [ ] Blob download records to blob_downloads table
- [ ] Status update accepts processing_stats
- [ ] SSE events include file_display_id + batch_display_id
- [ ] SDK receives blob.uploaded SSE and correlates by file_display_id
- [ ] Category views (vw_blob_batches_operational, etc.) return correct data
- [ ] Reconciliation handler works with file_entries table
