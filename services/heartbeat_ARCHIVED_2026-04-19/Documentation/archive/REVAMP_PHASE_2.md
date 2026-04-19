# HeartBeat Revamp — Phase 2

**Date:** 2026-02-17
**Status:** PLANNED
**Depends On:** Phase 1 (COMPLETE — 242 tests, 92% coverage)
**Audience:** HeartBeat dev team, DevOps

---

## Phase 1 Recap (What's Already Done)

| Area | Status | What Was Built |
|------|--------|----------------|
| blob.db schema | DONE | 12 tables, 24+ indexes, seed data (14 blobs) |
| registry.db schema | DONE | 5 tables (service discovery, API credentials, config) |
| Filesystem blob storage | DONE | FilesystemBlobClient (replaced MinIO — no Docker needed) |
| Service registry API | DONE | 9 endpoints: register, discover, health, config, credentials |
| Blob write + register API | DONE | 2 endpoints matching Relay HeartBeatClient contract |
| Blob status API | DONE | GET/POST status for Float SDK + Core |
| Dedup, limits, audit, metrics | DONE | 6 endpoints (fire-and-forget for callers) |
| Legacy blob API | DONE | 3 endpoints (backward compat) |
| Credential handler | DONE | bcrypt keygen, rotate, revoke, validate lifecycle |
| Error hierarchy | DONE | 15 error classes, consistent JSON shape |
| Dev scripts | DONE | run_dev.bat/sh (filesystem-only, no Docker) |
| Tests | DONE | 242 tests, 92% coverage |
| SDK docs | DONE | HEARTBEAT_INTEGRATION.md v2.0 (all 23 endpoints) |

**What Phase 1 explicitly deferred:**
- Reconciliation engine (Phase 3 from original plan)
- SSE event streaming
- config.db tenant configuration
- License management
- Redis caching layer
- blob_outputs API
- /internal/refresh-cache push
- Prometheus metrics export
- Wazuh security monitoring
- SQL migration framework

---

## Phase 2 Scope

Phase 2 covers **six workstreams**:

| # | Workstream | Priority | New Endpoints | New Tables |
|---|---|---|---|---|
| P2-A | Prometheus Metrics Export | HIGH | 1 | 0 |
| P2-B | Wazuh Security Integration | HIGH | 1-2 | 1 |
| P2-C | SQL Migration Framework | HIGH | 0 | 1 |
| P2-D | SSE Event Streaming | MEDIUM | 1 | 0 |
| P2-E | Reconciliation Engine | MEDIUM | 3 | 0 |
| P2-F | Ancillary APIs | LOW | 4-6 | 0 |

---

## P2-A: Prometheus Metrics Export

### Why

HeartBeat already stores metrics in `metrics_events` (fire-and-forget from callers). But this is a raw event log — no aggregation, no time-series, no dashboards. Prometheus scrapes a `/metrics` endpoint and provides:
- Time-series storage (disk-efficient, 15s resolution)
- PromQL for alerting rules
- Grafana dashboards out of the box
- Alertmanager integration for PagerDuty/Slack notifications

### What to Build

#### A1. Add `prometheus_client` dependency

```
requirements.txt:
  prometheus_client>=0.20.0
```

#### A2. Create `src/observability/metrics.py`

Define Prometheus metrics (counters, histograms, gauges):

```python
# ── Counters (monotonically increasing)
blobs_uploaded_total          # Labels: source, source_type, content_type
blobs_registered_total        # Labels: source
blobs_status_changed_total    # Labels: from_status, to_status
dedup_checks_total            # Labels: result (duplicate/unique)
daily_limit_checks_total      # Labels: result (allowed/blocked)
audit_events_total            # Labels: service, event_type
credentials_operations_total  # Labels: action (created/rotated/revoked)
registry_operations_total     # Labels: action (register/discover/health)
api_errors_total              # Labels: error_code, endpoint

# ── Histograms (latency distribution)
blob_write_duration_seconds   # Labels: content_type
blob_register_duration_seconds
api_request_duration_seconds  # Labels: method, path, status_code

# ── Gauges (point-in-time values)
blobs_by_status               # Labels: status (uploaded/processing/finalized/error)
active_services               # Labels: service_name
storage_health                # 1=healthy, 0=unhealthy
database_health               # 1=healthy, 0=unhealthy
daily_usage_current           # Labels: company_id
```

#### A3. Create `src/api/observability/prometheus.py`

```python
# GET /metrics — Prometheus scrape endpoint
# Returns: text/plain (Prometheus exposition format)
# Auth: None (Prometheus scraper needs unauthenticated access)
# Rate: Scraped every 15s (configurable in prometheus.yml)
```

#### A4. Instrument existing handlers

Add `@observe_latency` decorator or explicit `.observe()` calls to:
- `blob_handler.write_blob()` → `blob_write_duration_seconds`
- `blob_handler.register_blob()` → `blob_register_duration_seconds`
- All API endpoints → `api_request_duration_seconds` (via FastAPI middleware)
- Error paths → `api_errors_total`

#### A5. Add FastAPI middleware for automatic request metrics

```python
# In main.py — add PrometheusMiddleware
# Auto-records: method, path, status_code, duration
# Excludes: /metrics, /health (avoid self-referential noise)
```

#### A6. Create `config/prometheus.yml` (reference config)

```yaml
# Reference Prometheus config for HeartBeat
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'heartbeat'
    static_configs:
      - targets: ['localhost:9000']
    metrics_path: '/metrics'

  # Future: Add relay, core, edge scrape targets
```

#### A7. Config additions

```python
# config.py
HEARTBEAT_METRICS_ENABLED: bool = True       # Enable /metrics endpoint
HEARTBEAT_METRICS_PREFIX: str = "heartbeat"  # Metric name prefix
```

#### A8. Tests (~8-10 new tests)

- Counter increments on blob upload, dedup check, error
- Histogram records latency for blob write
- Gauge updates on status change
- `/metrics` endpoint returns valid Prometheus exposition format
- Middleware captures request duration

---

## P2-B: Wazuh Security Integration

### Why

Wazuh is the security monitoring stack (SIEM) for Helium. It provides:
- Intrusion detection (file integrity monitoring)
- Security event correlation
- Compliance reporting (FIRS requires audit trails)
- Alerting on suspicious patterns (credential brute-force, unusual upload volumes)

HeartBeat is a prime target for Wazuh integration because it handles:
- API credential validation (failed auth attempts)
- File uploads (potential malware vectors)
- Service registration (unauthorized service detection)
- All audit events (centralized trail)

### What to Build

#### B1. Create `src/observability/wazuh.py`

Security event emitter that formats events for Wazuh ingestion:

```python
class WazuhEventEmitter:
    """
    Emits security-relevant events to Wazuh agent.

    Delivery: JSON log file at {WAZUH_LOG_PATH}/heartbeat_security.log
    Wazuh agent reads this log file and forwards to Wazuh manager.

    Alternative: Direct Wazuh API (POST /api/events) — for cloud deployments.
    """

    def emit_auth_event(self, event_type, api_key_prefix, success, ip_address, details)
    def emit_credential_event(self, action, credential_id, performed_by, reason)
    def emit_upload_event(self, blob_uuid, filename, file_hash, source, file_size)
    def emit_registration_event(self, instance_id, service_name, base_url, is_new)
    def emit_anomaly_event(self, anomaly_type, severity, details)
```

#### B2. Define security event schema

Every security event follows OCSF (Open Cybersecurity Schema Framework) structure:

```json
{
  "timestamp": "2026-02-17T10:30:00+00:00",
  "source": "heartbeat",
  "category": "authentication",
  "event_type": "auth.failed",
  "severity": "medium",
  "actor": {
    "api_key_prefix": "rl_test_",
    "ip_address": "192.168.1.100",
    "service_name": "relay"
  },
  "target": {
    "endpoint": "/api/blobs/write",
    "method": "POST"
  },
  "outcome": "failure",
  "reason": "Invalid API secret",
  "metadata": {
    "trace_id": "abc-123",
    "attempt_count": 3
  }
}
```

#### B3. Add security event table to blob.db

```sql
-- TABLE 13: security_events (Wazuh-bound security trail)
CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    timestamp_iso TEXT NOT NULL,
    timestamp_unix INTEGER NOT NULL,

    category TEXT NOT NULL,           -- "authentication", "authorization", "file_integrity", "registration"
    event_type TEXT NOT NULL,         -- "auth.failed", "auth.success", "credential.rotated", etc.
    severity TEXT NOT NULL,           -- "low", "medium", "high", "critical"

    actor_service TEXT,               -- Service that triggered the event
    actor_ip TEXT,                    -- Source IP
    actor_api_key_prefix TEXT,        -- First 8 chars of API key (for identification)

    target_endpoint TEXT,             -- API endpoint involved
    target_resource TEXT,             -- blob_uuid, credential_id, instance_id

    outcome TEXT NOT NULL,            -- "success", "failure", "blocked"
    reason TEXT,                      -- Human-readable explanation

    details TEXT,                     -- JSON blob of additional context
    wazuh_forwarded BOOLEAN DEFAULT 0, -- Has been sent to Wazuh

    created_at TEXT NOT NULL,

    CONSTRAINT severity_values CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT outcome_values CHECK (outcome IN ('success', 'failure', 'blocked'))
);

CREATE INDEX IF NOT EXISTS idx_security_events_category
    ON security_events(category, event_type);

CREATE INDEX IF NOT EXISTS idx_security_events_severity
    ON security_events(severity, timestamp_unix DESC);

CREATE INDEX IF NOT EXISTS idx_security_events_forwarded
    ON security_events(wazuh_forwarded)
    WHERE wazuh_forwarded = 0;
```

#### B4. Instrument security-relevant code paths

| Code Path | Event Category | Event Type | Severity |
|---|---|---|---|
| API key validation — success | authentication | auth.success | low |
| API key validation — failure | authentication | auth.failed | medium |
| API key validation — 3+ failures | authentication | auth.brute_force | high |
| Credential created | authorization | credential.created | low |
| Credential rotated | authorization | credential.rotated | low |
| Credential revoked | authorization | credential.revoked | medium |
| File uploaded | file_integrity | file.uploaded | low |
| File hash suspicious (known-bad list) | file_integrity | file.suspicious_hash | high |
| Unknown service registration attempt | registration | service.unknown | high |
| Service re-registration (URL changed) | registration | service.url_changed | medium |
| Daily limit exceeded | authorization | limit.exceeded | medium |

#### B5. Create Wazuh log writer

```python
# Writes JSON events to {WAZUH_LOG_PATH}/heartbeat_security.log
# Wazuh ossec.conf localfile rule reads this
# Format: one JSON object per line (JSONL)
# Rotation: logrotate handles file rotation
```

#### B6. Optional: Wazuh API forwarder (async background task)

```python
# For cloud deployments where Wazuh agent isn't local
# POST events to Wazuh manager API (wazuh_api_url)
# Batch: send 100 events or every 30s (whichever first)
# Mark security_events.wazuh_forwarded = 1 after success
```

#### B7. Config additions

```python
# config.py
HEARTBEAT_WAZUH_ENABLED: bool = False          # Enable Wazuh integration
HEARTBEAT_WAZUH_LOG_PATH: str = "/var/ossec/logs/active-responses/"
HEARTBEAT_WAZUH_API_URL: str = ""              # Optional direct API
HEARTBEAT_WAZUH_API_KEY: str = ""              # Optional API auth
HEARTBEAT_WAZUH_BATCH_SIZE: int = 100
HEARTBEAT_WAZUH_FLUSH_INTERVAL: int = 30       # seconds
```

#### B8. Create `config/wazuh_rules.xml` (reference Wazuh config)

```xml
<!-- Reference Wazuh rules for HeartBeat security events -->
<group name="heartbeat,">
  <rule id="100001" level="5">
    <decoded_as>json</decoded_as>
    <field name="source">heartbeat</field>
    <field name="event_type">auth.failed</field>
    <description>HeartBeat: Authentication failure</description>
  </rule>

  <rule id="100002" level="10" frequency="3" timeframe="300">
    <if_matched_sid>100001</if_matched_sid>
    <description>HeartBeat: Brute force attempt (3+ failures in 5 min)</description>
  </rule>

  <rule id="100003" level="7">
    <decoded_as>json</decoded_as>
    <field name="source">heartbeat</field>
    <field name="event_type">service.unknown</field>
    <description>HeartBeat: Unknown service registration attempt</description>
  </rule>
</group>
```

#### B9. Tests (~8-10 new tests)

- Security event emitted on auth failure
- Brute force detection after 3 failed attempts
- Credential lifecycle events logged
- File upload event recorded
- JSONL log format valid
- Wazuh forwarded flag toggled
- Unknown service registration flagged

---

## P2-C: SQL Migration Framework

### Why

HeartBeat has two databases (`blob.db` 12 tables, `registry.db` 5 tables). Right now, schema changes require:
1. Editing `schema.sql` / `registry_schema.sql`
2. Manually deleting the `.db` file
3. Restarting HeartBeat to recreate from scratch

This is fine for dev. In production, we need:
- **Forward-only migrations** (never lose data)
- **Version tracking** (which migrations have been applied)
- **Rollback scripts** (for failed deployments)
- **Multi-database support** (both blob.db and registry.db)

### What to Build

#### C1. Create migration tracking table

Added to BOTH databases during Phase 2 migration:

```sql
-- TABLE: schema_migrations (added to blob.db AND registry.db)
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    version TEXT NOT NULL UNIQUE,        -- "001", "002", ... or "2.0.1"
    name TEXT NOT NULL,                  -- "add_security_events_table"
    applied_at TEXT NOT NULL,            -- ISO-8601 timestamp
    applied_by TEXT NOT NULL,            -- "heartbeat-2.0.0" or "admin"
    checksum TEXT,                       -- SHA256 of migration SQL (drift detection)
    execution_time_ms INTEGER,           -- How long it took

    status TEXT NOT NULL DEFAULT 'applied',  -- "applied", "rolled_back", "failed"
    rollback_sql TEXT,                   -- SQL to reverse this migration (if possible)

    created_at TEXT NOT NULL
);
```

#### C2. Create `src/database/migrator.py`

```python
class DatabaseMigrator:
    """
    Forward-only SQL migration runner.

    Migration files: databases/migrations/{db_name}/{version}_{name}.sql

    Convention:
      - File: 001_add_security_events.sql
      - Inside file: SQL statements separated by ;
      - Optional rollback: -- ROLLBACK section at bottom

    Flow:
      1. Read schema_migrations table → get applied versions
      2. Scan migration directory → find unapplied
      3. Apply in order (001, 002, 003...)
      4. Record in schema_migrations
      5. On failure: log error, mark "failed", stop
    """

    def __init__(self, db, db_name: str, migrations_dir: str)
    def get_applied_versions(self) -> list[str]
    def get_pending_migrations(self) -> list[Migration]
    def apply_migration(self, migration: Migration) -> bool
    def apply_all_pending(self) -> MigrationResult
    def rollback_last(self) -> bool  # If rollback_sql exists
    def verify_checksums(self) -> list[DriftWarning]  # Detect modified migrations
```

#### C3. Create migration directory structure

```
databases/
├── schema.sql                          ← blob.db base schema (Phase 1)
├── seed.sql                            ← blob.db seed data
├── registry_schema.sql                 ← registry.db base schema (Phase 1)
├── registry_seed.sql                   ← registry.db seed data
└── migrations/
    ├── blob/
    │   ├── 001_add_schema_migrations.sql
    │   ├── 002_add_security_events.sql     ← P2-B
    │   ├── 003_add_processing_errors_index.sql
    │   └── ...
    └── registry/
        ├── 001_add_schema_migrations.sql
        ├── 002_add_config_encryption.sql
        └── ...
```

#### C4. Migration file format

```sql
-- Migration: 002_add_security_events
-- Database: blob
-- Date: 2026-02-17
-- Description: Add security_events table for Wazuh integration

CREATE TABLE IF NOT EXISTS security_events (
    -- ... (from P2-B)
);

CREATE INDEX IF NOT EXISTS idx_security_events_category
    ON security_events(category, event_type);

-- ROLLBACK
-- DROP TABLE IF EXISTS security_events;
```

#### C5. Wire into startup (main.py lifespan)

```python
# In lifespan():
#   After initializing blob DB:
#     migrator = DatabaseMigrator(blob_db, "blob", "databases/migrations/blob/")
#     result = migrator.apply_all_pending()
#     logger.info(f"Blob DB migrations: {result.applied} applied, {result.pending} pending")
#
#   After initializing registry DB:
#     migrator = DatabaseMigrator(reg_db, "registry", "databases/migrations/registry/")
#     result = migrator.apply_all_pending()
```

#### C6. Config additions

```python
# config.py
HEARTBEAT_AUTO_MIGRATE: bool = True           # Run migrations on startup
HEARTBEAT_MIGRATION_DIR: str = ""             # Override migration directory
```

#### C7. Tests (~10-12 new tests)

- Apply single migration
- Apply multiple migrations in order
- Skip already-applied migrations
- Detect checksum drift (modified migration file)
- Record execution time
- Handle failed migration (stop, mark as failed)
- Rollback last migration
- Migration on empty database
- Migration on existing database
- Both blob.db and registry.db migrate independently

---

## P2-D: SSE Event Streaming

### Why

Currently the SDK polls `GET /api/v1/heartbeat/blob/{uuid}/status` every few seconds. This wastes bandwidth and introduces latency (up to poll-interval delay before the UI updates). SSE (Server-Sent Events) provides:
- One-way push from HeartBeat → SDK (lighter than WebSocket)
- Automatic reconnection (built into EventSource API)
- HTTP-based (works through proxies, no special protocol)
- Text-based (easy to debug)

### What to Build

#### D1. Create `src/api/streaming/sse.py`

```python
# GET /api/v1/events/blobs
# Content-Type: text/event-stream
# Auth: Bearer token (validated once at connection)
#
# Events:
#   event: blob.status_changed
#   data: {"blob_uuid": "...", "status": "processing", "processing_stage": "extracting", "timestamp": "..."}
#
#   event: blob.registered
#   data: {"blob_uuid": "...", "original_filename": "WN42752.pdf", "timestamp": "..."}
#
#   event: blob.finalized
#   data: {"blob_uuid": "...", "timestamp": "..."}
#
#   event: blob.error
#   data: {"blob_uuid": "...", "error_message": "...", "timestamp": "..."}
#
#   event: heartbeat
#   data: {"timestamp": "..."}    ← Keepalive every 30s
```

#### D2. Create internal event bus

```python
# src/events/bus.py
class EventBus:
    """
    In-process pub/sub for SSE.

    Producers: blob_handler, status_handler, registry_handler
    Consumers: SSE endpoint (one per connected client)

    Uses asyncio.Queue per subscriber.
    Cleanup: remove subscriber queue on disconnect.
    """

    async def publish(self, event_type: str, data: dict)
    async def subscribe(self, event_types: list[str]) -> AsyncIterator[Event]
    def unsubscribe(self, subscriber_id: str)
```

#### D3. Instrument handlers to publish events

- `blob_handler.register_blob()` → publish `blob.registered`
- `status_handler.update_blob_status()` → publish `blob.status_changed`
- When status becomes "finalized" → publish `blob.finalized`
- When status becomes "error" → publish `blob.error`

#### D4. Config additions

```python
# config.py
HEARTBEAT_SSE_ENABLED: bool = True
HEARTBEAT_SSE_KEEPALIVE_SECONDS: int = 30
HEARTBEAT_SSE_MAX_CONNECTIONS: int = 100
```

#### D5. Tests (~6-8 new tests)

- SSE connection established
- Event published → received by subscriber
- Keepalive sent every interval
- Client disconnect → cleanup
- Multiple subscribers receive same event
- Auth validation on connection

### SDK Impact

SDK WS3 sync layer already has `WebSocketClient` and `PollingFallbackClient`. Add:
- `SSEClient` class that connects to `GET /api/v1/events/blobs`
- Use `EventSource` pattern (auto-reconnect with Last-Event-ID)
- On event → update local sync.db row
- Fallback to polling if SSE unavailable

**Note:** This is HeartBeat's blob event stream only. Core needs its own SSE stream for invoice/customer/product events — that's a Core task, not a HeartBeat task.

---

## P2-E: Reconciliation Engine

### Why

Phase 3 from the original plan. HeartBeat stores metadata in `blob.db` and files on filesystem. These can drift:
- **Orphaned files** — file on disk but no blob.db row (failed registration)
- **Missing files** — blob.db row but no file on disk (deleted externally)
- **Stuck processing** — status="processing" for >1 hour (Core crashed)
- **Expired retention** — retention_until_unix < now (should be cleaned up)
- **Batch inconsistencies** — batch says 3 files but only 2 exist

### What to Build

#### E1. Create `src/handlers/reconciliation_handler.py`

```python
class ReconciliationEngine:
    """
    5-phase reconciliation job.

    Phase 1: Compare blob.db entries ↔ filesystem files → detect orphans/missing
    Phase 2: Check stuck processing (status="processing" for >1 hour)
    Phase 3: Check expired retention (retention_until < now, not deleted)
    Phase 4: Validate batch integrity (file_count matches actual entries)
    Phase 5: Generate notifications for anomalies

    Runs: hourly (APScheduler) or on-demand via API
    """
```

#### E2. Create 3 API endpoints

```
POST /api/v1/heartbeat/reconciliation/trigger   — Manual run
GET  /api/v1/heartbeat/reconciliation/history    — Past runs
GET  /api/v1/heartbeat/notifications             — Unresolved alerts
```

#### E3. Wire APScheduler for periodic runs

```python
# In main.py lifespan:
#   scheduler = AsyncIOScheduler()
#   scheduler.add_job(reconciliation_engine.run, 'interval', hours=1)
#   scheduler.start()
```

#### E4. Config additions

```python
HEARTBEAT_RECONCILIATION_ENABLED: bool = True
HEARTBEAT_RECONCILIATION_INTERVAL_HOURS: int = 1
HEARTBEAT_STUCK_PROCESSING_TIMEOUT_MINUTES: int = 60
```

#### E5. Tests (~10-12 new tests)

- Detect orphaned file
- Detect missing file
- Detect stuck processing
- Detect expired retention
- Detect batch inconsistency
- Notification created for each anomaly
- Manual trigger via API
- History endpoint returns past runs

---

## P2-F: Ancillary APIs

### F1. blob_outputs API (Core's processing results)

```
POST /api/v1/heartbeat/blob/{uuid}/outputs      — Register processing output
GET  /api/v1/heartbeat/blob/{uuid}/outputs       — List outputs for a blob
GET  /api/v1/heartbeat/blob/{uuid}/outputs/{type} — Download specific output
```

Currently `blob_outputs` table exists but has no API. Core needs this to:
1. Register its outputs (FIRS JSON, reports) after processing
2. SDK needs to fetch these outputs for the user

### F2. /internal/refresh-cache (Push to Relay)

```
POST /internal/refresh-cache    — Push API key updates to Relay instances
```

Relay already has the receiver (`POST /internal/refresh-cache`). HeartBeat needs the sender — called when credentials are rotated or revoked, pushes new key data to all active Relay instances (discovered via registry).

### F3. config.db tenant configuration (future)

```
GET  /api/v1/config/{key}          — Read config
PUT  /api/v1/config/{key}          — Update config
```

Tenant-level configuration (FIRS settings, SMTP, branding). Separate database from blob.db and registry.db. **Lower priority** — needed when multi-tenant features are implemented.

### F4. License management (future)

```
GET  /api/v1/license               — Get current license info + tier
POST /api/v1/license/activate      — Activate license key
```

License determines tier (Standard/Pro/Enterprise) which controls daily limits, feature gates, and deployment topology. **Lower priority** — needed when commercial deployment begins.

---

## Schema Changes Summary

### blob.db changes (Phase 2)

| Change | Workstream | Type |
|---|---|---|
| Add `schema_migrations` table | P2-C | New table |
| Add `security_events` table | P2-B | New table |
| Add index on `processing_errors` | P2-C | Migration |

### registry.db changes (Phase 2)

| Change | Workstream | Type |
|---|---|---|
| Add `schema_migrations` table | P2-C | New table |

### All changes delivered as SQL migrations (P2-C)

No manual schema editing. All changes go through the migration framework:
```
databases/migrations/blob/001_add_schema_migrations.sql
databases/migrations/blob/002_add_security_events.sql
databases/migrations/registry/001_add_schema_migrations.sql
```

---

## New Dependencies

| Package | Version | Workstream | Purpose |
|---|---|---|---|
| `prometheus_client` | >=0.20.0 | P2-A | Prometheus metrics export |
| `apscheduler` | >=3.10.0 | P2-E | Scheduled reconciliation jobs |
| `sse-starlette` | >=1.6.0 | P2-D | FastAPI SSE support |

**No new infrastructure required.** Prometheus server and Wazuh manager are external — HeartBeat only exposes the endpoint / writes log files.

---

## New Files Summary

| File | Workstream | Purpose |
|---|---|---|
| `src/observability/metrics.py` | P2-A | Prometheus counter/histogram/gauge definitions |
| `src/observability/__init__.py` | P2-A | Package init |
| `src/api/observability/prometheus.py` | P2-A | GET /metrics endpoint |
| `src/observability/wazuh.py` | P2-B | Security event emitter + log writer |
| `src/database/migrator.py` | P2-C | SQL migration runner |
| `src/api/streaming/sse.py` | P2-D | SSE event stream endpoint |
| `src/events/bus.py` | P2-D | In-process pub/sub |
| `src/handlers/reconciliation_handler.py` | P2-E | 5-phase reconciliation engine |
| `src/api/internal/reconciliation.py` | P2-E | Reconciliation API endpoints |
| `src/api/internal/blob_outputs.py` | P2-F | blob_outputs CRUD endpoints |
| `config/prometheus.yml` | P2-A | Reference Prometheus config |
| `config/wazuh_rules.xml` | P2-B | Reference Wazuh rules |
| `databases/migrations/blob/*.sql` | P2-C | blob.db migrations |
| `databases/migrations/registry/*.sql` | P2-C | registry.db migrations |
| `tests/unit/test_prometheus.py` | P2-A | Metrics tests |
| `tests/unit/test_wazuh.py` | P2-B | Security event tests |
| `tests/unit/test_migrator.py` | P2-C | Migration framework tests |
| `tests/unit/test_sse.py` | P2-D | SSE streaming tests |
| `tests/unit/test_reconciliation.py` | P2-E | Reconciliation tests |

## Modified Files

| File | Changes |
|---|---|
| `requirements.txt` | Add prometheus_client, apscheduler, sse-starlette |
| `src/config.py` | Add Prometheus, Wazuh, SSE, migration, reconciliation config |
| `src/main.py` | Include new routers, start scheduler, run migrations in lifespan |
| `src/api/__init__.py` | Export new routers |
| `src/handlers/blob_handler.py` | Instrument with Prometheus + Wazuh + EventBus |
| `src/handlers/status_handler.py` | Instrument with Prometheus + EventBus |
| `src/handlers/credential_handler.py` | Instrument with Wazuh security events |
| `src/api/internal/registry.py` | Instrument with Wazuh (service registration events) |
| `src/database/connection.py` | Add migrator integration |
| `src/database/registry.py` | Add migrator integration |
| `databases/schema.sql` | Add comment noting migrations take over from here |

---

## Test Estimates

| Workstream | New Tests | Focus |
|---|---|---|
| P2-A Prometheus | 8-10 | Counter increments, histogram observations, /metrics format |
| P2-B Wazuh | 8-10 | Event emission, JSONL format, severity mapping, brute force detection |
| P2-C Migrations | 10-12 | Apply, skip, drift detect, rollback, multi-db |
| P2-D SSE | 6-8 | Connection, event delivery, keepalive, disconnect cleanup |
| P2-E Reconciliation | 10-12 | Each anomaly type, notifications, API endpoints, scheduler |
| P2-F Ancillary | 6-8 | blob_outputs CRUD, refresh-cache push |
| **Total** | **48-60** | |

**Projected totals after Phase 2: ~300 tests, 90%+ coverage.**

---

## Implementation Order

```
Week 1:  P2-C (Migrations)     ← Foundation, everything else depends on this
         P2-A (Prometheus)      ← Quick win, immediate observability

Week 2:  P2-B (Wazuh)          ← Uses migration framework for new table
         P2-D (SSE)             ← Unblocks SDK WS3 sync layer

Week 3:  P2-E (Reconciliation) ← Background engine + APScheduler
         P2-F (Ancillary)       ← Remaining API gaps
```

Migration framework (P2-C) must be done first because P2-B needs it to add the `security_events` table. Prometheus (P2-A) has zero dependencies and can be parallelized with P2-C.

---

## Verification Checklist

1. `pytest tests/ -v` — all ~300 tests pass
2. `pytest --cov=src tests/` — 90%+ coverage
3. `curl GET /metrics` — returns Prometheus exposition format
4. `curl POST /api/v1/events/blobs` — SSE stream connects
5. `curl POST /api/v1/heartbeat/reconciliation/trigger` — runs without error
6. Migration applies cleanly on fresh DB
7. Migration applies cleanly on existing Phase 1 DB (upgrade path)
8. Wazuh log file created with valid JSONL
9. `scripts\run_dev.bat` still starts cleanly
10. HEARTBEAT_INTEGRATION.md updated with new endpoints (v2.1)
