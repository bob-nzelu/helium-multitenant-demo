# HeartBeat Team Response to SDK / Float Team

**From:** HeartBeat Team
**To:** SDK / Float Team
**Date:** 2026-02-18
**Re:** Your HEARTBEAT_REGISTRY_NOTE.md + "What are the implementation notes?"

---

## Summary

We've produced a 3-part service contract (`HEARTBEAT_SERVICE_CONTRACT_PART1/2/3.md` in `HeartBeat/Documentation/`). This note extracts what's directly relevant to your team.

---

## 1. What's Ready NOW (Phase 1 — Already Implemented, 23 Endpoints)

These endpoints are live and tested (242 tests, 92% coverage):

| What You Need | Endpoint | Status |
|---|---|---|
| Register Float instance at startup | `POST /api/registry/register` | Ready |
| Get your config (tenant_id, paths) | `GET /api/registry/config/float-sdk` | Ready |
| Discover Relay URL | `GET /api/registry/discover/relay` | Ready |
| Poll blob upload status | `GET /api/v1/heartbeat/blob/{uuid}/status` | Ready |
| Report health | `POST /api/registry/health/{instance_id}` | Ready |
| Log audit events | `POST /api/audit/log` | Ready |
| Report metrics | `POST /api/metrics/report` | Ready |

**You can integrate against these today.**

---

## 2. Config Keys — Our Decision on Your Questions

### 2A. Naming Convention (Your Question 6A)

**Flat keys, scoped by service_name.** The `service_config` table already scopes by `service_name`, so we use simple key names:

| service_name | config_key | example_value |
|---|---|---|
| `_shared` | `tenant_id` | `pikwik-001` |
| `_shared` | `tier` | `standard` |
| `_shared` | `data_base_path` | `C:\HeliumData\pikwik-001` |
| `float-sdk` | `relay_url` | `http://localhost:8082` |

Tenant-wide keys go under `service_name = '_shared'`. Service-specific keys go under `service_name = 'float-sdk'`.

### 2B. Multi-Instance Config (Your Question 6B)

**Colon-separated instance qualifier:**

```
service_name = "float-sdk"                  → shared Float config
service_name = "float-sdk:0e008e8xy0"       → Workstation A instance config
service_name = "float-sdk:7f3a2b1cd9"       → Workstation B instance config
```

Your SDK calls:
```
GET /api/registry/config/float-sdk:0e008e8xy0
```

HeartBeat looks up `float-sdk:0e008e8xy0` first, then falls back to `float-sdk` for shared keys.

The `instance_id` lives under the instance-qualified name:

| service_name | config_key | value |
|---|---|---|
| `float-sdk:0e008e8xy0` | `instance_id` | `0e008e8xy0` |
| `float-sdk:0e008e8xy0` | `sync_db_path` | `C:\HeliumData\pikwik-001\sync_pikwik-001_0e008e8xy0.db` |

### 2C. SQLCipher (Your Question 6C)

**SDK receives decrypted values from the API.** You do NOT need SQLCipher. HeartBeat handles decryption server-side. When we implement SQLCipher (Phase 3), the API response will include the plaintext encryption key for your database files — you just pass it to `PRAGMA key='{key}'` after opening the connection.

For now: `is_encrypted = false` everywhere. No action needed from your side.

### 2D. Shared Config Keys (Your Question 6D)

**`_shared` service_name.** Keys that are tenant-wide (tenant_id, tier, data_base_path) live under `service_name = '_shared'`. Not duplicated per service.

Your resolution order stays exactly as you proposed:
1. HeartBeat config API → `GET /api/registry/config/float-sdk:{instance_id}`
2. Environment variable → `HELIUM_SYNC_DB_PATH`
3. Tier default → `/data/sync.db`

---

## 3. Database Catalog (Phase 2 — Not Ready Yet)

We're building a `database_catalog` table in a new `config.db`. This will give HeartBeat a registry of every database in the platform. Your naming convention is accepted:

```
sync_pikwik-001_0e008e8xy0.db      ← per-instance
core_queue_pikwik-001.db            ← per-tenant
```

**Phase 2 endpoints (not yet implemented):**
```
POST /api/databases/register                  — Register a database
GET  /api/databases/{tenant_id}               — List tenant's databases
GET  /api/databases/{tenant_id}/{db_name}     — Get specific database info
```

**Your action:** When these are ready, add `POST /api/databases/register` to your startup sequence (after `POST /api/registry/register`). We'll notify you when the endpoints are live.

---

## 4. What We Expect You to Tackle First

### Immediate (Can Start Now)

1. **Add HeartBeat config lookup to SDK startup** — call `GET /api/registry/config/float-sdk:{instance_id}` and `GET /api/registry/config/_shared` to resolve tenant_id, data_base_path, relay_url. Fall back to env vars if HeartBeat is unreachable.

2. **Register on startup** — call `POST /api/registry/register` with Float's instance_id, endpoints, and health URL.

3. **Health endpoint** — expose `GET /health` on your SDK/Float process. HeartBeat will poll this if you stop reporting health.

4. **Use `POST /api/audit/log` for significant events** — file_selected, preview_accepted, upload_started. Fire-and-forget (don't wait for response).

### After Phase 2 (We'll Tell You When)

5. **Register databases** — `POST /api/databases/register` for sync.db and core_queue.db at startup.

6. **Replace polling with SSE** — switch from `GET /blob/{uuid}/status` polling to `GET /api/v1/events/blobs` (Server-Sent Events). Your `EventProcessor` already supports callbacks — just wire it to the SSE stream.

7. **Config API migration** — when `config.db` endpoints are ready, migrate from `service_config` (registry.db) to `config_entries` (config.db). We'll handle backwards compatibility — both will work during transition.

---

## 5. What HeartBeat Provides on Our End

| Deliverable | Status | ETA |
|---|---|---|
| 23 Phase 1 endpoints (registry, blobs, credentials, audit, metrics) | Done | Available now |
| Config key seeding for your 3 keys (tenant_id, instance_id, data_base_path) | Done | Installer seeds these via `POST /api/registry/register` at install time |
| Multi-instance config lookup (colon-separated) | Needs implementation | Phase 2 Layer 2 |
| database_catalog table + API | Needs implementation | Phase 2 Layer 2 |
| SSE event stream | Needs implementation | Phase 2 Layer 3 |
| Prometheus /metrics | Needs implementation | Phase 2 Layer 1 |
| 3-part service contract documentation | Done | `HeartBeat/Documentation/HEARTBEAT_SERVICE_CONTRACT_PART1/2/3.md` |

---

## 6. Installer Responsibilities (Confirmed)

Your mental model is correct:

```
INSTALL TIME:
  Installer → generates instance_id (10-char alphanumeric)
           → creates data directory (C:\HeliumData\pikwik-001\)
           → pre-creates .db files from schema + seed
           → calls POST /api/registry/credentials/generate (gets api_key + api_secret)
           → seeds service_config (tenant_id, instance_id, data_base_path)
           → writes HELIUM_API_KEY, HELIUM_API_SECRET to Float's env

FIRST BOOT (safety net):
  SDK → calls GET /api/registry/config/float-sdk:{instance_id}
     → resolves sync.db path
     → if file missing: create from schema, log warning
     → proceed normally
```

---

## 7. Questions Back to You

1. **core_queue.db ownership:** Your note says Float SDK writes to `core_queue_pikwik-001.db`. Core processes it. Who creates the schema — Installer, SDK, or Core? We need to know for the database catalog registration.

2. **Health endpoint port:** Does Float's `GET /health` run on a specific port, or is it the same process as the PySide6 app? (HeartBeat needs a URL to poll.)

3. **Instance ID format:** You proposed 10-char alphanumeric (`0e008e8xy0`). We'll use this exactly. Just confirming — is this hex, base36, or arbitrary alphanumeric?

---

*Read the full contracts at `HeartBeat/Documentation/HEARTBEAT_SERVICE_CONTRACT_PART1.md` (registry, credentials, database catalog), `PART2.md` (DataBox, BulkContainer, SDK integration), `PART3.md` (observability, health, updates, lifecycle).*
