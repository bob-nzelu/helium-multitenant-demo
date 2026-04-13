# RELAY → CORE API FLOW - CORRECTED ARCHITECTURE

**Version:** 1.0
**Date:** 2026-02-06
**Phase:** Phase 0a (Infrastructure Architecture)
**Status:** BINDING - Correct flow based on user clarification

---

## OVERVIEW

This document clarifies the correct API flow between Relay, Core, and HeartBeat services. **Relay does NOT write directly to databases** - all operations go through API calls.

---

## CORRECT RELAY INGESTION FLOW

```python
async def relay_ingest_file(file_data):
    """
    Relay ingestion flow - ALL via API calls
    No direct database writes from Relay
    """

    try:
        # ========================================
        # STEP 1: Call HeartBeat Blob API
        # HeartBeat writes atomically to MinIO + blob.db
        # ========================================
        blob_response = await heartbeat_api.post("/api/blob/write", json={
            "filename": file_data.filename,
            "content": base64.b64encode(file_data.content).decode(),
            "company_id": file_data.company_id,
            "uploaded_by": file_data.uploaded_by,
            "file_hash": hashlib.sha256(file_data.content).hexdigest()
        })

        blob_uuid = blob_response["blob_uuid"]
        blob_path = blob_response["blob_path"]

        # ========================================
        # STEP 2: Call Core Enqueue API
        # Core writes to core_queue table (its own database)
        # ========================================
        enqueue_response = await core_api.post("/api/queue/enqueue", json={
            "blob_uuid": blob_uuid,
            "original_filename": file_data.filename,
            "company_id": file_data.company_id,
            "uploaded_by": file_data.uploaded_by,
            "immediate_processing": file_data.immediate_processing,
            "batch_id": file_data.batch_id  # Optional, for bulk uploads
        })

        queue_id = enqueue_response["queue_id"]

        # ========================================
        # STEP 3: Call Core Process API
        # Core starts processing the queue entry
        # ========================================
        process_response = await core_api.post("/api/process", json={
            "queue_id": queue_id
        })

        # ========================================
        # STEP 4: Register with audit.db
        # Call HeartBeat Audit API (best-effort, async)
        # ========================================
        await heartbeat_api.post("/api/audit/log", json={
            "event_type": "relay.queue_written",
            "service_name": "relay",
            "queue_id": queue_id,
            "blob_uuid": blob_uuid,
            "company_id": file_data.company_id,
            "user_email": file_data.uploaded_by,
            "status": "success",
            "event_data": json.dumps({
                "filename": file_data.filename,
                "file_size": len(file_data.content),
                "batch_id": file_data.batch_id
            })
        })

        # ========================================
        # STEP 5: Return response to Float
        # ========================================
        return {
            "status": "success",
            "queue_id": queue_id,
            "blob_uuid": blob_uuid,
            "preview_data": process_response.get("preview_data")  # If preview mode
        }

    except Exception as e:
        # Log failure to audit.db
        await heartbeat_api.post("/api/audit/log", json={
            "event_type": "relay.ingest_failed",
            "service_name": "relay",
            "company_id": file_data.company_id,
            "status": "failure",
            "error_message": str(e)
        })
        raise
```

---

## REQUIRED API ENDPOINTS

### HeartBeat APIs (HeartBeat must implement)

#### 1. POST /api/blob/write

**Purpose**: Write blob to MinIO + register in blob.db (atomic)

**Request**:
```json
{
    "filename": "invoice.pdf",
    "content": "<base64_encoded_bytes>",
    "company_id": "execujet-ng",
    "uploaded_by": "user@example.com",
    "file_hash": "a1b2c3d4..."
}
```

**Response** (200 OK):
```json
{
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "created_at": "2026-02-06T10:00:00Z"
}
```

**HeartBeat Internal Logic**:
```python
async def blob_write(request):
    async with transaction():
        # 1. Write to MinIO
        blob_uuid = generate_uuid()
        blob_path = f"/files_blob/{blob_uuid}-{request.filename}"
        await minio_client.put_object(
            bucket="helium-raw-data",
            object_name=blob_path,
            data=base64.b64decode(request.content)
        )

        # 2. Write to blob.db
        await blob_db.insert({
            "blob_uuid": blob_uuid,
            "filename": request.filename,
            "blob_path": blob_path,
            "file_hash": request.file_hash,
            "company_id": request.company_id,
            "uploaded_by": request.uploaded_by,
            "status": "uploaded"
        })

        # Atomically commit both operations
        await commit()

    return {"blob_uuid": blob_uuid, "blob_path": blob_path}
```

---

#### 2. POST /api/audit/log

**Purpose**: Log event to audit.db

**Request**:
```json
{
    "event_type": "relay.queue_written",
    "service_name": "relay",
    "queue_id": "queue_123",
    "blob_uuid": "550e8400-...",
    "company_id": "execujet-ng",
    "user_email": "user@example.com",
    "status": "success",
    "event_data": "{\"filename\": \"invoice.pdf\"}"
}
```

**Response** (200 OK):
```json
{
    "status": "logged",
    "audit_id": 12345
}
```

---

### Core APIs (Core must implement)

#### 3. POST /api/queue/enqueue

**Purpose**: Add entry to core_queue table

**Request**:
```json
{
    "blob_uuid": "550e8400-...",
    "original_filename": "invoice.pdf",
    "company_id": "execujet-ng",
    "uploaded_by": "user@example.com",
    "immediate_processing": false,
    "batch_id": "batch_550e8400-..."
}
```

**Response** (200 OK):
```json
{
    "queue_id": "queue_123",
    "status": "queued",
    "created_at": "2026-02-06T10:00:01Z"
}
```

**Core Internal Logic**:
```python
async def queue_enqueue(request):
    queue_id = f"queue_{generate_id()}"

    await core_queue_db.insert({
        "queue_id": queue_id,
        "blob_uuid": request.blob_uuid,
        "original_filename": request.original_filename,
        "company_id": request.company_id,
        "uploaded_by": request.uploaded_by,
        "immediate_processing": request.immediate_processing,
        "batch_id": request.batch_id,
        "status": "PENDING",
        "priority": 5,  # Default priority
        "created_at": datetime.now()
    })

    return {"queue_id": queue_id, "status": "queued"}
```

---

#### 4. POST /api/process

**Purpose**: Start processing a queue entry

**Request**:
```json
{
    "queue_id": "queue_123"
}
```

**Response** (200 OK - Immediate Mode):
```json
{
    "status": "processing",
    "queue_id": "queue_123",
    "message": "Processing started"
}
```

**Response** (200 OK - Preview Mode):
```json
{
    "status": "preview_ready",
    "queue_id": "queue_123",
    "preview_data": {
        "invoice_id": "INV_001",
        "customer_name": "Acme Corp",
        "total_amount": 1000.00,
        "line_items": [...]
    }
}
```

**Core Internal Logic**:
```python
async def process_queue_entry(request):
    # 1. Fetch queue entry
    queue_entry = await core_queue_db.get(request.queue_id)

    # 2. Update status to PROCESSING
    await core_queue_db.update(request.queue_id, {"status": "PROCESSING"})

    # 3. Trigger processing pipeline (async)
    if queue_entry.immediate_processing:
        # Start full processing (async)
        asyncio.create_task(processing_pipeline.run(queue_entry))
        return {"status": "processing", "queue_id": request.queue_id}
    else:
        # Run preview mode (sync, return preview data)
        preview_data = await processing_pipeline.run_preview(queue_entry)
        return {"status": "preview_ready", "preview_data": preview_data}
```

---

## SEQUENCE DIAGRAM

```
Float UI          Relay              HeartBeat         Core
   |                |                     |              |
   |-- upload ----->|                     |              |
   |                |-- POST /blob/write->|              |
   |                |                     |              |
   |                |                     |-- Write MinIO
   |                |                     |-- Write blob.db
   |                |                     |              |
   |                |<-- blob_uuid -------|              |
   |                |                     |              |
   |                |-- POST /queue/enqueue ------------>|
   |                |                     |              |
   |                |                     |              |-- Write core_queue
   |                |                     |              |
   |                |<-- queue_id --------------------- |
   |                |                     |              |
   |                |-- POST /process ------------------->|
   |                |                     |              |
   |                |                     |              |-- Start processing
   |                |                     |              |
   |                |<-- response (preview or ack) -----|
   |                |                     |              |
   |                |-- POST /audit/log ->|              |
   |                |                     |              |
   |                |                     |-- Write audit.db
   |                |                     |              |
   |                |<-- logged ----------|              |
   |                |                     |              |
   |<-- response ---|                     |              |
   |                |                     |              |
```

---

## ERROR HANDLING

### Scenario 1: HeartBeat Blob Write Fails

```python
# Step 1 fails - no blob created
try:
    blob_response = await heartbeat_api.post("/api/blob/write", ...)
except Exception as e:
    # Log to audit.db (failure)
    await heartbeat_api.post("/api/audit/log", {
        "event_type": "relay.blob_write_failed",
        "status": "failure",
        "error_message": str(e)
    })
    # Return error to Float
    raise RelayError("Failed to write blob to storage")
```

**Result**: No blob, no queue entry, user sees error immediately.

---

### Scenario 2: Core Enqueue Fails (Blob Exists)

```python
# Step 1 succeeds, Step 2 fails
blob_response = await heartbeat_api.post("/api/blob/write", ...)  # ✅ Success
blob_uuid = blob_response["blob_uuid"]

try:
    enqueue_response = await core_api.post("/api/queue/enqueue", ...)
except Exception as e:
    # Log orphaned blob
    await heartbeat_api.post("/api/audit/log", {
        "event_type": "relay.enqueue_failed",
        "blob_uuid": blob_uuid,
        "status": "failure",
        "error_message": str(e)
    })
    # Return error to Float
    raise RelayError("Failed to enqueue for processing")
```

**Result**: Orphaned blob in MinIO. HeartBeat reconciliation will detect this in audit.db (blob_written but no queue_written) and create notification for ops review.

---

### Scenario 3: Core Process Fails (Blob + Queue Exist)

```python
# Steps 1-2 succeed, Step 3 fails
blob_uuid = (await heartbeat_api.post("/api/blob/write", ...))["blob_uuid"]  # ✅
queue_id = (await core_api.post("/api/queue/enqueue", ...))["queue_id"]      # ✅

try:
    process_response = await core_api.post("/api/process", ...)
except Exception as e:
    # Log processing failure
    await heartbeat_api.post("/api/audit/log", {
        "event_type": "relay.process_call_failed",
        "queue_id": queue_id,
        "blob_uuid": blob_uuid,
        "status": "failure",
        "error_message": str(e)
    })
    # Return error to Float
    raise RelayError("Failed to start processing")
```

**Result**: Queue entry exists but never processed. HeartBeat reconciliation will detect this (queue_written but no processing_started after 1 hour) and retry or alert ops.

---

### Scenario 4: Audit Log Fails (Non-Critical)

```python
# Steps 1-3 succeed, Step 4 fails
blob_uuid = (await heartbeat_api.post("/api/blob/write", ...))["blob_uuid"]  # ✅
queue_id = (await core_api.post("/api/queue/enqueue", ...))["queue_id"]      # ✅
process_response = await core_api.post("/api/process", ...)                  # ✅

try:
    await heartbeat_api.post("/api/audit/log", ...)
except Exception as e:
    # Log locally, continue (audit is best-effort)
    logger.warning(f"Failed to write audit log: {e}")
    # DO NOT raise error - processing already started successfully
```

**Result**: Processing continues successfully. Audit log missing this entry, but processing completes. HeartBeat reconciliation may detect gap, but not critical since processing succeeded.

---

## KEY ARCHITECTURAL POINTS

### 1. No Direct Database Writes from Relay

**Relay does NOT**:
- ❌ Write to MinIO directly
- ❌ Write to blob.db directly
- ❌ Write to core_queue directly
- ❌ Write to audit.db directly

**Relay ONLY**:
- ✅ Calls HeartBeat API (blob operations)
- ✅ Calls Core API (queue operations)
- ✅ Calls HeartBeat API (audit logging)

---

### 2. Atomicity Handled by Each Service

**HeartBeat atomicity**:
- MinIO write + blob.db write in single transaction
- If either fails, both rollback

**Core atomicity**:
- core_queue write is single operation (inherently atomic)

**No cross-service atomicity**:
- Cannot have atomic transaction across Relay → HeartBeat → Core
- Use best-effort + reconciliation strategy

---

### 3. Reconciliation Strategy

**HeartBeat hourly reconciliation**:
1. Scan audit.db for orphaned blobs (blob_written but no queue_written)
2. Scan audit.db for stuck processing (queue_written but no processing_started after 1 hour)
3. Create notifications for ops review
4. Optionally retry failed operations

---

### 4. Audit Logging is Best-Effort

- Audit log failure does NOT block critical path
- Logged asynchronously after processing starts
- Missing audit entries detected by HeartBeat reconciliation

---

## IMPLEMENTATION CHECKLIST FOR CORE (Phase 0 - OPUS)

### API Endpoints to Implement

- [ ] **POST /api/queue/enqueue**
  - [ ] Validate request (blob_uuid, company_id required)
  - [ ] Generate unique queue_id
  - [ ] Write to core_queue table
  - [ ] Return queue_id in response
  - [ ] Handle duplicate queue_id (idempotent)

- [ ] **POST /api/process**
  - [ ] Validate request (queue_id required)
  - [ ] Fetch queue entry from core_queue
  - [ ] Update status to PROCESSING
  - [ ] If immediate_processing: start async processing
  - [ ] If preview mode: run preview sync, return preview_data
  - [ ] Handle queue_id not found (404 error)

- [ ] **GET /api/queue/status**
  - [ ] Return queue status for HeartBeat monitoring
  - [ ] Count pending, processing, completed, failed entries
  - [ ] Return oldest pending entry age

---

## IMPLEMENTATION CHECKLIST FOR HEARTBEAT

### API Endpoints to Implement

- [ ] **POST /api/blob/write**
  - [ ] Decode base64 content
  - [ ] Generate blob_uuid
  - [ ] Write to MinIO (atomic transaction)
  - [ ] Write to blob.db (atomic transaction)
  - [ ] Return blob_uuid and blob_path
  - [ ] Handle duplicate blob_uuid (idempotent)

- [ ] **POST /api/audit/log**
  - [ ] Validate request (event_type, service_name required)
  - [ ] Write to audit.db (PostgreSQL)
  - [ ] Return audit_id
  - [ ] Handle database errors gracefully

- [ ] **Reconciliation Jobs**
  - [ ] Hourly job: scan audit.db for orphaned blobs
  - [ ] Hourly job: scan audit.db for stuck processing
  - [ ] Create notifications for ops review
  - [ ] Optionally retry failed operations

---

## TESTING REQUIREMENTS

### Integration Tests (Relay → HeartBeat → Core)

1. **Happy Path Test**:
   - [ ] Upload file through Relay
   - [ ] Verify blob written to MinIO
   - [ ] Verify blob.db entry created
   - [ ] Verify core_queue entry created
   - [ ] Verify processing started
   - [ ] Verify audit.db entries created

2. **Failure Scenario Tests**:
   - [ ] HeartBeat blob write fails → no queue entry
   - [ ] Core enqueue fails → orphaned blob detected
   - [ ] Core process fails → stuck queue entry detected
   - [ ] Audit log fails → processing continues

3. **Reconciliation Tests**:
   - [ ] Orphaned blob detected after 1 hour
   - [ ] Stuck processing detected after 1 hour
   - [ ] HeartBeat creates notifications

---

## SUMMARY

**Correct Flow**:
1. Relay → HeartBeat `/api/blob/write` (MinIO + blob.db)
2. Relay → Core `/api/queue/enqueue` (core_queue table)
3. Relay → Core `/api/process` (start processing)
4. Relay → HeartBeat `/api/audit/log` (audit.db, best-effort)

**No Direct Database Writes from Relay** - All via API calls.

**Atomicity**: Each service handles its own atomicity; no cross-service transactions.

**Reconciliation**: HeartBeat detects orphaned resources via audit.db queries.

---

**End of Document**
