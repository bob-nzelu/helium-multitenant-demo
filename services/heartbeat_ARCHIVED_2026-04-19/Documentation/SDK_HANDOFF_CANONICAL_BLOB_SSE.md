# SDK Team Handoff — SSE Events + Config Integration

**Date**: 2026-03-30 (updated 2026-03-31)
**Context**: HeartBeat canonical blob schema audit + tenant config system
**Affects**: Float SDK sync layer, event processor, Queue tab display, ConfigService

---

## What Changed in HeartBeat

HeartBeat's blob SSE events (`blob.uploaded`, `blob.status_changed`) now include
the full canonical dual identity. Previously they only included `blob_uuid`.

### New SSE Event Shapes

**`blob.uploaded`** (emitted when blob is registered in file_entries):
```json
{
    "blob_uuid": "abc-123",
    "file_display_id": "file_a1b2c3d4e5f6",
    "batch_display_id": "batch_x1y2z3a4b5c6",
    "file_hash": "sha256-hex-64-chars",
    "original_filename": "invoice.pdf",
    "file_size_bytes": 2048576,
    "status": "uploaded",
    "source": "relay-bulk-1"
}
```

**`blob.status_changed`** (emitted when Core updates status):
```json
{
    "blob_uuid": "abc-123",
    "file_display_id": "file_a1b2c3d4e5f6",
    "batch_display_id": "batch_x1y2z3a4b5c6",
    "status": "processing",
    "processing_stage": "extraction",
    "extracted_invoice_count": 5,
    "rejected_invoice_count": 1,
    "submitted_invoice_count": 4,
    "duplicate_count": 0,
    "error_message": null
}
```

---

## Required SDK Changes

### 1. Update Sync Models (`src/sdk/sync/models.py`)

Add the new fields to `BlobUploadedEvent` and `BlobStatusChangedEvent`:

```python
class BlobUploadedEvent(BaseModel):
    blob_uuid: str
    file_display_id: Optional[str] = None    # NEW
    batch_display_id: Optional[str] = None   # NEW
    file_hash: Optional[str] = None
    original_filename: Optional[str] = None  # NEW
    file_size_bytes: Optional[int] = None    # NEW
    status: str = "uploaded"
    source: Optional[str] = None             # NEW

class BlobStatusChangedEvent(BaseModel):
    blob_uuid: str
    file_display_id: Optional[str] = None    # NEW
    batch_display_id: Optional[str] = None   # NEW
    status: str
    processing_stage: Optional[str] = None
    # Processing statistics (NEW — Core-populated)
    extracted_invoice_count: Optional[int] = None
    rejected_invoice_count: Optional[int] = None
    submitted_invoice_count: Optional[int] = None
    duplicate_count: Optional[int] = None
    error_message: Optional[str] = None      # NEW
```

### 2. Update Event Processor (`src/sdk/sync/event_processor.py`)

The event processor currently correlates SSE events to local sync.db records.
With `file_display_id` now in the event, correlation becomes trivial:

**Before** (fragile — correlates by file_hash or filename):
```python
# Old approach: scan file_entries for matching file_hash
row = conn.execute(
    "SELECT file_display_id FROM file_entries WHERE file_hash = ?",
    (event.file_hash,)
).fetchone()
```

**After** (direct PK match):
```python
# New approach: use file_display_id directly from SSE event
if event.file_display_id:
    conn.execute("""
        UPDATE file_entries
        SET blob_uuid = ?, pending_sync = 0, status = ?,
            updated_at = ?
        WHERE file_display_id = ?
    """, (event.blob_uuid, event.status, now_iso, event.file_display_id))
```

For `blob.status_changed`, also update processing stats:
```python
if event.file_display_id:
    conn.execute("""
        UPDATE file_entries
        SET status = ?, processing_stage = ?,
            extracted_invoice_count = ?,
            rejected_invoice_count = ?,
            submitted_invoice_count = ?,
            duplicate_count = ?,
            error_message = ?,
            updated_at = ?
        WHERE file_display_id = ?
    """, (
        event.status, event.processing_stage,
        event.extracted_invoice_count,
        event.rejected_invoice_count,
        event.submitted_invoice_count,
        event.duplicate_count,
        event.error_message,
        now_iso, event.file_display_id,
    ))
```

### 3. Update Queue Tab Display (`src/swdb/data_service.py`)

The `_file_to_row()` method can now display processing stats from file_entries:

```python
def _file_to_row(self, file_row: dict) -> dict:
    # ... existing fields ...
    row["valid_count"] = _format_valid_count(
        file_row.get("extracted_invoice_count"),
        file_row.get("rejected_invoice_count"),
    )
    # ... etc
```

### 4. Update column_config.py (Optional)

If you want to display processing stats in the Queue tab, add columns:

```python
ColumnDef("invoices", "Invoices", 80, area="B", sortable=True),
ColumnDef("rejected", "Rejected", 70, area="B", sortable=True),
```

---

## What SDK Already Changed (2026-03-29)

The following change was made to `upload_manager.py` during the audit session.
**Verify this is working correctly:**

- `build_upload_metadata()` now includes `batch_display_id`, `file_display_ids[]`,
  `queue_mode`, `connection_type` in the metadata dict sent to Relay.
- `_start_upload_worker()` reads these from sync.db before spawning the worker.

---

---

## Config SSE Events (Added 2026-03-31)

HeartBeat now broadcasts config change events on the SSE stream. SDK should
listen for these and re-fetch the relevant config section.

### Events to Handle

| SSE Event | Trigger | SDK Action |
|-----------|---------|------------|
| `config.updated` | Tenant details, bank accounts, branding changed | Re-fetch `GET /api/v1/config/{float_id}`, update sync.db tables |
| `behaviour.updated` | App behaviour settings changed | Re-fetch, overwrite `behaviour.json` |
| `schema.updated` | New sync.db schema published | Re-fetch, run migration if `sync_db_version` > local |
| `user.updated` | User role/permissions changed | Re-fetch, update `float_user` table |
| `bank_accounts.updated` | Bank account added/removed/changed | Re-fetch, update `tenant_bank_accounts` table |

### Event Payload Shape (all config events)

```json
{
    "event": "config.updated",
    "data": {
        "changed": ["tenant_details", "bank_accounts"],
        "timestamp": "2026-03-31T01:00:00Z",
        "source": "admin"
    }
}
```

The `changed` array tells SDK which sections changed. SDK can selectively
update only the affected sync.db tables rather than re-fetching everything.

### Config Fetch Endpoint

```
GET /api/v1/config/{float_id}
Authorization: Bearer <JWT>
```

Returns full tenant config (tenant, branding, user, bank_accounts,
service_endpoints, registrations, behaviour, schema). See
`Documentation/Schema/config/float_config_response_schema.json` for the
complete JSON Schema and `sample_abbey_mortgage_config.json` for a
concrete example with Abbey Mortgage data.

### New sync.db Tables Required

Per TENANT_CONFIG_HANDOFF_SPEC.md Section 3, SDK needs these tables:
- `tenant_config` (1 row per tenant)
- `float_instance` (1 row)
- `float_user` (1 row currently)
- `tenant_bank_accounts` (N rows)
- `tenant_service_endpoints` (N rows)
- `tenant_registrations` (N rows)

Plus `behaviour.json` file and `license.db` (separate database).

---

## Backward Compatibility

All new fields in SSE events are Optional. If an older HeartBeat instance doesn't
include them, the SDK falls back to the existing correlation logic (file_hash match).
No breaking changes for the SDK if these fields are absent.
