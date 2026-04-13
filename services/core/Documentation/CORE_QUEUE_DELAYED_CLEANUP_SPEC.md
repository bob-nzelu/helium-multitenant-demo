# Core Queue Delayed Cleanup - Implementation Specification

**For:** Core Implementation Team
**From:** HeartBeat Blob Storage Integration
**Date:** 2026-02-01
**Status:** SPECIFICATION - Ready for Core Implementation
**Priority:** Required for HeartBeat Reconciliation

---

## Executive Summary

Core must **STOP deleting `core_queue` entries immediately** after processing. Instead, schedule deletion **24 hours after each individual entry is processed**. This provides:

1. **Audit trail** for HeartBeat reconciliation (can verify processing status)
2. **Recovery window** if Core crashes during processing
3. **Compliance** with blob storage 7-year retention tracking

---

## What's Changing

### Current Behavior (INCORRECT):

```python
def process_file(queue_id):
    # ... processing logic ...

    # Mark as processed
    core_db.core_queue.update_one(
        {"queue_id": queue_id},
        {"status": "processed", "processed_at": now()}
    )

    # ❌ PROBLEM: Immediately delete entry
    core_db.core_queue.delete_one({"queue_id": queue_id})

    return {"status": "processed"}
```

**Problem:** HeartBeat's hourly reconciliation can't verify that Core finished processing because the `core_queue` entry is already gone.

---

### New Behavior (REQUIRED):

```python
def process_file(queue_id):
    # ... processing logic ...

    # Mark as processed with timestamp
    core_db.core_queue.update_one(
        {"queue_id": queue_id},
        {
            "status": "processed",
            "processed_at": now(),
            "updated_at": now()  # ✅ Important: track when processed
        }
    )

    # ✅ Schedule deletion for THIS specific entry 24 hours from now
    scheduler.add_job(
        delete_queue_entry,
        'date',
        run_date=now() + timedelta(hours=24),
        args=[queue_id],
        id=f"cleanup_queue_{queue_id}"  # Unique job ID
    )

    return {"status": "processed"}
```

**Key Changes:**
1. **Set `updated_at` timestamp** when marking as processed
2. **Schedule per-entry deletion** 24 hours in the future
3. **Do NOT delete immediately**

---

## Implementation Requirements

### 1. Add APScheduler Dependency

```bash
pip install apscheduler
```

### 2. Initialize Scheduler in Core Service

```python
# In Core/src/main.py or Core/src/app.py

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

# Job store persists scheduled jobs (survives Core restart)
jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.db')
}

scheduler = BackgroundScheduler(jobstores=jobstores)
scheduler.start()

# Make scheduler available to processing functions
app.state.scheduler = scheduler
```

### 3. Create Cleanup Function

```python
# In Core/src/workers/queue_cleanup.py

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def delete_queue_entry(queue_id: int):
    """
    Delete a single core_queue entry.

    Called 24 hours after entry is processed.
    Provides audit trail window for HeartBeat reconciliation.

    Args:
        queue_id: The queue entry ID to delete
    """
    try:
        # Check if entry still exists and is processed
        entry = core_db.core_queue.find_one({"queue_id": queue_id})

        if not entry:
            logger.warning(f"Queue entry {queue_id} not found (already deleted?)")
            return

        if entry["status"] != "processed":
            logger.warning(
                f"Queue entry {queue_id} status is '{entry['status']}', "
                f"not 'processed'. Skipping deletion."
            )
            return

        # Verify 24 hours have passed
        processed_at = entry.get("updated_at") or entry.get("processed_at")
        time_since_processed = datetime.utcnow() - processed_at

        if time_since_processed.total_seconds() < (24 * 3600):
            logger.warning(
                f"Queue entry {queue_id} processed only "
                f"{time_since_processed.total_seconds()/3600:.1f} hours ago. "
                f"Waiting until 24h window."
            )
            return

        # Safe to delete
        core_db.core_queue.delete_one({"queue_id": queue_id})

        logger.info(
            f"Deleted queue entry {queue_id} after 24h retention window "
            f"(processed at {processed_at.isoformat()})"
        )

        # Optional: Log to audit trail
        audit_db.insert({
            "table_name": "core_queue",
            "operation": "delete",
            "queue_id": queue_id,
            "blob_uuid": entry.get("blob_uuid"),
            "reason": "24-hour retention window expired",
            "deleted_at": datetime.utcnow().isoformat(),
            "service": "core-cleanup"
        })

    except Exception as e:
        logger.error(f"Failed to delete queue entry {queue_id}: {str(e)}", exc_info=True)
        raise
```

### 4. Update Processing Function

```python
# In Core/src/workers/processing.py

from datetime import datetime, timedelta

def process_file(queue_id):
    """Core file processing pipeline"""

    # Fetch from queue
    queue_entry = core_db.core_queue.find_one({"queue_id": queue_id})
    blob_path = queue_entry["blob_path"]

    # Fetch from MinIO
    file_data = minio.get_object(blob_path)

    # Process (extract, enrich, etc.)
    extracted_data = extract_invoices(file_data)
    enriched_data = enrich_invoices(extracted_data)

    # Create invoice records
    for invoice in enriched_data:
        core_db.invoices.insert({
            "invoice_number": invoice["number"],
            "customer": invoice["customer"],
            # ... etc
            "status": "draft"
        })

    # Mark as processed (DO NOT DELETE YET)
    now = datetime.utcnow()
    core_db.core_queue.update_one(
        {"queue_id": queue_id},
        {
            "status": "processed",
            "processed_at": now,
            "updated_at": now  # ✅ Track when processed
        }
    )

    # ✅ Schedule deletion for 24 hours from now
    from .queue_cleanup import delete_queue_entry

    scheduler.add_job(
        delete_queue_entry,
        'date',
        run_date=now + timedelta(hours=24),
        args=[queue_id],
        id=f"cleanup_queue_{queue_id}",
        replace_existing=True  # If job already exists, replace it
    )

    logger.info(
        f"Processed queue entry {queue_id}, scheduled cleanup for "
        f"{(now + timedelta(hours=24)).isoformat()}"
    )

    # Notify Edge
    core_db.edge_queue.insert({...})

    return {"status": "processed", "queue_id": queue_id}
```

---

## New API Endpoint Required

HeartBeat needs to query Core's queue status during reconciliation.

### Endpoint Specification

```python
# In Core/src/api/endpoints.py

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from datetime import datetime

router = APIRouter(prefix="/api/v1/core_queue", tags=["queue"])

@router.get("/status", response_model=List[Dict[str, Any]])
async def get_queue_status():
    """
    Get status of all core_queue entries.

    Used by HeartBeat during hourly reconciliation to verify processing status.

    Returns:
        List of queue entries with status, timestamps, and blob info

    Example Response:
        [
            {
                "queue_id": 123,
                "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
                "blob_path": "/files_blob/550e8400-...-invoice.pdf",
                "status": "processed",
                "created_at": "2026-02-01T10:00:00Z",
                "updated_at": "2026-02-01T10:05:00Z",
                "processed_at": "2026-02-01T10:05:00Z"
            },
            ...
        ]
    """
    try:
        # Fetch all queue entries
        entries = list(core_db.core_queue.find({}))

        # Format for response
        response = []
        for entry in entries:
            response.append({
                "queue_id": entry["queue_id"],
                "blob_uuid": entry.get("blob_uuid"),
                "blob_path": entry.get("blob_path"),
                "status": entry["status"],  # "queued", "processing", "processed"
                "created_at": entry["created_at"].isoformat() if entry.get("created_at") else None,
                "updated_at": entry["updated_at"].isoformat() if entry.get("updated_at") else None,
                "processed_at": entry["processed_at"].isoformat() if entry.get("processed_at") else None
            })

        return response

    except Exception as e:
        logger.error(f"Failed to fetch queue status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch queue status"
        )
```

**Register the router:**
```python
# In Core/src/api/app.py
from .endpoints import router as queue_router

app.include_router(queue_router)
```

---

## Database Schema Update

Ensure `core_queue` table has `updated_at` field:

```sql
-- If not already present, add updated_at column
ALTER TABLE core_queue ADD COLUMN updated_at TIMESTAMP;

-- Update existing rows
UPDATE core_queue SET updated_at = processed_at WHERE status = 'processed';
UPDATE core_queue SET updated_at = created_at WHERE status != 'processed';
```

---

## Error Handling

### If Core Crashes Before Scheduling Deletion:

```python
# Add this to Core startup/recovery logic

def recover_unscheduled_cleanups():
    """
    On Core startup, schedule cleanups for any processed entries
    that don't have scheduled jobs.
    """
    processed_entries = core_db.core_queue.find({"status": "processed"})

    for entry in processed_entries:
        queue_id = entry["queue_id"]
        processed_at = entry.get("updated_at") or entry.get("processed_at")

        # Calculate when this should be deleted
        delete_at = processed_at + timedelta(hours=24)

        # Check if job already exists
        job_id = f"cleanup_queue_{queue_id}"
        if scheduler.get_job(job_id):
            continue  # Already scheduled

        # Schedule (or execute immediately if past due)
        if delete_at <= datetime.utcnow():
            # Past due - delete immediately
            delete_queue_entry(queue_id)
        else:
            # Schedule for future
            scheduler.add_job(
                delete_queue_entry,
                'date',
                run_date=delete_at,
                args=[queue_id],
                id=job_id
            )

        logger.info(f"Recovered cleanup scheduling for queue entry {queue_id}")
```

---

## Testing Requirements

### Test Cases (Minimum):

1. ✅ **Process file normally** → Entry marked "processed", not deleted immediately
2. ✅ **Wait 24 hours** → Entry deleted automatically
3. ✅ **Core restart before 24h** → Entry still exists, cleanup rescheduled
4. ✅ **Query status endpoint** → Returns processed entries for HeartBeat
5. ✅ **Multiple entries** → Each gets its own 24h deletion schedule
6. ✅ **Audit trail** → All deletions logged to audit.db

### Example Test:

```python
def test_delayed_cleanup():
    """Test that queue entries are deleted 24 hours after processing"""

    # Process a file
    queue_id = process_file(test_file)

    # Verify entry still exists
    entry = core_db.core_queue.find_one({"queue_id": queue_id})
    assert entry is not None
    assert entry["status"] == "processed"

    # Verify cleanup job is scheduled
    job_id = f"cleanup_queue_{queue_id}"
    job = scheduler.get_job(job_id)
    assert job is not None

    # Fast-forward 24 hours (in tests, manually trigger job)
    scheduler._run_job(job)

    # Verify entry is deleted
    entry = core_db.core_queue.find_one({"queue_id": queue_id})
    assert entry is None
```

---

## Monitoring & Alerts

Monitor these metrics:

```python
def monitor_queue_cleanup_health():
    """Monitor cleanup job health"""

    metrics = {
        # Entries stuck in "processing" for >1 hour
        "stuck_processing": core_db.core_queue.count({
            "status": "processing",
            "updated_at": {"$lt": now() - timedelta(hours=1)}
        }),

        # Very old processed entries (>25 hours - cleanup may have failed)
        "overdue_cleanup": core_db.core_queue.count({
            "status": "processed",
            "updated_at": {"$lt": now() - timedelta(hours=25)}
        }),

        # Pending cleanups (scheduled but not executed)
        "pending_cleanups": len(scheduler.get_jobs())
    }

    # Alert if:
    # - stuck_processing > 0 (Core may be hung)
    # - overdue_cleanup > 10 (cleanup job failing)
    # - pending_cleanups > 1000 (too many scheduled jobs)

    return metrics
```

---

## Integration with HeartBeat

HeartBeat will:
1. **Call `GET /api/v1/core_queue/status`** every hour during reconciliation
2. **Verify blob processing** by checking if `blob_uuid` is in queue with `status="processed"`
3. **Update blob_entries** to `status="finalized"` when Core confirms processing
4. **Detect stale processing** if entries stuck in "processing" for >1 hour

---

## Summary of Changes

| Item | Before | After | Reason |
|------|--------|-------|--------|
| **Delete timing** | Immediate | 24 hours later | Allow reconciliation |
| **Set `updated_at`** | No | Yes ✅ | Track processing time |
| **Cleanup method** | Direct delete | Scheduled job per entry | Per-entry tracking |
| **API endpoint** | None | `GET /api/v1/core_queue/status` ✅ | HeartBeat integration |
| **Recovery logic** | None | Startup recovery ✅ | Handle Core crashes |

---

## Questions?

- **"Why 24 hours?"** → Allows HeartBeat reconciliation to verify processing, provides recovery window
- **"Why per-entry scheduling instead of batch?"** → More accurate, prevents race conditions, better audit trail
- **"What if cleanup job fails?"** → Entry stays until next Core restart triggers recovery logic
- **"What if scheduler database is lost?"** → Recovery logic on startup reschedules all pending cleanups

---

## Implementation Checklist

- [ ] Install APScheduler dependency
- [ ] Initialize scheduler in Core service
- [ ] Create `delete_queue_entry()` function
- [ ] Update processing function to schedule deletions
- [ ] Add `GET /api/v1/core_queue/status` endpoint
- [ ] Add `updated_at` column to `core_queue` table
- [ ] Implement startup recovery logic
- [ ] Write tests (6+ test cases)
- [ ] Add monitoring metrics
- [ ] Update Core documentation
- [ ] Commit changes

---

**Status:** ✅ Specification Ready
**Integration Date:** Required before HeartBeat Reconciliation Phase
**Estimated Effort:** 1-2 days for Core team
**Priority:** HIGH (blocks HeartBeat reconciliation)

---

**Document Version:** 1.0
**Last Updated:** 2026-02-01
**Contact:** HeartBeat Team (for questions about integration)
