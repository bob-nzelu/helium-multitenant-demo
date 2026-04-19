# HeartBeat Service - Complete Overview

**Service Name:** HeartBeat
**Version:** 1.0.0
**Status:** Phase 2 Complete, Reconciliation In Progress
**Last Updated:** 2026-02-01

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Service Responsibilities](#service-responsibilities)
3. [Architecture](#architecture)
4. [Implementation Phases](#implementation-phases)
5. [Parent-Client Model](#parent-client-model)
6. [API Endpoints](#api-endpoints)
7. [Database Schema](#database-schema)
8. [Integration Points](#integration-points)
9. [Deployment](#deployment)
10. [Documentation Index](#documentation-index)

---

## Executive Summary

**HeartBeat** is Helium's **service orchestration and shared resource management** layer. It provides:

1. **Blob Storage Management** (7-year FIRS compliance)
   - Blob registration API for Relay services
   - Hourly MinIO reconciliation
   - Retention policy enforcement
   - Lifecycle management (soft delete → hard delete)

2. **Health Monitoring** (future)
   - Service health checks (Relay, Core, Edge, Float)
   - Uptime tracking
   - Performance metrics
   - **Downtime Notifications (Pro/Enterprise):** For Helium Pro and Enterprise tenants,
     HeartBeat sends email/push notifications when any monitored server experiences
     service downtime. This ensures operations teams are alerted immediately to outages
     without needing to manually check dashboards.

3. **Shared Resources** (future)
   - audit.db (compliance audit trail)
   - notifications.db (system notifications)
   - config.db (system configuration)
   - usage.db (daily usage tracking)
   - license.db (license management)

4. **Service Orchestration** (future)
   - Cross-service communication
   - Event routing
   - Failure recovery

---

## Service Responsibilities

### Phase 2 (IMPLEMENTED ✅)

**Blob Registration API:**
- Accept blob registration calls from Relay services
- Validate blob metadata (UUID, path, hash, size)
- Create `blob_entries` records in blob.db
- Return 201 (success), 409 (duplicate), or 5xx (error)
- Support idempotent operations (safe to retry)

**Database:**
- Manage blob.db with 9 tables (from Phase 1)
- Track all blobs with 7-year retention policy
- Store blob metadata, status, and lifecycle events

### Reconciliation Phase (IN PROGRESS 🔄)

**Hourly MinIO Reconciliation:**
- Scan MinIO bucket every hour
- Find orphaned blobs (in MinIO, not in blob_entries)
- Verify processing status with Core
- Check soft-deleted blobs (24-hour recovery window)
- Detect unexpected deletions
- Cleanup old Core queue entries (>24 hours)

**Notifications:**
- Create alerts for reconciliation anomalies
- Track orphaned blobs, stale processing, unexpected deletions
- Severity levels: critical, warn, info

### Future Phases (PLANNED 📅)

**Health Monitoring:**
- Poll service health endpoints
- Track uptime and performance
- Alert on service failures

**Shared Resource Management:**
- Centralized audit logging
- System-wide notifications
- Configuration management
- Usage tracking and reporting
- License validation

---

## Architecture

### Deployment Model

HeartBeat uses a **parent-client architecture** for Pro/Enterprise tiers:

```
┌─────────────────────────────────────────────────┐
│                 ENTERPRISE                      │
│                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌────────┐│
│  │ Location A   │  │ Location B   │  │ Loc C  ││
│  │              │  │              │  │        ││
│  │ Relay        │  │ Relay        │  │ Relay  ││
│  │ Core         │  │ Core         │  │ Core   ││
│  │ Edge         │  │ Edge         │  │ Edge   ││
│  │ HeartBeat    │  │ HeartBeat    │  │ HB     ││
│  │ (CLIENT)     │  │ (CLIENT)     │  │ (CLI)  ││
│  └──────┬───────┘  └──────┬───────┘  └────┬───┘│
│         │                 │                │    │
│         └─────────────────┼────────────────┘    │
│                           │                     │
│              ┌────────────▼─────────────┐       │
│              │  PARENT HEARTBEAT        │       │
│              │  (Prodeus Server)        │       │
│              │  ├─ Aggregate health     │       │
│              │  ├─ Central dashboard    │       │
│              │  ├─ Global alerts        │       │
│              │  └─ Cross-location sync  │       │
│              └──────────────────────────┘       │
└─────────────────────────────────────────────────┘
```

**Client HeartBeat (at each location):**
- Manages local blob storage
- Runs hourly reconciliation
- Monitors local services
- Reports health to parent

**Parent HeartBeat (Prodeus server):**
- Aggregates health from all clients
- Global dashboard
- Cross-location reporting
- License management
- Central notifications

### Technology Stack

- **Framework:** FastAPI (REST API)
- **Server:** uvicorn (ASGI)
- **Database:** SQLite (blob.db)
- **Scheduling:** APScheduler (reconciliation jobs)
- **Storage:** MinIO (blob files)
- **Authentication:** Bearer tokens (Phase 2), JWT (future)

---

## Implementation Phases

### ✅ Phase 1: Database Schema (COMPLETE)

**Implemented by:** Haiku
**Status:** Complete
**Deliverables:**
- schema.sql (9 tables, 24 indexes)
- seed.sql (reference data)
- 27 test cases (100% pass rate)
- 92-95% code coverage

**Tables:**
- blob_entries (core tracking)
- blob_batches (multi-file uploads)
- blob_batch_entries (join table)
- blob_outputs (processed outputs)
- blob_deduplication (duplicate prevention)
- blob_access_log (analytics)
- blob_cleanup_history (compliance audit)
- notifications (reconciliation alerts)
- relay_services (reference data)

### ✅ Phase 2: Blob Registration API (COMPLETE)

**Implemented by:** Sonnet
**Status:** Complete
**Commit:** 2eabced
**Deliverables:**
- POST /api/v1/heartbeat/blob/register endpoint
- GET /api/v1/heartbeat/blob/{blob_uuid} endpoint
- GET /api/v1/heartbeat/blob/health endpoint
- Database connection module (thread-safe)
- 27 test cases (90%+ coverage)
- Complete documentation

**Location:** Services/HeartBeat/src/

**Integration:** Relay calls registration API after MinIO write

### 🔄 Core Integration: Delayed Queue Cleanup (SPEC DELIVERED)

**Owner:** Core team
**Status:** Specification created
**Document:** Services/Core/Documentation/CORE_QUEUE_DELAYED_CLEANUP_SPEC.md

**Requirements:**
- Stop deleting core_queue entries immediately
- Schedule per-entry deletion 24 hours after processing
- Add GET /api/v1/core_queue/status endpoint
- Support HeartBeat reconciliation verification

### 📅 Reconciliation Phase: Hourly MinIO Sync (NEXT)

**Owner:** HeartBeat team (Sonnet)
**Status:** Pending implementation
**Deliverables:**
- Reconciliation job (5 phases)
- APScheduler integration
- Notification system
- MinIO client integration
- Core API client
- Comprehensive tests (90%+ coverage)

**Reconciliation Phases:**
1. Find orphaned blobs (in MinIO, not in blob_entries)
2. Verify processing status with Core
3. Check soft-deleted blobs (24h recovery window)
4. Detect unexpected deletions
5. Cleanup old Core queue entries

---

## Parent-Client Model

### Client HeartBeat (Standard/Pro/Enterprise)

**Runs at:** Each Helium installation location

**Responsibilities:**
- Blob registration API (local)
- Hourly reconciliation (local MinIO)
- Health monitoring (local services)
- Local notifications
- Report health to parent (Pro/Enterprise only)

**Databases:**
- blob.db (local blob tracking)
- notifications.db (local alerts)
- audit.db (local audit trail)

**Configuration:**
```yaml
heartbeat:
  mode: "client"  # or "standalone" for Standard tier
  parent_url: "https://prodeus.example.com/heartbeat"  # Pro/Enterprise
  location_id: "execujet-location-a"
  reconciliation:
    enabled: true
    interval_hours: 1
```

### Parent HeartBeat (Enterprise Only)

**Runs at:** Prodeus central server

**Responsibilities:**
- Aggregate health from all clients
- Global dashboard
- Cross-location reporting
- Central license management
- Global alerts and notifications
- Analytics across all locations

**Databases:**
- locations.db (all client locations)
- global_health.db (aggregated metrics)
- licenses.db (license tracking)
- global_audit.db (cross-location audit)

**Configuration:**
```yaml
heartbeat:
  mode: "parent"
  clients:
    - location_id: "execujet-location-a"
      url: "https://location-a.execujet.com/heartbeat"
    - location_id: "execujet-location-b"
      url: "https://location-b.execujet.com/heartbeat"
```

**Client Reporting:**
```python
# Client HeartBeat reports to parent every hour
POST https://parent.heartbeat.com/api/v1/client/report
{
  "location_id": "execujet-location-a",
  "timestamp": "2026-02-01T10:00:00Z",
  "health": {
    "relay": "healthy",
    "core": "healthy",
    "edge": "degraded",
    "float": "healthy"
  },
  "blob_stats": {
    "total_blobs": 15234,
    "blobs_today": 42,
    "orphaned_blobs_found": 0,
    "reconciliation_duration_ms": 1234
  },
  "alerts": [
    {
      "severity": "warn",
      "message": "Edge service response time degraded"
    }
  ]
}
```

---

## API Endpoints

### Phase 2 Endpoints (IMPLEMENTED ✅)

#### POST /api/v1/heartbeat/blob/register

Register blob after MinIO write.

**Request:**
```json
{
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "blob_path": "/files_blob/550e8400-...-invoice.pdf",
  "file_size_bytes": 2048576,
  "file_hash": "abc123...",
  "content_type": "application/pdf",
  "source": "execujet-bulk-1"
}
```

**Response (201 Created):**
```json
{
  "status": "created",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob registered successfully"
}
```

**Response (409 Conflict):**
```json
{
  "status": "conflict",
  "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Blob already registered (duplicate blob_uuid)"
}
```

#### GET /api/v1/heartbeat/blob/{blob_uuid}

Get blob information.

**Response (200 OK):**
```json
{
  "blob_uuid": "550e8400-...",
  "blob_path": "/files_blob/550e8400-...-invoice.pdf",
  "status": "uploaded",
  "file_size_bytes": 2048576,
  "file_hash": "abc123...",
  "uploaded_at_iso": "2026-02-01T10:00:00Z",
  "retention_until_iso": "2033-02-01T10:00:00Z"
}
```

#### GET /api/v1/heartbeat/blob/health

Health check endpoint.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "service": "heartbeat-blob",
  "database": "connected",
  "blob_entries_count": 15234,
  "timestamp": "2026-02-01T10:00:00Z"
}
```

### Future Endpoints (PLANNED 📅)

- POST /api/v1/heartbeat/reconcile/trigger (manual reconciliation)
- GET /api/v1/heartbeat/reconcile/status (reconciliation status)
- GET /api/v1/heartbeat/health/services (all service health)
- POST /api/v1/client/report (client → parent reporting)
- GET /api/v1/dashboard/global (parent dashboard)

---

## Database Schema

### blob_entries (Core Table)

```sql
CREATE TABLE blob_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identity
    blob_uuid TEXT NOT NULL UNIQUE,
    blob_path TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,

    -- Source
    source TEXT NOT NULL,  -- FK to relay_services
    source_type TEXT,      -- Relay ingestion method: "bulk", "api", "polling", "watcher", "dbc", "email"

    -- File Metadata
    file_size_bytes INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    content_type TEXT,

    -- Processing State
    status TEXT NOT NULL,  -- "uploaded", "processing", "finalized", etc.

    -- Timestamps
    uploaded_at_unix INTEGER NOT NULL,
    uploaded_at_iso TEXT NOT NULL,

    -- Retention (7-year FIRS compliance)
    retention_until_unix INTEGER NOT NULL,
    retention_until_iso TEXT NOT NULL,

    -- Lifecycle
    deleted_at_unix INTEGER,
    deleted_at_iso TEXT,

    -- Audit
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by_service TEXT
);
```

**See:** `Services/HeartBeat/databases/schema.sql` for complete schema (9 tables)

---

## Integration Points

### Relay → HeartBeat

**When:** After successful MinIO write
**How:** POST /api/v1/heartbeat/blob/register
**Retry:** Exponential backoff (1s, 2s, 4s, 8s, 16s)
**Idempotent:** Returns 409 on duplicate (safe to retry)

### HeartBeat → Core

**When:** Hourly reconciliation
**How:** GET /api/v1/core_queue/status
**Purpose:** Verify processing status, detect stale entries

### HeartBeat → MinIO

**When:** Hourly reconciliation
**How:** MinIO client (list_objects, stat_object, remove_object)
**Purpose:** Verify blob existence, enforce retention, hard delete

### Client HeartBeat → Parent HeartBeat

**When:** Hourly (Pro/Enterprise only)
**How:** POST /api/v1/client/report
**Purpose:** Aggregate health, global dashboard, cross-location alerts

---

## Deployment

### Standard Tier (Float embedded)

```
Float (PySide6)
└── Embedded HeartBeat
    ├── Blob registration API (standalone)
    ├── Local reconciliation
    └── No parent connection
```

### Pro Tier (Docker)

```
docker-compose.yml:
  heartbeat:
    image: helium-heartbeat:1.0
    environment:
      - HEARTBEAT_MODE=client
      - HEARTBEAT_PARENT_URL=https://prodeus.example.com/heartbeat
      - HEARTBEAT_LOCATION_ID=execujet-pro-1
    ports:
      - "9000:9000"
    volumes:
      - ./data/blob.db:/app/databases/blob.db
```

### Enterprise Tier (Kubernetes)

**Client Deployment:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: heartbeat-client
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: heartbeat
        image: helium-heartbeat:1.0
        env:
        - name: HEARTBEAT_MODE
          value: "client"
        - name: HEARTBEAT_PARENT_URL
          value: "https://prodeus.example.com/heartbeat"
```

**Parent Deployment (Prodeus server):**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: heartbeat-parent
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: heartbeat
        image: helium-heartbeat:1.0
        env:
        - name: HEARTBEAT_MODE
          value: "parent"
```

---

## Documentation Index

### Core Documentation

- **HEARTBEAT_OVERVIEW.md** (this file) - Service overview
- **HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md** - Phase 2 implementation details
- **README.md** - Quick start guide

### Implementation Specs

- **Services/Core/Documentation/CORE_QUEUE_DELAYED_CLEANUP_SPEC.md** - Core integration spec
- **Services/General_Docs/RELAY_API_STANDARDIZATION_NOTICE.md** - Relay team notice

### Original Planning (Blob/)

- **Blob/Documentation/00_PHASE_NONNEGOTIABLE.md** - Binding protocol
- **Blob/Documentation/10_BLOB_SYNC_AND_HEARTBEAT_RECONCILIATION.md** - Reconciliation architecture
- **Blob/PHASE_1_COMPLETION_REPORT.md** - Phase 1 summary
- **Blob/PHASE_1_HANDOFF_TO_PHASE_2.md** - Phase 1→2 handoff

### API Documentation

- **Services/HeartBeat/src/api/register.py** - API implementation (inline docs)
- **HELIUM_OVERVIEW.md** - Technical standards section

---

## Status Summary

| Phase | Status | Owner | Deliverables |
|-------|--------|-------|--------------|
| **Phase 1: Database** | ✅ Complete | Haiku | schema.sql, seed.sql, tests |
| **Phase 2: Registration API** | ✅ Complete | Sonnet | API endpoints, tests, docs |
| **Core Integration** | 📋 Spec Ready | Core Team | Per-entry cleanup, status endpoint |
| **Reconciliation** | ⏭️ Next | Sonnet | Hourly sync, notifications |
| **Health Monitoring** | 📅 Future | TBD | Service health, uptime |
| **Parent-Client** | 📅 Future | TBD | Aggregation, global dashboard |

---

## Quick Start

### Run HeartBeat Service

```bash
cd Services/HeartBeat
python -m src.main
```

or

```bash
uvicorn src.main:app --host 0.0.0.0 --port 9000 --reload
```

### Test Registration API

```bash
curl -X POST http://localhost:9000/api/v1/heartbeat/blob/register \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "blob_path": "/files_blob/550e8400-...-invoice.pdf",
    "file_size_bytes": 2048576,
    "file_hash": "aaaa...",
    "content_type": "application/pdf",
    "source": "execujet-bulk-1"
  }'
```

### Run Tests

```bash
cd Services/HeartBeat
pytest tests/unit/test_heartbeat_register.py -v --cov=src
```

---

## Contact

**Questions about HeartBeat?**
- See documentation in `Services/HeartBeat/Documentation/`
- Submit GitLab issue for bugs
- Contact Helium Core Team for integration questions

**Implemented By:** Helium Core Team (Sonnet - Phase 2)
**Date:** 2026-02-01
**Version:** 1.0.0

---

**Last Updated:** 2026-02-01
**Document Version:** 1.0
**Status:** ✅ Complete and Current
