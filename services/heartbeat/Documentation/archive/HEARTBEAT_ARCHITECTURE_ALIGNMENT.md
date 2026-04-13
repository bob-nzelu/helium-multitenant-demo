# HeartBeat Architecture - Complete Alignment Document

**Version:** 1.0
**Date:** 2026-02-01
**Status:** ✅ ALIGNED - Ready for Implementation
**Audience:** All Teams (HeartBeat, Core, Relay, Architecture)

---

## Table of Contents

1. [Core Principle](#core-principle)
2. [Parent-Client Model](#parent-client-model)
3. [Deployment Scenarios](#deployment-scenarios)
4. [Blob Storage Architecture](#blob-storage-architecture)
5. [Integration Points](#integration-points)
6. [Implementation Phases](#implementation-phases)
7. [Documentation Index](#documentation-index)

---

## Core Principle

### One HeartBeat Per Helium Installation

**Key Rule:**
> **Every Helium installation that has one or more services (Float, Edge, Core, any Relay) MUST have a HeartBeat instance.**

**HeartBeat is PER INSTALLATION, not:**
- ❌ Per location
- ❌ Per service type
- ❌ Per company department
- ❌ Per user

**Installation Examples:**
- ✅ Server running Relay-Bulk + Core + Edge → **One HeartBeat**
- ✅ Laptop running Float UI + embedded Relay-Bulk → **One HeartBeat**
- ✅ Branch server running Relay-Bulk + Core → **One HeartBeat**
- ✅ Docker host running all services → **One HeartBeat**

---

## Parent-Client Model

### When Parent-Client is Required

**Single Installation (Test/Standard):**
- ONE HeartBeat in **PARENT mode** (no clients needed)
- All services on same machine/Docker host
- blob.db and MinIO local to this installation

**Multiple Installations (Pro/Enterprise):**
- **FIRST installation** → HeartBeat in **PARENT mode**
- **All other installations** → HeartBeat in **CLIENT mode**
- Clients report to parent
- **ONE blob.db** (at parent only)
- **ONE MinIO** (at parent only)

### Parent HeartBeat Responsibilities

**Location:** Client's HQ server (NOT Prodeus, NOT external cloud)

**Owns:**
1. **blob.db** (single shared database)
2. **MinIO** (single shared storage)
3. **Deduplication** (across all client installations)
4. **External APIs** (for SIEM tools, company audits)
5. **Coordination** (all blob writes go through parent)

**Does:**
- Keep alive local services (Relay, Core, Edge at HQ)
- Accept blob registration from ALL clients
- Coordinate blob writes to shared blob.db
- Run reconciliation on shared MinIO
- Expose APIs for external tools (audit, SIEM)
- Aggregate health from all client installations
- Handle deduplication across all installations

**Exposes:**
- **Internal APIs:** For local Helium services at HQ
- **Coordination APIs:** For client HeartBeats to register blobs
- **External APIs:** For SIEM, audit tools, company-wide reports

### Client HeartBeat Responsibilities

**Location:** Every non-HQ Helium installation (branch server, Float laptop, etc.)

**Does:**
1. **Keep alive** local services (Relay, Core, Edge, Float)
2. **Expose internal APIs** for local Helium services
3. **Feed to parent:**
   - Blob registration requests
   - Health status
   - Service metrics
4. **Coordinate blob writes** through parent API (does NOT write directly to blob.db)
5. **Act as local parent** (does primary parent job for local services)

**Does NOT:**
- ❌ Own blob.db (parent owns it)
- ❌ Own MinIO (parent owns it)
- ❌ Expose external APIs (parent does this)
- ❌ Handle deduplication (parent does this)

### Auto-Detection

**On First HeartBeat Startup:**
```python
# HeartBeat auto-detects role

1. Check for existing parent (broadcast discovery or config)
2. If parent found:
   - Start in CLIENT mode
   - Register with parent
   - Report health to parent
3. If no parent found:
   - Start in PARENT mode
   - Initialize blob.db
   - Initialize MinIO
   - Listen for client registrations
```

**Manual Configuration (optional):**
```yaml
heartbeat:
  mode: "auto"  # or "parent" or "client"

  # If mode=client, parent URL required
  parent:
    url: "https://hq.execujet.com:9000"
    api_key: "${PARENT_API_KEY}"

  # If mode=parent, these are required
  storage:
    blob_db: "./databases/blob.db"
    minio_endpoint: "localhost:9001"
```

---

## Deployment Scenarios

### Scenario 1: Test/Standard (Single Installation)

```
┌────────────────────────────────────┐
│  Docker Host or Local Machine      │
│                                    │
│  Relay-Bulk                        │
│  Core                              │
│  Edge                              │
│  MinIO                             │
│                                    │
│  ┌──────────────────────────────┐  │
│  │ HeartBeat PARENT (only)      │  │
│  │                              │  │
│  │ • Keep alive all services    │  │
│  │ • blob.db (local)            │  │
│  │ • MinIO (local)              │  │
│  │ • Internal APIs              │  │
│  │ • External APIs (audit)      │  │
│  │ • No clients                 │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

**Configuration:**
```yaml
heartbeat:
  mode: "parent"  # or "auto" (will detect no clients)
  port: 9000
  storage:
    blob_db: "./databases/blob.db"
    minio_endpoint: "localhost:9001"
```

---

### Scenario 2: Pro/Enterprise (Multiple Installations)

```
EXECUJET COMPANY
┌──────────────────────────────────────────────────────────┐
│                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────┐ │
│  │ HQ Server      │  │ Branch Server  │  │ Laptop    │ │
│  │ Installation#1 │  │ Installation#2 │  │ Install#3 │ │
│  │                │  │                │  │           │ │
│  │ Relay-Bulk     │  │ Relay-Bulk     │  │ Float     │ │
│  │ Core           │  │ Core           │  │ Relay-Bulk│ │
│  │ Edge           │  │                │  │ (embedded)│ │
│  │ MinIO (SHARED) │  │                │  │           │ │
│  │                │  │                │  │           │ │
│  │ ┌────────────┐ │  │ ┌────────────┐ │  │ ┌───────┐│ │
│  │ │ HeartBeat  │◄├──┼─┤ HeartBeat  │ │  │ │HB     ││ │
│  │ │ PARENT     │ │  │ │ CLIENT     │ │  │ │CLIENT ││ │
│  │ │            │◄├──┼─┼────────────┘ │  │ └───────┘│ │
│  │ │• blob.db   │ │  │                │  │          │ │
│  │ │• MinIO     │ │  │ Reports to HQ  │  │ Reports  │ │
│  │ │• Dedup     │ │  │ ┌────────────┐ │  │ to HQ    │ │
│  │ │• External  │ │  │ │• Keep alive│ │  │┌────────┐│ │
│  │ │  APIs      │ │  │ │• Feed back │ │  ││• Float ││ │
│  │ │• Keep alive│ │  │ │• Coordinate│ │  ││  alive ││ │
│  │ │  (local HQ)│ │  │ │  blob write│ │  ││• Feed  ││ │
│  │ └────────────┘ │  │ └────────────┘ │  │└────────┘│ │
│  └────────────────┘  └────────────────┘  └───────────┘ │
│                                                          │
│  Parent at HQ:                                           │
│  • ONE blob.db (shared by all)                          │
│  • ONE MinIO (shared by all)                            │
│  • External APIs (for SIEM, audits)                     │
│  • Coordination (deduplication)                         │
└──────────────────────────────────────────────────────────┘
```

**Parent Configuration (HQ):**
```yaml
heartbeat:
  mode: "parent"
  port: 9000

  storage:
    blob_db: "./databases/blob.db"
    minio_endpoint: "localhost:9001"
    minio_access_key: "${MINIO_ACCESS_KEY}"
    minio_secret_key: "${MINIO_SECRET_KEY}"

  # Accept client registrations
  clients:
    auto_register: true

  # External APIs for SIEM/audit tools
  external_apis:
    enabled: true
    cors_origins:
      - "https://siem.execujet.com"
      - "https://audit.execujet.com"
```

**Client Configuration (Branch/Laptop):**
```yaml
heartbeat:
  mode: "client"
  port: 9000

  # Parent connection
  parent:
    url: "https://hq.execujet.com:9000"
    api_key: "${PARENT_API_KEY}"
    report_interval_hours: 1

  # Keep alive local services
  services:
    relay:
      url: "http://localhost:8082/health"
    core:
      url: "http://localhost:8080/api/v1/health"
```

---

## Blob Storage Architecture

### Single Shared Storage

**Critical:** There is **ONE blob.db** and **ONE MinIO** instance (at parent).

**All clients coordinate writes through parent:**

```python
# Client HeartBeat (branch/laptop)
# When local Relay uploads a file

# 1. Relay writes to MinIO (parent's MinIO, accessible via network)
relay.upload_to_minio(
    endpoint="hq.execujet.com:9001",
    file_data=file_data,
    blob_path="/files_blob/550e8400-...-invoice.pdf"
)

# 2. Client HeartBeat calls Parent to register blob
client_heartbeat.register_blob_with_parent(
    blob_uuid="550e8400-e29b-41d4-a716-446655440000",
    blob_path="/files_blob/550e8400-...-invoice.pdf",
    file_hash="abc123...",
    ...
)

# 3. Parent HeartBeat handles registration
parent_heartbeat.register_blob(
    # Check deduplication
    # Write to blob.db
    # Return 201 or 409
)
```

### Deduplication (Parent Only)

**Parent coordinates all blob writes:**
1. Client sends blob registration request to parent
2. Parent checks `blob_deduplication` table (in blob.db)
3. If duplicate found → Return 409 Conflict
4. If new → Insert into `blob_entries` and `blob_deduplication`
5. Return 201 Created

**Why parent handles deduplication:**
- Only parent has access to blob.db
- Ensures consistency across all installations
- Prevents race conditions

---

## Integration Points

### 1. Relay → HeartBeat (Client or Parent)

**When:** After MinIO write
**What:** Register blob

**If single installation (Test/Standard):**
```python
# Relay calls local HeartBeat (parent mode)
POST http://localhost:9000/api/v1/heartbeat/blob/register
```

**If multiple installations (Pro/Enterprise):**
```python
# Relay calls local HeartBeat CLIENT
POST http://localhost:9000/api/v1/heartbeat/blob/register

# Client HeartBeat forwards to PARENT
POST https://hq.execujet.com:9000/api/v1/heartbeat/blob/register
```

### 2. Client HeartBeat → Parent HeartBeat

**When:** Hourly (or on blob registration)
**What:** Report health, register blobs

```http
POST https://hq.execujet.com:9000/api/v1/client/report
{
  "installation_id": "branch-server-1",
  "timestamp": "2026-02-01T10:00:00Z",

  "health": {
    "relay": "healthy",
    "core": "healthy"
  },

  "blobs_registered_last_hour": 42
}
```

### 3. HeartBeat (Parent) → Core

**When:** Hourly reconciliation
**What:** Verify processing status

```http
GET http://localhost:8080/api/v1/core_queue/status
```

### 4. External Tools → HeartBeat (Parent)

**When:** Company audits, SIEM integration
**What:** Fetch audit logs, blob statistics

```http
# SIEM tool queries parent
GET https://hq.execujet.com:9000/api/v1/audit/logs?start_date=2026-01-01

# Audit tool queries blob stats
GET https://hq.execujet.com:9000/api/v1/blobs/statistics?location=all
```

---

## Implementation Phases

### ✅ Phase 1: Database Schema (COMPLETE)
- **Status:** Complete
- **Owner:** Haiku
- **Deliverable:** schema.sql, seed.sql, blob.db
- **Mode:** Parent only (no client yet)

### ✅ Phase 2: Blob Registration API (COMPLETE)
- **Status:** Complete
- **Owner:** Sonnet
- **Deliverable:** POST /api/v1/heartbeat/blob/register
- **Mode:** Parent only (clients use same API, forwarded to parent later)

### 🔄 Core Integration: Delayed Cleanup (SPEC READY)
- **Status:** Specification delivered to Core team
- **Owner:** Core team
- **Deliverable:** Per-entry 24h cleanup, status endpoint
- **Blocking:** HeartBeat Reconciliation

### 📅 Phase 3: HeartBeat Reconciliation (NEXT)
- **Status:** Pending implementation
- **Owner:** HeartBeat team (Sonnet)
- **Deliverable:** Hourly MinIO reconciliation
- **Mode:** Parent only (reconciles shared MinIO)

### 📅 Phase 4: Client Implementation (FUTURE)
- **Status:** Design phase
- **Owner:** HeartBeat team
- **Deliverable:**
  - Client mode support
  - Auto-detection (parent vs client)
  - Client → Parent reporting
  - Blob write forwarding
- **Mode:** Enable parent-client architecture

### 📅 Phase 5: External APIs (FUTURE)
- **Status:** Design phase
- **Owner:** HeartBeat team
- **Deliverable:**
  - Audit log APIs
  - SIEM integration endpoints
  - Company-wide reporting
- **Mode:** Parent exposes external APIs

---

## Documentation Index

### Architecture Documents
- **HEARTBEAT_ARCHITECTURE_ALIGNMENT.md** (this file) - Complete alignment
- **HEARTBEAT_OVERVIEW.md** - Service overview
- **PARENT_CLIENT_ARCHITECTURE.md** - Detailed parent-client design

### Implementation Specs
- **Services/HeartBeat/Documentation/HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md** - Phase 2 details
- **Services/Core/Documentation/CORE_QUEUE_DELAYED_CLEANUP_SPEC.md** - Core integration spec
- **Services/Core/Documentation/CORE_HANDOVER_BLOB_INTEGRATION.md** - Core team handover

### Original Planning
- **Blob/Documentation/** - Original planning docs (reference only)
- **Blob/PHASE_1_COMPLETION_REPORT.md** - Phase 1 summary
- **Blob/PHASE_1_HANDOFF_TO_PHASE_2.md** - Handoff document

### Code
- **Services/HeartBeat/src/** - Implementation
- **Services/HeartBeat/tests/** - Test suite
- **Services/HeartBeat/databases/** - Schema and seed data

---

## Key Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **One HeartBeat per installation** | Keeps services alive, provides local APIs |
| **First installation = parent** | Auto-detection, no manual configuration needed |
| **ONE blob.db (at parent)** | Single source of truth, consistent deduplication |
| **ONE MinIO (at parent)** | Shared storage, accessed by all installations |
| **Parent at client HQ** | NOT Prodeus, client controls their own data |
| **Clients coordinate through parent** | Deduplication, consistency, audit trail |
| **External APIs at parent only** | Simplifies SIEM integration, security |
| **Graceful degradation** | Clients work even if parent temporarily down |

---

## Status Summary

| Component | Status | Next Step |
|-----------|--------|-----------|
| **HeartBeat Phase 2** | ✅ Complete | Deploy, test |
| **Core Integration** | 📋 Spec Ready | Core team implements |
| **HeartBeat Reconciliation** | ⏭️ Next | Begin implementation |
| **Parent-Client Mode** | 📅 Future | Design phase |
| **External APIs** | 📅 Future | After client mode |

---

## Alignment Confirmation

### Are We Aligned?

- ✅ **One HeartBeat per Helium installation** (not per location)
- ✅ **Parent at client HQ** (not Prodeus)
- ✅ **ONE blob.db and ONE MinIO** (at parent, shared by all)
- ✅ **Clients coordinate blob writes** (through parent API)
- ✅ **Parent handles deduplication** (consistency)
- ✅ **Auto-detection** (first = parent, rest = clients)
- ✅ **External APIs at parent** (for SIEM, audits)

### Next Immediate Steps:

1. **Core team:** Implement delayed cleanup (1-2 days)
2. **HeartBeat team:** Begin reconciliation implementation (after Core ready)
3. **Architecture team:** Review and approve parent-client design
4. **Future:** Implement client mode support

---

**Document Version:** 1.0
**Last Updated:** 2026-02-01
**Status:** ✅ ALIGNED - Approved for Implementation

**Contact:**
- HeartBeat Team: For reconciliation and architecture questions
- Core Team: For integration questions
- Architecture Team: For design approval
