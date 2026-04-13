# Error Handling Decisions — 2026-03-30

Decided by Bob. These are binding — see CLAUDE.md Rule #4.

---

## EH-001: Duplicate File Status
**Decision**: SKIPPED (new terminal status)
**Rationale**: SDK and Relay flag duplicates before Core. If Core detects one, it's a belt-and-suspenders catch. SKIPPED is distinct from FAILED (not an error, just redundant).
**Action**: Add `SKIPPED` to core_queue status CHECK constraint. Set status=SKIPPED when dedup detects a match.

## EH-002: HeartBeat Blob Download Failure
**Decision**: 3 retries then FAILED
**Rationale**: Already implemented in heartbeat_client.py (3 attempts, exponential backoff). On final failure, mark queue entry as FAILED. User can manually retry.
**Action**: No code change needed — current behavior matches.

## EH-003: Queue Scanner Max Attempts
**Decision**: User notification + admin log
**Rationale**: User must know their upload failed. Admin needs ERROR-level logs for monitoring dashboards.
**Action**: After 3 failed scan attempts, call `notification_service.send()` to uploading user + `logger.error()`.

## EH-004: HIS Service Down
**Decision**: Continue degraded + red flag
**Rationale**: Already implemented — enrichment failure is non-fatal, adds `enrichment_failed` red flag. User sees warning in HLX preview.
**Action**: No code change needed — current behavior matches.

## EH-005: Partial Finalize Success
**Decision**: Commit what passes + warnings
**Rationale**: Don't block 50 valid invoices because 50 others have issues. User can fix and re-finalize the failures separately.
**Action**: Current finalize pipeline already supports this pattern. Ensure FinalizeResult includes per-invoice error details.

## EH-006: Per-Phase Timeouts
**Decision**: Overall 280s soft timeout only (for now)
**Rationale**: Per-phase timeouts add complexity. Each phase varies wildly by file size. Revisit after production data shows bottlenecks.
**Action**: No code change needed. Monitor phase_timings in audit logs to inform future per-phase limits.

## EH-007: Resource Limits
**Decision**: Enforce with sensible defaults
**Defaults**:
- Max file size: 50MB
- Max invoices per batch: 1000
**Configurable via**: `CORE_MAX_FILE_SIZE_MB` and `CORE_MAX_INVOICES_PER_BATCH`
**Enforcement point**: `/api/v1/enqueue` (reject at intake, don't waste pipeline resources)
**Action**: Add config fields + validation in ingestion router.
