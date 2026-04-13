# Helium Audit Architecture Requirements

**Date:** 2026-03-31
**Status:** DRAFT — captures current state + requirements for future sessions
**Owner:** HeartBeat (central audit store)

---

## Current State (2026-03-31)

### Two Audit Stores

| Store | Service | Database | Table | Events |
|-------|---------|----------|-------|--------|
| **HeartBeat audit** | HeartBeat | blob.db (SQLite) | `audit_events` | Relay file ingestion, HeartBeat internal events, security events |
| **Core audit** | Core | PostgreSQL | `core.audit_log` | 26 event types: pipeline lifecycle, entity CRUD, finalization, system events |

These are logically linked by `trace_id` / `x_trace_id` but there is **no single query** to get the full lifecycle of an invoice from upload to FIRS transmission.

### Who Logs Where

| Service | Logs to HeartBeat `/api/audit/log` | Logs to own DB | UI actions logged? |
|---------|-----------------------------------|----------------|-------------------|
| **Relay** | YES (file.ingested) | No | N/A (no UI) |
| **Core** | NO | YES (core.audit_log, 26 events) | N/A (no UI) |
| **SDK/Float** | NO (method exists, never called) | No | NO |
| **HeartBeat** | Self (audit_events table) | YES | N/A (no UI) |
| **Edge** | NOT BUILT | NOT BUILT | N/A |

---

## Requirements

### R1: Edge Service MUST log FIRS transmission events to HeartBeat

When Edge is implemented, it MUST call `POST /api/audit/log` for:

| Event Type | Trigger | Details |
|------------|---------|---------|
| `firs.transmission.started` | Edge begins FIRS submission | invoice_id, irn, batch_id, endpoint (test/live) |
| `firs.transmission.success` | FIRS returns success | invoice_id, irn, firs_confirmation, response_time_ms |
| `firs.transmission.rejected` | FIRS returns rejection | invoice_id, irn, rejection_code, rejection_message |
| `firs.transmission.failed` | Network/auth failure | invoice_id, irn, error_type, retry_count |
| `firs.transmission.retried` | Automatic retry attempt | invoice_id, irn, attempt_number, delay_seconds |
| `firs.csid.renewed` | CSID token renewed | old_csid, new_csid, expires_at |
| `firs.certificate.updated` | FIRS certificate rotated | old_cert_hash, new_cert_hash |

Edge should also implement the webhook contract (`POST /api/v1/webhook/config_changed`) to receive config updates from HeartBeat.

### R2: Core Should Forward Critical Events to HeartBeat (Future)

For a unified audit view, Core should forward these high-value events to HeartBeat:

| Event Type | Current Location | Why Forward |
|------------|-----------------|-------------|
| `finalize.completed` | core.audit_log | HeartBeat needs to know invoice lifecycle completed |
| `finalize.failed` | core.audit_log | HeartBeat needs to know failures for reconciliation |
| `entity.updated` | core.audit_log | Customer/inventory changes affect multiple services |
| `entity.deleted` | core.audit_log | Soft-delete tracking for compliance |

**Decision needed:** Forward via HTTP (`POST /api/audit/log`) or via shared PostgreSQL schema?

### R3: SDK/Float Should Log User-Facing Actions (Future)

Float desktop app should audit:

| Event Type | Trigger | Details |
|------------|---------|---------|
| `ui.login` | User authenticates | user_id, machine_guid, method (password/pin) |
| `ui.invoice.viewed` | User opens invoice detail | invoice_id, user_id |
| `ui.invoice.exported` | User exports PDF | invoice_id, export_format |
| `ui.upload.initiated` | User starts bulk upload | file_count, total_size, user_id |
| `ui.settings.changed` | User modifies settings | setting_key, old_value, new_value |

### R4: Webhook Contract Implementation

Per `WEBHOOK_CONFIG_CONTRACT.md`, all services must implement:
- `POST /api/v1/webhook/config_changed` receiver
- Startup fetch from `GET /api/v1/heartbeat/config`
- In-memory config cache with webhook-triggered refresh

**Current implementation status:**

| Service | Webhook Receiver | Config Fetch | Status |
|---------|-----------------|--------------|--------|
| Core | YES | YES | DONE |
| Relay | NO | NO (only Transforma modules) | NEEDS WORK |
| SDK/Float | N/A (uses SSE) | NO (hardcoded) | SDK team wiring |
| Edge | NOT BUILT | NOT BUILT | Future |
| HIS | NOT BUILT | NOT BUILT | Future |

---

## Future: Unified Audit Query Layer

**Goal:** Single API to query the full invoice lifecycle across all services.

**Options:**
1. **HeartBeat as central store** — All services forward events to HeartBeat. Simple but creates a bottleneck and couples all services to HeartBeat availability.
2. **Shared PostgreSQL schema** — All services write to a shared `audit` schema in PostgreSQL. Core already uses PG. HeartBeat would need a PG writer for audit (already has `pg_connection.py`).
3. **Query federation** — HeartBeat exposes a unified `/api/audit/query` endpoint that fans out to Core + HeartBeat + Edge audit stores and merges results by trace_id. Most flexible but most complex.
4. **Dedicated session** — Hold a focused audit architecture session to design and implement. Recommended approach.

**Recommendation:** Option 4 — defer to a dedicated audit session once all services are operational. The current two-store approach works for development. Unification matters for production compliance (FIRS 7-year audit trail requirement).

---

## Trace ID Correlation

All services propagate trace IDs for cross-service correlation:

```
SDK user_trace_id (client-generated)
    → Relay x_trace_id (server-generated, propagated to all downstream)
        → Core x_trace_id (in audit_log entries)
        → HeartBeat x_trace_id (in file_entries + audit_events)
        → Edge x_trace_id (future: in transmission_attempts)
```

A unified query would JOIN on `x_trace_id` across all audit stores.
