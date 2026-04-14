# Helium Deployment Architecture

**Version:** 1.0
**Date:** 14 April 2026
**Status:** Canonical reference for all service handoffs
**Scope:** Multi-tenancy, auth, registration, Installer, updates, service topology

---

## 1. SERVICE TOPOLOGY

```
                        Internet / Tenant LAN
                                │
                          ┌─────▼─────┐
                          │   nginx    │  SSL termination, routing
                          └─────┬─────┘
                                │
        ┌───────────┬───────────┼───────────┬───────────┐
        │           │           │           │           │
   ┌────▼───┐  ┌───▼────┐  ┌──▼───┐  ┌───▼────┐  ┌──▼───┐
   │ Relay  │  │  Core  │  │ Edge │  │  HIS   │  │ SIS  │
   │ :8082  │  │ :8080  │  │:8085 │  │ :8500  │  │:8501 │
   └───┬────┘  └───┬────┘  └──┬───┘  └───┬────┘  └──────┘
       │           │           │           │
       └─────┬─────┴─────┬─────┘           │
             │           │                 │
        ┌────▼────┐  ┌──▼──────┐          │
        │HeartBeat│  │RabbitMQ │          │
        │  :9000  │  │ :5672   │          │
        └────┬────┘  └─────────┘          │
             │                             │
        ┌────▼─────────────────────────────┘
        │     PostgreSQL :5432 + Redis :6379
        └──────────────────────────────────
```

### Service Roles

| Service | Port | Role | Dependencies |
|---------|------|------|-------------|
| **HeartBeat** | 9000 | Auth, config, blob store, SSE relay, update engine | PostgreSQL |
| **Relay** | 8082 | Ingestion gateway, HMAC+JWT dual-auth | HeartBeat, Redis |
| **Core** | 8080 | Transformation, processing pipeline, invoice CRUD | PostgreSQL, HeartBeat, RabbitMQ |
| **Edge** | 8085 | FIRS submission proxy, transmission tracking | RabbitMQ, HeartBeat |
| **HIS** | 8500 | Intelligence: HS codes, addresses, classification | HeartBeat |
| **SIS** | 8501 | Anomaly detection, compliance scoring | HeartBeat |
| **RabbitMQ** | 5672 | Message broker: core_queue, edge_queue + DLQs | — |
| **PostgreSQL** | 5432 | All persistent data (tenant_id column scoping) | — |
| **Redis** | 6379 | Rate limiting, caching | — |
| **Simulator** | 8090 | Demo data generation (not deployed in production) | Relay, Core |

### Queue Architecture (RabbitMQ)

```
Relay ──HTTP──▶ Core ──publish──▶ core_queue ──consume──▶ Core (workers)
                Core ──publish──▶ edge_queue ──consume──▶ Edge

Dead Letter Queues (7-day TTL):
  core_queue.dlq ◀── failed core_queue messages
  edge_queue.dlq ◀── failed edge_queue messages
```

---

## 2. MULTI-TENANCY MODEL

**Pattern:** Single database, `tenant_id` column scoping (Option B).

Every table that stores tenant data has a `tenant_id TEXT NOT NULL` column. Every query includes `WHERE tenant_id = $1`. Services resolve tenant_id from:
- **JWT claims** (frontend apps): `tenant_id` in JWT payload
- **HMAC API key** (machine integrations): API key → tenant lookup via tenants.json
- **Service token** (internal): HeartBeat resolves tenant from service context

**In production (tenant-controlled):** Each tenant runs their own isolated stack. tenant_id exists but there's only one tenant per deployment. Multi-tenancy is for our demo/staging infrastructure where multiple tenants share one stack.

---

## 3. AUTHENTICATION

### 3.1 Two Auth Mechanisms

| Caller Type | Auth Method | Token Type | Validated By |
|-------------|-------------|------------|-------------|
| **Frontend apps** (Float, Reader, Mobile) | JWT | EdDSA Ed25519 (30 min, 8hr session cap) | HeartBeat introspect or local public key |
| **Machine integrations** (ERP, Simulator) | HMAC-SHA256 | API key + secret | Relay validates directly |
| **Internal services** | Service token | Static bearer token | HeartBeat validates |

### 3.2 Frontend Auth Flow

```
App Startup
  ├── Compute device_id = SHA256(machine_guid + ":" + mac_address)[:16]
  ├── Check OS Keyring (service="helium", account="session")
  │   ├── Found + valid → skip login → register app (if needed) → main window
  │   ├── Found + expired token → refresh → update Keyring → main window
  │   ├── Found + expired session → delete → login required
  │   └── Not found → login required
  │
  ├── Login: POST /api/auth/login {email, password, device_id}
  │   ├── is_first_run=true → forced password change → re-login
  │   └── is_first_run=false → write Keyring → main window
  │
  └── Register App: POST /api/auth/register-app {source_type, device_id, ...}
      └── Returns: source_id + tenant config + endpoints + capabilities
```

### 3.3 Session Sharing

**Same machine:** All Helium desktop apps (Float, Reader) share one Keyring entry. First app to authenticate writes it. Subsequent apps read it and skip login.

**Different devices:** Separate sessions. Max 3 concurrent sessions per user. 4th login evicts oldest.

### 3.4 JWT Timing

| Parameter | Value |
|-----------|-------|
| JWT lifetime | 30 minutes |
| Silent refresh | 25-minute client timer |
| Session hard cap | 8 hours (immutable from login) |
| Step-up freshness | Configurable per operation (default 5 min) |

### 3.5 Relay Dual-Auth

Relay accepts BOTH JWT and HMAC:
1. `Authorization: Bearer {jwt}` → verify via HeartBeat introspect → extract tenant_id from claims
2. `X-API-Key` + `X-Signature` → HMAC verification → tenant_id from tenants.json
3. Neither → 401

---

## 4. APP REGISTRATION & SOURCE IDENTITY

Every frontend app instance registers with HeartBeat and receives a **source_id** for audit traceability.

### Registration Endpoint

```
POST /api/auth/register-app
Authorization: Bearer {jwt}

{
  "source_type": "float",                    // float | transforma_reader | transforma_reader_mobile | monitoring
  "source_name": "Float_DESKTOP-PROBOOK",    // {AppType}_{computer_name}
  "app_version": "2.0.0",
  "machine_guid": "ABC123-DEF456",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "computer_name": "PROBOOK",
  "os_type": "windows",
  "os_version": "Windows 11 Pro",
  "device_id": "a1b2c3d4e5f60001"
}
```

### Registration Response

```json
{
  "source_id": "src-float-a1b2c3-001",
  "tenant": { "tenant_id", "company_name", "tin", "firs_service_id", "invoice_prefix" },
  "endpoints": { "heartbeat", "heartbeat_sse", "relay", "core", "core_sse" },
  "capabilities": { "can_upload", "can_finalize", "max_file_size_mb", "allowed_extensions" },
  "feature_flags": { "sse_enabled", "bulk_upload_enabled", "inbound_review_enabled" },
  "security": { "session_timeout_hours", "jwt_refresh_minutes", "step_up_required_for" }
}
```

**Idempotent:** Same device_id + source_type → returns existing registration (updates last_seen).

**No HMAC credentials in response.** Frontend apps use JWT for all service calls. HMAC credentials are only for machine integrations, provisioned by Installer.

### Every API Call Includes

```
Authorization: Bearer {jwt}
X-Device-Id: {device_id}
X-Source-Id: {source_id}
X-Trace-Id: {uuid7}
```

---

## 5. FRONTEND HARMONY (Float + Reader)

### 5.1 Shared Patterns

| Pattern | Float | Reader | Contract |
|---------|-------|--------|----------|
| **Keyring** | Writes session | Reads session | service="helium", account="session" |
| **device_id** | SHA256(machine_guid:mac) | Same computation | MUST be identical |
| **DPAPI session** | Writes to shared dir | Reads shared + own | `C:\ProgramData\Helium\sessions\` |
| **Auth overlays** | login_page.py + pin_setup | login_page.py + pin_setup | Identical UI, same HeartBeat calls |
| **PIN system** | bcrypt 12 rounds, Keyring | bcrypt 12 rounds, Keyring | Same constants, same decision tree |
| **Token refresh** | 25-min timer | 25-min timer | Both update same Keyring entry |
| **source_type** | `float` | `transforma_reader` | Different — HeartBeat returns app-specific config |

### 5.2 DPAPI Session (Windows)

Three-tier auth chain (Reader):
1. Try Reader's own DPAPI session (`~/.transforma/session.token.enc`)
2. Try shared session with Float (`C:\ProgramData\Helium\sessions\<user>.token.enc`)
3. Fall back to HeartBeat login

Float writes to the shared directory. Reader reads from it. DPAPI ensures only the same Windows user can decrypt.

### 5.3 Registration Storage

Each app stores its registration in its OWN local directory:
- Float: `~/.helium/float/registration.json`
- Reader: `~/.transforma/registration.json`

Registration data is per-app (different source_type → different capabilities).

---

## 6. INSTALLER (Tenant-Controlled Deployment)

### 6.1 Deployment Package

Pronalytics generates a deployment package via Admin_Packager:

```
helium-deploy-{tenant_id}-{version}.tar.gz
├── docker-compose.yml              — Full service stack
├── .env                            — Credentials, secrets (encrypted)
├── config/
│   ├── tenants.json                — Tenant HMAC credentials (for ERP integrations)
│   ├── schemas/                    — All SQL migrations (ordered)
│   └── rabbitmq/                   — Queue definitions
├── helium.yaml                     — Float/Reader client config (endpoints, tier, tenant)
├── installer.sh                    — Bootstrap script (Linux/Mac)
├── installer.ps1                   — Bootstrap script (Windows)
├── DEPLOYMENT.md                   — Instructions for tenant IT
└── checksums.sha256                — Package integrity verification
```

### 6.2 Bootstrap Flow

```bash
$ ./installer.sh

[1/8] Verifying package integrity... ✓
[2/8] Starting PostgreSQL + Redis + RabbitMQ... ✓
[3/8] Applying database schemas (14 migrations)... ✓
[4/8] Generating Ed25519 JWT keys... ✓
[5/8] Seeding first Owner user... ✓
[6/8] Starting HeartBeat... ✓ (healthy)
[7/8] Starting Relay + Core + Edge + HIS + SIS... ✓ (all healthy)
[8/8] Verifying service mesh... ✓

═══════════════════════════════════════════════════════
  Helium deployed successfully!
  
  HeartBeat: https://helium.{tenant}.local:9000
  First login: {owner_email} / {temp_password}
  
  Install Float on user machines using helium.yaml
═══════════════════════════════════════════════════════
```

### 6.3 Infrastructure Agnostic

The Installer does NOT assume AWS, Azure, or any cloud. Requirements:
- Docker Engine 24+
- Docker Compose v2+
- 4GB RAM minimum (8GB recommended)
- 20GB disk
- Network: outbound HTTPS (for FIRS API), inbound on configured ports

---

## 7. UPDATE ENGINE (HeartBeat-Managed)

HeartBeat is the single update authority. Two frontends deliver packages to it:

### 7.1 Float Admin Tool Path

```
Owner clicks "Check for Updates" in Float Admin dashboard
  → Float downloads update package from Pronalytics CDN (or receives via USB)
  → Float uploads to HeartBeat: POST /api/admin/updates/apply
  → HeartBeat validates package signature
  → HeartBeat shows update plan (what changes, estimated downtime)
  → Owner approves
  → HeartBeat applies: stop services → migrate schemas → pull images → restart
  → HeartBeat reports success/failure
  → Audit: "Owner Charles Omoakin approved update v2.1.4 at 2026-04-14T10:00:00Z"
```

### 7.2 Server-Side Script Path

```
IT receives update package (email, USB, secure download)
  → IT scans package (malware, integrity)
  → IT runs: ./helium-update.sh update-v2.1.4.tar.gz
  → Script uploads to HeartBeat: POST /api/admin/updates/apply
  → HeartBeat validates, applies, reports
  → Audit: "Server script applied update v2.1.4 at 2026-04-14T10:00:00Z"
```

### 7.3 Update API

```
POST /api/admin/updates/apply          — Upload + apply update package
GET  /api/admin/updates/status         — Current update progress
GET  /api/admin/updates/history        — Past updates (who, when, what)
POST /api/admin/updates/rollback       — Rollback to previous version
```

### 7.4 Safe Update Protocol

1. **Validate** package signature (Ed25519 signed by Pronalytics)
2. **Backup** current state (DB snapshot, current Docker image tags)
3. **Apply** schema migrations (forward-only, ordered)
4. **Pull** new Docker images (or load from package if offline)
5. **Restart** services one-by-one (rolling, health-checked)
6. **Verify** all services healthy
7. **Rollback** automatically if any service fails health check within 60s

---

## 8. TEST HARNESS (Developer Mode)

Physical file on developer's laptop (`~/.helium/test_harness_key`) enables privileged test operations. Server stores only SHA-256 hash of the key.

**Endpoints:** `/api/test/*` (auth/reset, data/seed, data/clear, pipeline/trigger, sse/emit, config/override)

**Activation:** `HEARTBEAT_TEST_HARNESS_ENABLED=true` env var on HeartBeat.

**Security:** Code is identical in dev and production. No key file = no test mode activates. HMAC-signed requests, constant-time validation, full audit logging.

See `UNIFIED_AUTH_CONTRACT.md` Section 8 for full spec.

---

## 9. CURRENT LIVE INFRASTRUCTURE

**EC2:** 13.247.224.147 (t3.medium, 4GB RAM, Ubuntu 24.04)

| Service | Port | Image | Status |
|---------|------|-------|--------|
| PostgreSQL | 5432 | postgres:16-alpine | healthy |
| Redis | 6379 | redis:7-alpine | healthy |
| RabbitMQ | 5672/15672 | rabbitmq:3.13-management-alpine | healthy |
| HeartBeat | 9000 | helium-multitenant-demo-heartbeat | healthy (MOCK_AUTH=true) |
| Relay | 8082 | helium-multitenant-demo-relay-api | healthy |
| Core | 8080 | helium-multitenant-demo-core | healthy (canonical) |
| Edge | 8085 | helium-multitenant-demo-edge | healthy (stub) |
| HIS | 8500 | helium-multitenant-demo-his | healthy (stub) |
| SIS | 8501 | helium-multitenant-demo-sis | healthy (stub) |
| Simulator | 8090 | helium-multitenant-demo-simulator | running |

**GitHub:** `https://github.com/bob-nzelu/helium-multitenant-demo`

**SSH:** `ssh -i helium-key.pem ubuntu@13.247.224.147`
