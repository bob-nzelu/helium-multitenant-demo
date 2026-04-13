# WS-ERROR-HANDLING — Comprehensive Error Handling, Recovery & Fallbacks

**Date:** 2026-03-25
**From:** Architecture session (Bob + Opus)
**To:** Error Handling team
**Status:** KICKSTARTER — The team will walk through each phase with Bob in their session to align on specifics. This document provides the landscape.
**Scope:** Every failure mode across Core's pipeline (WS1 → WS2 → WS3 → WS4 → WS5) + external service interactions

---

## MANDATORY READING

1. `Core/Documentation/CORE_CLAUDE.md` — Master protocol. Rule #0: ALWAYS RECOMMEND WHEN ASKING.
2. `Core/Documentation/DECISIONS_V2.md` — 25 binding decisions (includes graceful degradation rules)
3. `Core/src/errors.py` — 13 typed error codes. **This is your error taxonomy. Extend it, don't replace it.**
4. `Core/src/processing/circuit_breaker.py` — Existing circuit breaker (CLOSED/OPEN/HALF_OPEN)
5. `Core/src/ingestion/queue_scanner.py` — Existing retry logic (attempts, max_attempts, stale recovery)
6. `Core/src/processing/his_client.py` — Existing HIS stub with circuit breaker
7. Every source file in `Core/src/` — You need to understand the full pipeline before adding error handling

---

## QUESTIONS PROTOCOL

When you encounter ambiguity:
1. **ALWAYS recommend an answer and explain WHY**
2. Walk Bob through each failure scenario in the chat
3. Get Bob's decision before implementing
4. **Never ask a naked question** — see CORE_CLAUDE.md Rule #0

---

## ERROR TAXONOMY (from WS0 errors.py)

```python
CoreError                    # Base — all Core errors inherit from this
├── ValidationError          # 400 — Bad input, schema mismatch
├── NotFoundError            # 404 — Entity not found
├── DuplicateError           # 409 — Duplicate IRN, duplicate file hash
├── TimeoutError             # 504 — Pipeline, external service, scanner timeout
├── ExternalServiceError     # 502 — HeartBeat, HIS, Edge, IntelliCore down
├── DatabaseError            # 500 — PostgreSQL connection, query, transaction failures
├── PermissionDeniedError    # 403 — RBAC (future)
├── RateLimitedError         # 429 — Rate limit exceeded (future)
├── QuotaExceededError       # 429 — Tenant quota exceeded
├── SchemaMismatchError      # 422 — Schema version incompatible
├── StaleDataError           # 409 — Concurrent modification detected
├── CircuitOpenError         # 503 — Circuit breaker is open
└── InternalError            # 500 — Catch-all for unexpected failures
```

---

## PHASE-BY-PHASE FAILURE LANDSCAPE

### WS1: INGESTION — What Can Go Wrong

| Failure | Current Handling | Needed |
|---|---|---|
| HeartBeat blob download fails (HTTP 5xx) | `ExternalServiceError` raised | **Retry with backoff? Queue stays PENDING?** |
| HeartBeat blob download times out | `TimeoutError` raised | **How long to wait? What after timeout?** |
| HeartBeat blob not found (HTTP 404) | `NotFoundError` raised | **Mark queue entry FAILED? Notify user?** |
| File type unrecognized | Returns "unknown" | **Reject? Queue as FAILED? Accept with warning?** |
| Excel parser fails (corrupt file) | Exception in parser | **Mark FAILED? Retry? Store error detail?** |
| CSV encoding detection fails | Exception in parser | **Try common encodings? Fail?** |
| PDF parser fails | Exception in parser | **This is a stub — what about when IntelliCore is wired?** |
| .hlm file invalid JSON | Exception in HLM parser | **Mark FAILED with "Invalid .hlm format"?** |
| SHA256 dedup: file already processed | `DuplicateError` | **Mark FAILED? Mark as duplicate (different status)?** |
| Scanner: entry stuck in PROCESSING >5min | Stale recovery resets to PENDING | **How many retries before permanent FAILED?** |
| Scanner: database connection lost | `DatabaseError` | **Skip tick? Crash? Log and retry next tick?** |
| Queue: max_attempts (3) exceeded | Entry marked FAILED | **Notify user? Notify admin? Just log?** |

### WS2: PROCESSING — What Can Go Wrong

| Failure | Current Handling | Needed |
|---|---|---|
| Transforma script not found for tenant | Returns default script | **Is default script always safe? What does it produce?** |
| Transforma script execution fails | `TransformError` raised | **Full pipeline abort? Partial results?** |
| Transforma AST validation rejects script | `TransformError` | **Notify admin that script is broken?** |
| HIS classify returns low confidence (<0.60) | Red flag generated | **Block submission? Allow with warning?** |
| HIS service down | Circuit breaker opens | **Continue without enrichment? What fields are missing?** |
| HIS circuit breaker OPEN | `CircuitOpenError` | **Skip enrichment? Retry after recovery_timeout?** |
| Entity resolution: fuzzy match ambiguous (>1 match above threshold) | Takes best match | **Flag for user review? Take highest? Reject?** |
| Entity resolution: Levenshtein library missing | Falls back to difflib | **Is difflib good enough? Performance impact?** |
| Customer TIN format invalid | Red flag | **Block? Allow with warning?** |
| Transformation produces 0 invoices | Empty result | **Mark as FAILED? Or success with 0 results?** |
| Transformation produces >10,000 invoices | Large result | **Memory concern? Batch processing? Quota check?** |

### WS3: ORCHESTRATOR — What Can Go Wrong

| Failure | Current Handling | Needed |
|---|---|---|
| Pipeline timeout (overall) | Worker manager kills task | **What timeout? 5 min? 10 min? Configurable?** |
| Phase timeout (individual) | None currently | **Should each phase have its own timeout?** |
| HLX generation fails (pack_hlx error) | Exception | **Retry packing? Mark queue FAILED?** |
| HLX encryption fails | Exception | **Ship unencrypted? Mark FAILED?** |
| HeartBeat blob store fails (can't store .hlx) | `ExternalServiceError` | **Retry? Queue stays in PROCESSING?** |
| SSE publish fails | Exception in SSE manager | **Log and continue (fire-and-forget)?** |
| Worker pool exhausted (all workers busy) | Task waits | **Queue overflow? Reject new tasks? Backpressure?** |
| Pipeline crashes mid-phase | Unhandled exception | **Which phase? Can we resume from last phase?** |
| Memory pressure (large file + many invoices) | None | **OOM kill? Graceful degradation?** |
| Concurrent pipeline for same data_uuid | Possible if scanner retries | **Idempotency? Lock by data_uuid?** |

### WS4: ENTITY CRUD — What Can Go Wrong

| Failure | Current Handling | Needed |
|---|---|---|
| Concurrent update (stale data) | `StaleDataError` with optimistic locking | **Client retry? Merge? Last-write-wins?** |
| Soft delete of entity with active references | Allowed (soft delete) | **Cascade? Block? Warn?** |
| FTS index corruption | None | **Rebuild index? Fallback to LIKE queries?** |
| Bulk update exceeds transaction size | None | **Batch? Streaming? What's the limit?** |
| pg_notify payload too large | 8000 byte limit in PostgreSQL | **Truncate payload? Split events?** |

### WS5: FINALIZE — What Can Go Wrong

| Failure | Current Handling | Needed |
|---|---|---|
| IRN generation collision (duplicate) | IRNChecker callback | **Regenerate? Fail? How many retries?** |
| QR code generation fails | Exception | **Skip QR? Mark invoice as incomplete?** |
| Edge service down (can't queue for FIRS) | `ExternalServiceError` | **Keep in Core? Retry later? User notification?** |
| Edge queue rejects invoice | Validation error from Edge | **Surface to user? Auto-fix? Re-validate?** |
| Partial finalize (50 of 100 invoices succeed) | None | **Atomic? Partial success? Rollback?** |
| Database transaction fails during commit | `DatabaseError` | **Retry transaction? Mark FAILED?** |
| HLX versioning fails | Exception | **Old version still valid? Mark FAILED?** |
| Re-finalize of failed invoices still fails | Validation errors persist | **Max re-finalize attempts? Permanent failure?** |
| Fixed PDF stamping fails | Not built yet (WS5 supplementary) | **Skip overlay, finalize anyway (already decided)** |

### External Services — Cross-Cutting Failures

| Service | Failure Mode | Graceful Degradation (from DECISIONS_V2) |
|---|---|---|
| HeartBeat blob store | Down/timeout | **Queue stays PENDING. Scanner retries next tick.** |
| HeartBeat SSE | Connection lost | **Reconnect with exponential backoff.** |
| HIS (Pronalytics) | Down/timeout | **Continue without enrichment. Flag as MANUAL. Circuit breaker.** |
| IntelliCore | Down/timeout | **Continue without PDF extraction. Flag for manual review.** |
| Edge | Down/timeout | **Keep in Core queue. Auto-retry later. User notification.** |
| PostgreSQL | Connection lost | **Pool reconnect. Fail current request. Next request gets new connection.** |

---

## EXISTING RECOVERY MECHANISMS

### 1. Queue Scanner Retry (WS1)

```
Entry PENDING → picked up → PROCESSING → fails → attempt++ → back to PENDING
                                                            → if attempt >= 3 → FAILED
```

### 2. Stale Entry Recovery (WS1)

```
Entry stuck in PROCESSING > 5 minutes → reset to PENDING → retry
```

### 3. Circuit Breaker (WS2)

```
CLOSED → 5 consecutive failures → OPEN (reject immediately for 60s)
OPEN → 60s passes → HALF_OPEN (allow 1 request)
HALF_OPEN → success → CLOSED
HALF_OPEN → failure → OPEN (another 60s)
```

### 4. HeartBeat Reconciliation (Cross-Service)

```
HeartBeat scans blob_entries: status='uploaded' but no matching core_queue entry
→ HeartBeat calls Core: "You missed this blob"
→ Core creates queue entry → scanner picks it up
```

---

## AREAS THAT NEED DECISIONS (Walk through with Bob)

These are the open questions the team should discuss with Bob phase by phase:

### Atomicity vs Partial Success
- Should finalize be atomic (all-or-nothing) or allow partial success?
- If 50 of 100 invoices fail validation during finalize, what happens to the 50 that passed?
- Does the user see a partial .hlx with 50 successes and 50 failures?

### Retry Strategy
- Exponential backoff for external services? Or fixed interval?
- Max retries per phase? Per pipeline? Per day?
- Dead letter queue for permanently failed items?

### User Notification
- When should the user be notified of failures? Every error? Only permanent failures?
- Should Float show a toast for transient errors that auto-recover?
- Should the Notifications tab show "Upload processing delayed — HeartBeat temporarily unavailable"?

### Idempotency
- What happens if the same file is processed twice (scanner retry + original task both complete)?
- Is `data_uuid` + phase a sufficient idempotency key?
- Should each phase check "did I already run for this data_uuid"?

### Memory & Resource Limits
- Maximum file size Core will process? (Currently unbounded)
- Maximum invoices per upload? (Quota exists in Transforma but not enforced in Core)
- What happens when a 500MB Excel produces 50,000 invoices?

### Timeout Hierarchy
- Overall pipeline timeout: 10 minutes? Configurable per tenant?
- Per-phase timeouts: 60s for parse, 120s for transform, 60s for enrich, 30s for resolve?
- External service call timeout: 30s (current default)?

### Graceful Shutdown
- When Core receives SIGTERM (HeartBeat restarting it), what happens to in-flight pipelines?
- Cancel immediately? Finish current phase? Drain queue?
- How does the scanner know to stop picking up new work?

---

## DELIVERABLES

| # | Deliverable | Priority |
|---|---|---|
| 1 | Error handling audit: read every `except` block in WS1-WS5, document gaps | P0 |
| 2 | Retry policy document (per phase, per service) — aligned with Bob | P0 |
| 3 | Timeout configuration (overall + per-phase + per-service) | P0 |
| 4 | Dead letter handling for permanently failed items | P1 |
| 5 | Graceful shutdown implementation (SIGTERM handling) | P0 |
| 6 | Idempotency guards (prevent double-processing) | P0 |
| 7 | User-facing error messages (what Float shows for each failure class) | P1 |
| 8 | Resource limits (max file size, max invoices, memory guards) | P1 |
| 9 | Integration tests: simulate each failure mode and verify recovery | P0 |
| 10 | Error handling documentation (DECISIONS update + runbook) | P0 |

---

## HOW TO RUN THIS SESSION

1. **Read every source file** in `Core/src/` — understand the current error handling
2. **Walk through each phase with Bob** using the tables above
3. **For each failure mode**: What happens today? What should happen? Bob decides.
4. **Implement decisions** — add try/except, retries, timeouts, idempotency checks
5. **Write integration tests** — simulate each failure (mock HeartBeat down, mock HIS timeout, corrupt file, etc.)

**Do NOT make error handling decisions without Bob.** This is one of those sessions where alignment matters more than speed. Every decision affects what the user sees when things go wrong.

---

**Last Updated:** 2026-03-25
