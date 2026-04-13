# RELAY BULK UPLOAD - HEARTBEAT INTEGRATION REQUIREMENTS

**Version**: 1.0
**Date**: 2026-01-31
**Status**: 🔴 ACTION REQUIRED - HeartBeat Team
**Related Phase**: Relay Phase 1B (Complete)
**Implemented By**: Claude Sonnet 4.5

---

## 📋 EXECUTIVE SUMMARY

**What Happened**: Relay Phase 1B (Bulk Upload Service) has been implemented with **full retry logic and graceful degradation** assuming HeartBeat APIs exist.

**What You Need To Do**: HeartBeat team must implement **5 API endpoints** to complete the Relay integration.

**Why This Matters**:
- Relay calls HeartBeat for blob storage, deduplication, and daily usage limits
- Without these endpoints, Relay will gracefully degrade but features will be limited
- All retry logic (exponential backoff, 5 attempts) is already implemented in Relay
- You just need to implement the server-side endpoints

**Timeline**: Required for Relay Phase 1C (Integration & Testing)

---

## 🎯 WHAT RELAY IMPLEMENTED (ON YOUR BEHALF)

Relay Phase 1B implemented these components that integrate with HeartBeat:

### 1. HeartBeatClient with Full Retry Logic
**Location**: `Services/Core/src/services/clients/heartbeat_client.py`

**Features Implemented**:
- ✅ Exponential backoff retry (5 attempts: 1s, 2s, 4s, 8s, 16s)
- ✅ Transient error detection (503, connection timeout → retry)
- ✅ Permanent error detection (400, 404 → fail immediately)
- ✅ Graceful degradation (HeartBeat unavailable → allow upload, log warning)
- ✅ Trace ID propagation for request tracking

**Methods That Call HeartBeat**:
```python
# 1. Write blob to storage
await heartbeat_client.write_blob(
    file_uuid="550e8400-...",
    filename="invoice.pdf",
    data=file_bytes
)
# → Retries 5 times if HeartBeat unavailable
# → Returns blob_path on success

# 2. Register blob metadata
await heartbeat_client.register_blob(
    file_uuid="550e8400-...",
    blob_path="/files_blob/550e8400-...-invoice.pdf",
    file_hash="a1b2c3...",
    company_id="execujet-ng"
)
# → Idempotent (returns 409 if already exists)
# → Retries on network failure

# 3. Check daily usage limit
response = await heartbeat_client.check_daily_usage(
    company_id="execujet-ng",
    file_count=2
)
# → Graceful degradation: If unavailable, allow upload
# → Returns {"status": "allowed"} or {"status": "limit_exceeded"}

# 4. Check for duplicate file
response = await heartbeat_client.check_duplicate(
    file_hash="a1b2c3..."
)
# → Graceful degradation: If unavailable, treat as not duplicate
# → Returns {"is_duplicate": true/false, "queue_id": "..."}

# 5. Record processed file hash
await heartbeat_client.record_duplicate(
    file_hash="a1b2c3...",
    queue_id="queue_123",
    file_uuid="550e8400-..."
)
# → Called by Core after successful processing
```

### 2. Graceful Degradation Strategy

**When HeartBeat is Unavailable**:
- ✅ Blob write fails → Return error to user (file not uploaded)
- ✅ Daily usage check fails → Allow upload (log warning)
- ✅ Duplicate check fails → Treat as not duplicate (log warning)
- ✅ Blob registration fails → Retry 5 times, then trigger reconciliation

**User Impact**:
- Critical operations (blob write) → Block upload if HeartBeat down
- Non-critical operations (dedup, limits) → Allow upload with degraded features

---

## 🔧 REQUIRED HEARTBEAT API ENDPOINTS

You need to implement these **5 endpoints** to complete the integration:

---

### ENDPOINT 1: Write Blob to Storage

**Route**: `POST /api/blob/write`

**Purpose**: Write raw file bytes to blob storage (MinIO or filesystem)

**Request**:
```json
{
    "file_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "filename": "550e8400-...-invoice.pdf",
    "data": "<base64_encoded_bytes>"
}
```

**Response** (200 OK):
```json
{
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "file_uuid": "550e8400-...",
    "created_at": "2026-01-31T10:00:00Z"
}
```

**Response** (500 Internal Server Error):
```json
{
    "error": "BLOB_WRITE_FAILED",
    "message": "Failed to write blob: {reason}"
}
```

**Implementation Notes**:
- Write file to MinIO (Pro/Enterprise) or filesystem (Test/Standard)
- Generate blob_path based on tier configuration
- Return blob_path for Relay to use in Core enqueue call
- **This is a critical operation** - if it fails, upload fails

**Retry Behavior** (Relay already handles):
- 5 attempts with exponential backoff
- Transient errors (503) → retry
- Permanent errors (400) → fail immediately

---

### ENDPOINT 2: Register Blob Metadata

**Route**: `POST /api/blob/register`

**Purpose**: Register blob metadata in `helium_blob.db` for tracking and reconciliation

**Request**:
```json
{
    "file_uuid": "550e8400-...",
    "filename": "invoice.pdf",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "file_size_bytes": 1048576,
    "file_hash": "a1b2c3d4e5f6...",
    "batch_id": "batch_550e8400-...",
    "company_id": "execujet-ng",
    "uploaded_by": "user@example.com"
}
```

**Response** (200 OK - First Registration):
```json
{
    "status": "registered",
    "file_uuid": "550e8400-...",
    "retention_until": "2033-01-31T00:00:00Z"
}
```

**Response** (409 Conflict - Already Exists):
```json
{
    "status": "already_exists",
    "file_uuid": "550e8400-...",
    "message": "Blob already registered (idempotent)"
}
```

**Implementation Notes**:
- Write to `helium_blob.db.blob_entries` table
- Set `retention_until` = `created_at` + 7 years (FIRS compliance)
- **Must be idempotent**: Same request twice → same result (409 if exists, but treat as success)
- This allows Relay to retry on network failure without creating duplicates

**Why Idempotence Matters**:
```
Relay attempts to register blob:
  Attempt 1: Network timeout (did it reach HeartBeat? Unknown)
  Attempt 2: Retry same request

HeartBeat must:
  - Check if file_uuid already exists
  - If yes: Return 409 (already_exists) - Relay treats as success
  - If no: Create new entry, return 200
```

**Retry Behavior** (Relay already handles):
- 5 attempts with exponential backoff
- Both 200 and 409 treated as success

---

### ENDPOINT 3: Check Daily Usage Limit

**Route**: `GET /api/daily_usage/check`

**Purpose**: Check if company has exceeded daily file upload limit

**Request**:
```http
GET /api/daily_usage/check?company_id=execujet-ng&file_count=2
```

**Query Parameters**:
- `company_id`: Company identifier (from config)
- `file_count`: Number of files in current upload request

**Response** (200 OK - Within Limit):
```json
{
    "status": "allowed",
    "company_id": "execujet-ng",
    "current_usage": 350,
    "daily_limit": 500,
    "remaining": 150,
    "resets_at": "2026-02-01T00:00:00Z"
}
```

**Response** (200 OK - Limit Exceeded):
```json
{
    "status": "limit_exceeded",
    "company_id": "execujet-ng",
    "current_usage": 500,
    "daily_limit": 500,
    "remaining": 0,
    "resets_at": "2026-02-01T00:00:00Z"
}
```

**Implementation Notes**:
- Query `daily_usage.db` for company's current usage
- Compare `current_usage + file_count` vs `daily_limit`
- Do NOT increment usage here (that happens after successful upload)
- Daily limit is **company-wide** (not per-user)

**Graceful Degradation** (Relay handles):
- If this endpoint fails → Relay allows upload anyway (logs warning)
- This is a **soft limit** enforcement (best-effort)

---

### ENDPOINT 4: Check for Duplicate File

**Route**: `POST /api/duplicate/check`

**Purpose**: Check if file hash already exists in processed files

**Request**:
```json
{
    "file_hash": "a1b2c3d4e5f6789012345678901234567890abcdef123456789012345678901234"
}
```

**Response** (200 OK - Not Duplicate):
```json
{
    "is_duplicate": false,
    "file_hash": "a1b2c3..."
}
```

**Response** (200 OK - Duplicate Found):
```json
{
    "is_duplicate": true,
    "file_hash": "a1b2c3...",
    "queue_id": "queue_100",
    "original_upload_date": "2026-01-30T10:00:00Z",
    "original_filename": "invoice.pdf"
}
```

**Implementation Notes**:
- Query Core's `processed_files` table (or HeartBeat's dedup table)
- SHA256 hash is **content-based** (not filename-based)
- Return original `queue_id` so user can reference previous upload

**Why This Matters**:
```
User uploads invoice.pdf on Monday (processed)
User uploads same file on Tuesday (different filename: invoice_copy.pdf)

HeartBeat checks:
  - SHA256 hash matches? YES
  - Return: is_duplicate=true, queue_id=queue_100

Relay returns to user:
  "This file was already processed on Monday. Queue ID: queue_100"
```

**Graceful Degradation** (Relay handles):
- If this endpoint fails → Relay treats as not duplicate (logs warning)
- This is a **convenience feature** (not critical)

---

### ENDPOINT 5: Record Processed File Hash

**Route**: `POST /api/duplicate/record`

**Purpose**: Record file hash after Core successfully processes file

**Request**:
```json
{
    "file_hash": "a1b2c3...",
    "queue_id": "queue_123",
    "file_uuid": "550e8400-...",
    "filename": "invoice.pdf",
    "company_id": "execujet-ng"
}
```

**Response** (200 OK):
```json
{
    "status": "recorded",
    "file_hash": "a1b2c3..."
}
```

**Implementation Notes**:
- Write to `helium_blob.db.blob_deduplication` table
- Called by **Core** (not Relay) after successful processing
- This enables future duplicate detection

**NOTE**: Relay does NOT call this endpoint. Core calls it.

---

## 🗄️ DATABASE SCHEMA REQUIREMENTS

You need these tables in `helium_blob.db`:

### 1. blob_entries (Main Blob Tracking)

```sql
CREATE TABLE blob_entries (
    file_uuid TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    blob_path TEXT NOT NULL UNIQUE,
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    batch_id TEXT,
    company_id TEXT NOT NULL,
    uploaded_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'uploaded',  -- uploaded, finalized, deleted
    retention_until TIMESTAMP NOT NULL,  -- created_at + 7 years
    deleted_at TIMESTAMP,  -- Soft delete (24h recovery window)

    INDEX idx_company_created (company_id, created_at),
    INDEX idx_blob_path (blob_path),
    INDEX idx_file_hash (file_hash),
    INDEX idx_status (status)
);
```

### 2. blob_deduplication (Duplicate Detection)

```sql
CREATE TABLE blob_deduplication (
    file_hash TEXT PRIMARY KEY,
    queue_id TEXT NOT NULL,
    file_uuid TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    company_id TEXT NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (file_uuid) REFERENCES blob_entries(file_uuid),
    INDEX idx_company_hash (company_id, file_hash)
);
```

### 3. daily_usage (Company-Wide Limits)

```sql
CREATE TABLE daily_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    usage_date DATE NOT NULL,
    file_count INTEGER DEFAULT 0,
    total_size_bytes INTEGER DEFAULT 0,
    daily_limit INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (company_id, usage_date),
    INDEX idx_company_date (company_id, usage_date)
);
```

---

## 🔄 BLOB RECONCILIATION REQUIREMENTS

HeartBeat must implement **hourly reconciliation job** to detect orphaned/missing blobs:

### Reconciliation Job (Every 1 Hour)

```python
def reconcile_blobs():
    """
    Hourly job to reconcile MinIO blobs with helium_blob.db.

    Detects:
    1. Orphaned blobs (in MinIO but not in blob_entries)
    2. Missing blobs (in blob_entries but not in MinIO)
    3. Blobs not queued to Core after 24 hours
    4. Soft-deleted blobs past 24h recovery window
    """

    # 1. Scan MinIO for all blobs
    minio_blobs = minio_client.list_objects("helium-blobs")

    # 2. Get all blob_entries from database
    db_blobs = query("SELECT blob_path, file_uuid, created_at, status FROM blob_entries")

    # 3. Find orphaned blobs (in MinIO but not in DB)
    orphaned = [b for b in minio_blobs if b not in db_blobs]
    for blob in orphaned:
        logger.warning(f"Orphaned blob found: {blob}")
        create_notification("orphaned_blob", blob)
        # Optional: Register in blob_entries (recovery)

    # 4. Find missing blobs (in DB but not in MinIO)
    missing = [b for b in db_blobs if b not in minio_blobs]
    for blob in missing:
        logger.error(f"Missing blob: {blob}")
        create_notification("missing_blob", blob)

    # 5. Check blobs not queued to Core after 24h
    stale_blobs = query("""
        SELECT file_uuid, blob_path, created_at
        FROM blob_entries
        WHERE status = 'uploaded'
        AND created_at < datetime('now', '-24 hours')
    """)
    for blob in stale_blobs:
        # Check if in Core's core_queue
        in_queue = core_client.check_queue(blob['file_uuid'])
        if not in_queue:
            logger.warning(f"Blob not queued after 24h: {blob}")
            create_notification("blob_not_queued", blob)

    # 6. Cleanup soft-deleted blobs past 24h recovery window
    deleted_blobs = query("""
        SELECT file_uuid, blob_path
        FROM blob_entries
        WHERE status = 'deleted'
        AND deleted_at < datetime('now', '-24 hours')
    """)
    for blob in deleted_blobs:
        # Permanent delete from MinIO
        minio_client.delete_object(blob['blob_path'])
        # Remove from blob_entries
        query("DELETE FROM blob_entries WHERE file_uuid = ?", blob['file_uuid'])
        logger.info(f"Permanently deleted blob: {blob}")
```

**Why This Matters**:
- Detects when Relay writes blob but fails to register (network issue)
- Detects when MinIO loses files unexpectedly
- Detects when Core fails to queue files
- Provides audit trail for compliance

---

## 📈 7-YEAR RETENTION REQUIREMENTS

**FIRS Compliance**: All invoice blobs must be retained for **7 years**.

### Retention Logic

```python
def set_blob_retention(file_uuid, created_at):
    """
    Set retention_until = created_at + 7 years.

    Called when blob is registered.
    """
    from datetime import timedelta

    retention_until = created_at + timedelta(days=7*365)  # 7 years

    query("""
        UPDATE blob_entries
        SET retention_until = ?
        WHERE file_uuid = ?
    """, retention_until, file_uuid)
```

### 7-Day Processed Data Cleanup

**Core appends processed data to blob** (preview JSON, report, etc.):
- These are stored as **blob_outputs** (separate from main blob)
- Retained for **7 days** (not 7 years)
- Cleaned up after user finalizes or 7 days elapse

```sql
-- Cleanup job (daily)
DELETE FROM blob_outputs
WHERE created_at < datetime('now', '-7 days');
```

**Main blob vs Processed data**:
- Main blob (`invoice.pdf`) → 7 years
- Processed data (`preview.json`) → 7 days

---

## 🧪 TESTING REQUIREMENTS

HeartBeat team must test these scenarios:

### Unit Tests
- [ ] POST /api/blob/write → returns blob_path
- [ ] POST /api/blob/register → creates blob_entry
- [ ] POST /api/blob/register (duplicate) → returns 409 (idempotent)
- [ ] GET /api/daily_usage/check → returns allowed/limit_exceeded
- [ ] POST /api/duplicate/check → returns is_duplicate=true/false
- [ ] POST /api/duplicate/record → creates dedup entry

### Integration Tests (with Relay)
- [ ] Relay writes blob → HeartBeat stores in MinIO → returns blob_path
- [ ] Relay registers blob → HeartBeat creates blob_entry → returns retention_until
- [ ] Relay checks daily limit → HeartBeat returns usage stats
- [ ] Relay checks duplicate → HeartBeat returns original queue_id
- [ ] HeartBeat unavailable → Relay gracefully degrades (logs warning)

### Retry Tests
- [ ] Blob write fails (503) → Relay retries 5 times → succeeds on attempt 3
- [ ] Blob registration times out → Relay retries → HeartBeat returns 409 (already exists)
- [ ] Daily usage check fails → Relay allows upload anyway (graceful degradation)

### Reconciliation Tests
- [ ] Orphaned blob in MinIO (not in DB) → Reconciliation detects → Creates notification
- [ ] Missing blob (in DB but not MinIO) → Reconciliation detects → Creates alert
- [ ] Blob not queued after 24h → Reconciliation detects → Notifies ops

### Performance Tests
- [ ] 100 concurrent blob writes → All succeed
- [ ] 1000 daily usage checks → <100ms latency (95th percentile)
- [ ] Reconciliation job with 10,000 blobs → Completes in <60 seconds

---

## 📊 MONITORING & METRICS

HeartBeat should export these Prometheus metrics:

```
# Blob writes
helium_heartbeat_blob_writes_total{status="success"}
helium_heartbeat_blob_writes_total{status="error"}

# Blob write latency
helium_heartbeat_blob_write_duration_seconds{quantile="0.95"}

# Daily usage checks
helium_heartbeat_daily_usage_checks_total{status="allowed"}
helium_heartbeat_daily_usage_checks_total{status="limit_exceeded"}

# Duplicate checks
helium_heartbeat_duplicate_checks_total{is_duplicate="true"}
helium_heartbeat_duplicate_checks_total{is_duplicate="false"}

# Reconciliation
helium_heartbeat_reconciliation_orphaned_blobs
helium_heartbeat_reconciliation_missing_blobs
helium_heartbeat_reconciliation_duration_seconds
```

---

## 🚨 CRITICAL NOTES

### 1. Idempotence is MANDATORY

**Why**: Relay retries on network failure. Same request may arrive multiple times.

**Example**:
```
Attempt 1: POST /api/blob/register (file_uuid=550e8400-...)
  → Network timeout (did it reach HeartBeat? Unknown)

Attempt 2: POST /api/blob/register (same file_uuid)
  → HeartBeat must check: "Does 550e8400-... already exist?"
  → If yes: Return 409 (already_exists) - Relay treats as success ✅
  → If no: Create new entry, return 200 ✅
```

**DO NOT**: Create duplicate entries or throw 500 errors on retry

### 2. Graceful Degradation is Implemented

**Critical operations** (blob write):
- If HeartBeat fails → Relay returns error to user
- User cannot upload without blob storage

**Non-critical operations** (dedup, daily limits):
- If HeartBeat fails → Relay allows upload anyway (logs warning)
- Features degrade gracefully (no dedup, no limit enforcement)

### 3. 7-Year Retention is FIRS Compliance

**DO NOT**: Delete main blobs before 7 years
- `retention_until` must be set to `created_at + 7 years`
- Cleanup jobs must check `retention_until` before deleting

**Processed data** (preview JSON):
- Can be deleted after 7 days (not compliance-sensitive)

### 4. Reconciliation Detects Issues

**Without reconciliation**:
- Orphaned blobs waste storage space
- Missing blobs go undetected
- Files queued but never processed accumulate

**With reconciliation**:
- Ops team notified of anomalies
- Recovery actions can be triggered
- Audit trail for compliance

---

## 📅 IMPLEMENTATION TIMELINE

**Phase 1B** (complete): Relay implemented with retry logic + graceful degradation
**Phase 1C** (pending): OPUS validates HeartBeat implementation + integration tests
**Phase 2** (future): HeartBeat reconciliation job monitoring + alerts

**Deadline**: HeartBeat endpoints must be ready **before Phase 1C begins**.

---

## 📞 CONTACT & COORDINATION

**Relay Team**: relay-team@prodeus.com
**HeartBeat Team**: heartbeat-team@prodeus.com
**Integration Issues**: Submit to GitLab issue tracker

**Questions?**:
- Check `HELIUM_OVERVIEW.md` - Appendix A for full requirements
- Check `Services/Core/src/services/clients/heartbeat_client.py` for Relay's implementation
- Ask in #helium-integration Slack channel

---

## ✅ NEXT STEPS FOR HEARTBEAT TEAM

1. **Read this document** (you're here! ✓)
2. **Review database schema** (create tables in `helium_blob.db`)
3. **Implement 5 API endpoints** (see sections above)
4. **Implement reconciliation job** (hourly cron)
5. **Write tests** (unit, integration, performance)
6. **Export Prometheus metrics**
7. **Notify Relay team** when endpoints are ready for Phase 1C

---

**This document contains EVERYTHING HeartBeat team needs to implement for Relay integration.**

**Last Updated**: 2026-01-31
**Status**: 🔴 ACTION REQUIRED
