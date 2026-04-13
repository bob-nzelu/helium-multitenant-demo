# PHASE X: [STEP NAME] - IMPLEMENTATION GUIDE

**Phase:** X (of 8)
**Step:** [STEP NAME]
**Variant:** [HAIKU/SONNET/OPUS]
**Status:** 🔵 NOT YET STARTED

---

## PHASE PURPOSE

[One-line summary of what this phase does]

**Example (Phase 1):**
"PHASE 1: FETCH - Retrieve invoice files from blob storage and extract queue metadata"

---

## PHASE INPUTS

**Depends On:**
- ✅ Core/Documentation/CORE_CLAUDE.md (binding protocol)
- ✅ Core/Documentation/DECISIONS.md (architectural decisions)
- ✅ [Previous Phase X-1 Completion] (if applicable)

**Data Inputs:**
- [Where data comes from]
- [Format of input data]

**Example (Phase 1):**
- core_queue table (queue_id, file_uuid, blob_path)
- Blob storage (MinIO or S3)

---

## PHASE OUTPUTS

**Deliverables:**
- [What this phase produces]

**Data Outputs:**
- [Data passed to next phase]
- [Format of output]

**Example (Phase 1):**
- Raw file data (bytes)
- Metadata: {file_uuid, blob_path, original_filename, file_size}

---

## PHASE ARCHITECTURE

[ASCII diagram or description of what happens]

**Example (Phase 1):**
```
Input: core_queue entry (queue_id)
  ↓
1. Read core_queue table
  ├─ Extract: queue_id, file_uuid, blob_path, original_filename
  └─ Verify: Entry exists, status="pending"
  ↓
2. Fetch file from blob storage
  ├─ Read from blob_path
  ├─ Verify file integrity (if hash available)
  └─ Extract file bytes
  ↓
3. Update queue status
  ├─ Set status="processing"
  └─ Set updated_at timestamp
  ↓
Output: {raw_file_data, metadata}
  ↓
Pass to Phase 2
```

---

## PHASE DECISIONS

**Phase-Specific Decisions** (these override general DECISIONS.md if conflict):

| Decision | Value | Rationale |
|----------|-------|-----------|
| [Decision 1] | [Choice] | [Why] |
| [Decision 2] | [Choice] | [Why] |

**Example (Phase 1):**

| Decision | Value | Rationale |
|----------|-------|-----------|
| Queue polling interval | 60 seconds | Balances latency vs CPU usage |
| Max retries for blob fetch | 3 with exponential backoff | Handles transient network failures |
| File size limit | 100MB per file | Prevents memory exhaustion |
| Missing blob handling | Mark queue entry failed, alert | Indicates corrupted/deleted blob |

---

## PHASE API CONTRACTS

**Endpoints Implemented This Phase:**
- [List endpoints this phase implements]

**Example (Phase 1):**
- `GET /api/v1/health` (basic health check)

**Request/Response Formats:**

[Define Pydantic models, HTTP signatures, error codes]

**Example (Phase 1):**
```python
# Input
QueueEntry = {
    queue_id: str,
    file_uuid: str,
    blob_path: str,
    original_filename: str,
    created_at: datetime
}

# Output
FetchResult = {
    file_data: bytes,
    file_uuid: str,
    blob_path: str,
    original_filename: str,
    file_size: int,
    retrieved_at: datetime
}

# Error Codes
# PH1-001: Queue entry not found (404)
# PH1-002: Blob file not found (404)
# PH1-003: Blob read failed (500)
# PH1-004: File too large (400)
```

---

## PHASE WORKERS

**Workers Implemented This Phase:**

[List worker classes, responsibilities, concurrency model]

**Example (Phase 1):**
```python
class QueueScannerWorker:
    """
    Continuously scan core_queue for pending entries
    """
    - Polls core_queue every 60 seconds
    - Fetches "pending" entries
    - Passes to FileParserWorker
    - Retry backoff: exponential, max 3 attempts
    - Concurrency: 1 instance (singleton)
```

---

## PHASE IMPLEMENTATION CHECKLIST

### Before Coding
- [ ] Read Core/Documentation/CORE_CLAUDE.md (binding protocol)
- [ ] Read Core/Documentation/DECISIONS.md (architectural decisions)
- [ ] Read Core/Documentation/PHASE_X_DECISIONS.md (this file)
- [ ] Understand API contracts and expected signatures
- [ ] Understand input/output data formats
- [ ] Clarify any ambiguities with user

### Core Implementation
- [ ] [Component 1] - [Description]
- [ ] [Component 2] - [Description]
- [ ] [Component 3] - [Description]

**Example (Phase 1):**
- [ ] QueueScannerWorker - Polling loop + error handling
- [ ] BlobFetcher - Read from MinIO/S3 + retries
- [ ] QueueUpdater - Update core_queue status

### Testing (90%+ Coverage Required)
- [ ] Unit tests for [Component 1]
  - [ ] Happy path
  - [ ] Error handling
  - [ ] Edge cases
- [ ] Unit tests for [Component 2]
- [ ] Integration tests (between components)
- [ ] Error code tests (all error codes from API_CONTRACTS.md)
- [ ] Performance tests (verify latency targets)
- [ ] Coverage report (90%+ required)

### Documentation
- [ ] Code comments on complex logic
- [ ] Docstrings on all public functions
- [ ] README or implementation notes
- [ ] Update Core/Documentation/PHASE_X_STATUS.md (final report)

### Git Commits
- [ ] Commit each component when complete
- [ ] All tests passing before commit
- [ ] 90%+ coverage before commit
- [ ] Proper commit messages

### Quality Checks
- [ ] No hardcoded values (use config)
- [ ] No TODOs or FIXMEs
- [ ] Proper error handling
- [ ] Graceful degradation where applicable
- [ ] Logging on key operations
- [ ] No security issues

---

## PHASE DEPENDENCIES

**Phase Depends On:**
- ✅ Infrastructure (database, API framework)
- [Previous phases if applicable]

**Next Phase Depends On:**
- This phase

**Blocking Issues:** [List anything that would block this phase]

---

## PHASE PERFORMANCE TARGETS

**Latency Targets:**
- [Operation 1]: < [X]ms
- [Operation 2]: < [X]ms

**Throughput Targets:**
- [X] invoices/second
- [X] operations/second

**Example (Phase 1):**
- Queue scan: < 100ms per 1000 entries
- Blob fetch: < 5 seconds for 100MB file
- Throughput: 1000 queue scans per hour

---

## PHASE ERROR CODES

[Define all error codes with HTTP status, message, recovery action]

**Example (Phase 1):**

| Code | Status | Message | Recovery |
|------|--------|---------|----------|
| PH1-001 | 404 | Queue entry not found | Verify queue_id exists |
| PH1-002 | 404 | Blob file not found | Check blob_path, verify upload succeeded |
| PH1-003 | 500 | Blob read failed | Retry with exponential backoff |
| PH1-004 | 400 | File too large (>100MB) | Reject, ask user to split file |

---

## PHASE INTEGRATION POINTS

**Interfaces With:**
- [Phase X-1 output]: [How you consume it]
- [Phase X+1 input]: [How you produce it]
- [External service]: [How you call it]

**Example (Phase 1):**
- Infrastructure: Use core_queue table (read/write status)
- Phase 2: Pass raw_file_data + metadata
- Blob Storage: Fetch files from MinIO/S3

---

## PHASE TESTING STRATEGY

### Test Categories

1. **Unit Tests** - Test individual components
   ```python
   def test_queue_scanner_reads_pending_entries():
       # Mock core_queue table
       # Call QueueScannerWorker.scan()
       # Verify it fetches only "pending" entries
   ```

2. **Integration Tests** - Test between components
   ```python
   def test_full_fetch_flow():
       # Create test queue entry
       # Call fetch end-to-end
       # Verify file data + metadata returned
   ```

3. **Error Handling Tests** - Test all error codes
   ```python
   def test_blob_not_found_returns_404():
       # Mock blob storage to return 404
       # Verify error handling
   ```

4. **Performance Tests** - Verify latency/throughput
   ```python
   def test_fetch_1000_entries_under_100ms():
       # Create 1000 queue entries
       # Measure scan time
       # Verify < 100ms
   ```

---

## PHASE STATUS & PROGRESS

**Status:** 🔵 NOT YET STARTED

**Progress:** 0% (0/X components)

**Estimated Effort:** [X hours/days]

**Started:** [Date if started]

**Completed:** [Date if completed]

**Test Coverage:** [X%]

---

## PHASE NOTES

[Any additional notes, considerations, or known issues]

---

## NEXT STEPS

1. [Step 1]
2. [Step 2]
3. [Step 3]

---

**Last Updated:** [Date]
**Version:** [Version]
**Variant Assigned:** [HAIKU/SONNET/OPUS]

---

## HOW TO USE THIS DOCUMENT

1. **Claude Variant:** Read this document completely before starting Phase X
2. **User:** Customize decisions for your Phase X before sharing with variant
3. **Both:** Use this as contract - API contracts are binding, decisions can be discussed
