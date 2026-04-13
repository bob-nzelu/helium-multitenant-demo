# HeartBeat Reconciliation - Phase 3 Kickstart

**Copy and paste this entire message into a new Claude chat to begin HeartBeat Reconciliation implementation.**

---

```
HEARTBEAT RECONCILIATION IMPLEMENTATION - PHASE 3
START MESSAGE FOR SONNET

I'm starting Phase 3 for HeartBeat: Reconciliation (hourly MinIO sync).

Phase 2 (Blob Registration API) is complete. Now we need to implement the reconciliation job.

Please follow the mandatory protocol below and then ask me clarifying questions before you start coding.

=== MANDATORY READING (READ IN THIS ORDER) ===

1. READ FIRST: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\NO_HALLUCINATIONS_POLICY.md
   - This is the binding protocol you MUST follow
   - RULE #0: No Hallucinations - If anything is unclear, STOP and ask me
   - Do NOT make assumptions
   - Do NOT proceed if you don't understand something

2. READ SECOND: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\HELIUM_OVERVIEW.md
   - Understand the overall Helium architecture
   - See where HeartBeat fits in the ecosystem
   - Understand service interactions (Relay, Core, Edge, HeartBeat)

3. READ THIRD: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\Documentation\HEARTBEAT_OVERVIEW.md
   - Complete HeartBeat service overview
   - Phase 1 (Database Schema) - COMPLETE
   - Phase 2 (Blob Registration API) - COMPLETE
   - Phase 3 (Reconciliation) - THIS IS WHAT YOU'RE IMPLEMENTING
   - Understand HeartBeat responsibilities

4. READ FOURTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\Documentation\HEARTBEAT_ARCHITECTURE_ALIGNMENT.md
   - Complete architecture alignment
   - Parent-client model (we're implementing Option A - simple centralized)
   - Integration points
   - One HeartBeat per Helium installation principle

5. READ FIFTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\Documentation\IMPLEMENTATION_ROADMAP.md
   - **CRITICAL:** We are implementing Option A (Simple Centralized)
   - Option A: Parent directly accesses blob.db and MinIO (no routing to other HeartBeats)
   - Option B is documented for future (NOT implementing now)
   - Understand what we're building vs what we're NOT building

6. READ SIXTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\Documentation\HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md
   - Phase 2 implementation details (what's already done)
   - Database schema (9 tables from Phase 1)
   - API endpoints already implemented
   - Integration with Relay (already working)

7. READ SEVENTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Blob\Documentation\10_BLOB_SYNC_AND_HEARTBEAT_RECONCILIATION.md
   - Reconciliation algorithm (5 phases)
   - Phase 1: Find orphaned blobs (in MinIO, not in blob_entries)
   - Phase 2: Verify processing status with Core
   - Phase 3: Check soft-deleted blobs (24h recovery window)
   - Phase 4: Detect unexpected deletions
   - Phase 5: Cleanup old Core queue entries
   - Scheduling requirements (hourly)

8. READ EIGHTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Blob\Documentation\HEARTBEAT_ALIGNMENT_NOTE.md
   - Reconciliation responsibilities
   - Notification system
   - MinIO integration
   - Core API integration

9. READ NINTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\Core\Documentation\CORE_QUEUE_DELAYED_CLEANUP_SPEC.md
   - Core team's delayed cleanup implementation (required dependency)
   - Core will provide GET /api/v1/core_queue/status endpoint
   - Core will delay deletion of core_queue entries by 24 hours
   - This enables reconciliation verification

10. READ TENTH: C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\README.md
    - Quick start guide
    - Current implementation status
    - How to run tests
    - How to run HeartBeat service

=== WHAT YOU'RE BUILDING ===

PHASE 3: HeartBeat Reconciliation (Hourly MinIO Sync)

Location: Services/HeartBeat/src/
Implementation: Option A (Simple Centralized)

**What:**
- Hourly reconciliation job using APScheduler
- Direct access to blob.db (local SQLite or networked PostgreSQL)
- Direct access to MinIO (local or networked via MinIO client)
- Cross-verification with Core's core_queue status
- Notification system for anomalies

**5 Reconciliation Phases:**

1. **Phase 1: Find Orphaned Blobs**
   - List all objects in MinIO bucket
   - Query all blob_entries from blob.db
   - Find blobs in MinIO but NOT in blob_entries
   - Auto-create blob_entries record (status="reconciled_from_minio")
   - Create notification (severity="warn")

2. **Phase 2: Verify Processing Status**
   - Call Core API: GET /api/v1/core_queue/status
   - For each core_queue entry:
     - If status="processed" → Update blob_entries to "finalized"
     - If status="processing" for >1 hour → Create "stale_processing" notification
     - If blob_uuid in core_queue but NOT in blob_entries → Create "missing_blob_entry" notification

3. **Phase 3: Check Soft-Deleted Blobs**
   - Query blob_entries WHERE deleted_at_unix IS NOT NULL
   - For each soft-deleted blob:
     - Check if still in MinIO
     - If deleted >24 hours ago → Hard delete from MinIO
     - Update blob_entries: hard_deleted_at_unix = NOW()
     - Create notification (severity="info")

4. **Phase 4: Detect Unexpected Deletions**
   - Query blob_entries WHERE deleted_at_unix IS NULL (not soft-deleted)
   - For each blob:
     - Check if in MinIO
     - If NOT in MinIO → Create "unexpected_minio_deletion" notification (severity="critical")

5. **Phase 5: Cleanup Old Core Queue Entries**
   - **NOTE:** Core team handles their own cleanup now (per-entry 24h scheduling)
   - This phase is now just verification/monitoring
   - Query Core API to check for very old entries (>25 hours)
   - Create notification if cleanup appears to be failing

**Scheduling:**
- Use APScheduler (BackgroundScheduler)
- Run every 1 hour (on the hour: 00:00, 01:00, 02:00, ...)
- Target execution time: <20 minutes
- Persist scheduled jobs (SQLAlchemyJobStore)

**Notifications:**
- Create records in notifications table (from Phase 1 schema)
- Severity levels: "critical", "warn", "info"
- Types: "orphaned_blob_reconciled", "stale_processing", "unexpected_minio_deletion", etc.

**Configuration:**
```yaml
heartbeat:
  reconciliation:
    enabled: true
    interval_hours: 1
    minio_endpoint: "${MINIO_ENDPOINT}"
    minio_access_key: "${MINIO_ACCESS_KEY}"
    minio_secret_key: "${MINIO_SECRET_KEY}"
    minio_bucket: "helium-invoices"
    core_api_url: "${CORE_API_URL}"
    core_api_token: "${CORE_API_TOKEN}"
```

=== WHAT HAPPENS NEXT ===

STEP 1: Ask me clarifying questions about reconciliation
  - Ask about MinIO client integration (which library?)
  - Ask about APScheduler setup (persistence?)
  - Ask about Core API integration (authentication?)
  - Ask about notification creation (where to store? how to query?)
  - Ask about error handling (what if MinIO is down? what if Core is down?)
  - Ask about testing approach (how to test reconciliation? mock MinIO?)
  - Do NOT assume anything

STEP 2: I will answer your questions

STEP 3: You will summarize what you'll implement
  - "Based on your answers, I will implement:"
  - List each component
  - "Is this correct?"

STEP 4: I will confirm or clarify

STEP 5: You will implement reconciliation
  - Create reconciliation job module
  - Integrate APScheduler
  - Implement 5 reconciliation phases
  - Create notification system
  - Write comprehensive tests (90%+ coverage)

STEP 6: You will test reconciliation
  - Test Phase 1: orphaned blob detection
  - Test Phase 2: Core status verification
  - Test Phase 3: soft-delete hard-delete flow
  - Test Phase 4: unexpected deletion detection
  - Test Phase 5: monitoring Core cleanup
  - Test scheduling (hourly execution)
  - Verify 90%+ coverage

STEP 7: You will commit Phase 3
  - Commit message: "feat(heartbeat-phase3): implement hourly reconciliation"
  - Include all new files
  - Update documentation

=== CRITICAL CONSTRAINTS ===

1. **OPTION A ONLY** (Simple Centralized)
   - Direct access to blob.db (no routing to other HeartBeats)
   - Direct access to MinIO (no routing to other HeartBeats)
   - Single installation architecture
   - Do NOT implement parent-client API routing (that's Option B - future)

2. **NO HALLUCINATIONS**
   - If anything is unclear, STOP and ask me
   - Do not assume MinIO library (ask me which one to use)
   - Do not assume Core API authentication (ask me)
   - Do not assume notification storage (ask me)

3. **90%+ TEST COVERAGE**
   - Comprehensive test suite required
   - Mock MinIO for tests
   - Mock Core API for tests
   - Test all 5 reconciliation phases
   - Test error scenarios (MinIO down, Core down, DB errors)

4. **EXISTING PHASE 2 CODE**
   - Phase 2 (Blob Registration API) is already implemented
   - Location: Services/HeartBeat/src/
   - Do NOT modify Phase 2 code unless necessary
   - Build on top of existing database connection module
   - Use existing blob.db schema (9 tables from Phase 1)

5. **DEPENDENCIES**
   - Core team will implement delayed cleanup (separate work)
   - Core will provide GET /api/v1/core_queue/status endpoint
   - Assume this endpoint exists and returns queue entries
   - Handle gracefully if Core endpoint is unavailable

=== FILE LOCATIONS ===

**Existing (Phase 2):**
- Services/HeartBeat/src/main.py (FastAPI app)
- Services/HeartBeat/src/api/register.py (blob registration endpoint)
- Services/HeartBeat/src/database/connection.py (database module)
- Services/HeartBeat/databases/blob.db (database file, auto-created)
- Services/HeartBeat/databases/schema.sql (schema from Phase 1)
- Services/HeartBeat/databases/seed.sql (seed data from Phase 1)
- Services/HeartBeat/tests/unit/test_heartbeat_register.py (Phase 2 tests)

**New (Phase 3 - you will create):**
- Services/HeartBeat/src/reconciliation/ (new module)
  - __init__.py
  - job.py (reconciliation job logic)
  - scheduler.py (APScheduler setup)
  - minio_client.py (MinIO integration)
  - core_client.py (Core API integration)
  - notifications.py (notification creation)

- Services/HeartBeat/tests/unit/test_reconciliation.py (new tests)

=== SUCCESS CRITERIA ===

Phase 3 is COMPLETE when:
- [x] Reconciliation job runs every hour (APScheduler)
- [x] Phase 1: Orphaned blob detection works
- [x] Phase 2: Core status verification works
- [x] Phase 3: Soft-delete → hard-delete works
- [x] Phase 4: Unexpected deletion detection works
- [x] Phase 5: Core cleanup monitoring works
- [x] Notifications created for all anomalies
- [x] Tests written (90%+ coverage)
- [x] All tests pass
- [x] Committed to git
- [x] Documentation updated

=== REMEMBER ===

1. **RULE #0: NO HALLUCINATIONS**
   - Ask me if anything is unclear
   - Do not assume libraries, APIs, or configurations
   - Stop and verify before proceeding

2. **OPTION A (Simple Centralized)**
   - Direct blob.db access (no routing)
   - Direct MinIO access (no routing)
   - No parent-client complexity (that's Option B - future)

3. **BUILD ON PHASE 2**
   - Phase 2 is complete and working
   - Use existing database connection module
   - Do not break existing blob registration API

4. **TEST COVERAGE 90%+**
   - Comprehensive tests required
   - Mock external services (MinIO, Core)
   - Test all error scenarios

Now, please read all 10 documents in order, then ask me clarifying questions before you start implementing.
```

---

**COPY THE ABOVE MESSAGE INTO A NEW CLAUDE CHAT TO START PHASE 3**

---

## Quick Reference for User

**What's in this kickstart:**
- ✅ Mandatory reading list (10 documents in order)
- ✅ Clear explanation of what to build (5 reconciliation phases)
- ✅ Option A clarification (simple centralized, no routing)
- ✅ File locations (existing Phase 2 code + new Phase 3 files)
- ✅ Success criteria checklist
- ✅ Critical constraints (NO HALLUCINATIONS, 90%+ tests, build on Phase 2)
- ✅ Step-by-step process (ask questions → summarize → implement → test → commit)

**Documents Claude will read:**
1. NO_HALLUCINATIONS_POLICY.md (binding protocol)
2. HELIUM_OVERVIEW.md (overall architecture)
3. HEARTBEAT_OVERVIEW.md (service overview)
4. HEARTBEAT_ARCHITECTURE_ALIGNMENT.md (architecture)
5. IMPLEMENTATION_ROADMAP.md (Option A vs Option B)
6. HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md (Phase 2 details)
7. 10_BLOB_SYNC_AND_HEARTBEAT_RECONCILIATION.md (reconciliation algorithm)
8. HEARTBEAT_ALIGNMENT_NOTE.md (reconciliation specs)
9. CORE_QUEUE_DELAYED_CLEANUP_SPEC.md (Core dependency)
10. README.md (quick start)

**Ready to copy into new chat!**
