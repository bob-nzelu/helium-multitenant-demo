# HeartBeat Service Contract — Part 1: Registry, Credentials & Database Catalog

**Version:** 3.2
**Date:** 2026-02-23
**Status:** AUTHORITATIVE — supersedes all prior registry/credential documentation
**Audience:** All service teams (Core, Relay, HIS, Float/SDK, Edge)
**Changelog:** v3.2 — §3.8 rewritten as redirect to Part 4 (HeartBeat now owns auth fully). Header entry updated to reflect Pronalytics Limited ownership.
v3.1 — Added E2EE encryption section (§3.7), updated response shapes (§2.2), added user auth stub (§3.8), field renames (data_uuid, filenames[], call_type)

---

## 1. What HeartBeat Is

HeartBeat is the platform's **central infrastructure service**. It owns:

| Domain | What It Manages |
|---|---|
| **Service Registry** | Which services are running, their URLs, their endpoints |
| **API Credentials** | Inter-service authentication keys (bcrypt-hashed) |
| **Database Catalog** | Every database in the platform — names, paths, owners, credentials |
| **Blob Storage** | File lifecycle: upload → processing → preview → finalized |
| **Audit Trail** | Immutable INSERT-only event log for compliance |
| **Configuration** | Tier limits, feature flags, per-service config |
| **Encryption Keys** | E2EE key distribution for payload encryption (see §3.7) |

HeartBeat does **NOT**:
- Validate invoice content (that's Core)
- Submit to FIRS (that's Edge)
- Ingest files from users (that's Relay)
- Run business logic (that's Core + HIS)
- Track queue processing status (that's Core)

> **Note:** End-user authentication is now **fully owned by HeartBeat**.
> See Part 4 for the complete authentication, tenancy governance, and
> license enforcement contract. The previous statement "Authenticate end
> users (that's Core/Float)" in v3.1 is **superseded**.

---

## 2. Service Registration

### 2.1 On Startup: Every Service Registers

When any Helium service starts, it **must** call:

```
POST /api/registry/register
```

**Request:**
```json
{
    "service_name": "relay",
    "instance_id": "relay-bulk-001",
    "base_url": "http://localhost:8082",
    "health_url": "http://localhost:8082/health",
    "websocket_url": null,
    "tier": "standard",
    "endpoints": [
        {
            "path": "/api/ingest",
            "method": "POST",
            "description": "File ingestion (bulk and external)"
        },
        {
            "path": "/internal/refresh-cache",
            "method": "POST",
            "description": "Receive credential/config updates from HeartBeat"
        }
    ]
}
```

**Response (201):**
```json
{
    "status": "registered",
    "instance_id": "relay-bulk-001",
    "catalog": [
        {
            "service_name": "heartbeat",
            "instance_id": "heartbeat-primary-001",
            "base_url": "http://localhost:9000",
            "health_url": "http://localhost:9000/health",
            "endpoints": [
                {"path": "/api/blobs/write", "method": "POST"},
                {"path": "/api/dedup/check", "method": "GET"},
                ...
            ]
        },
        {
            "service_name": "core",
            "instance_id": "core-001",
            "base_url": "http://localhost:8080",
            ...
        }
    ]
}
```

**What you get back:** The full service catalog — every active service, its URL, and its endpoints. This is how services discover each other. **No hardcoded URLs in production.**

### 2.2 Discovery Without Registration

To look up a service without re-registering:

```
GET /api/registry/discover                    — Full catalog (all services)
GET /api/registry/discover/{service_name}     — Single service's instances + endpoints
```

### 2.3 Health Reporting

Services should periodically report their health:

```
POST /api/registry/health/{instance_id}
```

```json
{
    "status": "healthy",
    "uptime_seconds": 3600,
    "details": {
        "db_connected": true,
        "queue_depth": 42
    }
}
```

If a service stops reporting health, HeartBeat will actively check it (see Part 3: Heartbeat Polling).

---

## 3. API Credentials & Authentication

### 3.1 How Credentials Work

Every inter-service call is authenticated with an API key + secret pair.

**Key format:** `{2-letter-prefix}_{env}_{32-hex}`
- `rl_test_a1b2c3d4e5f6...` — Relay, test environment
- `cr_prod_9f8e7d6c5b4a...` — Core, production
- `fl_test_0e008e8xy0...` — Float SDK, test

**Secret:** 48-byte random token, bcrypt-hashed (12 rounds) before storage. **Plaintext returned only once at creation — never retrievable again.**

### 3.2 Credential Lifecycle

| Action | Endpoint | Who Calls It |
|---|---|---|
| **Generate** new key+secret | `POST /api/registry/credentials/generate` | Installer (at install time) |
| **Rotate** key+secret | `POST /api/registry/credentials/{id}/rotate` | Admin (periodic rotation) |
| **Revoke** a credential | `POST /api/registry/credentials/{id}/revoke` | Admin (decommission) |
| **List** credentials for a service | `GET /api/registry/credentials/{service_name}` | Admin (audit) |
| **Validate** on incoming request | Internal — `credential_handler.validate_api_key()` | HeartBeat (on every authenticated call) |

### 3.3 Generate Credentials (Installer Flow)

```
POST /api/registry/credentials/generate
```

```json
{
    "service_name": "float-sdk",
    "environment": "test",
    "permissions": "read,write",
    "expires_in_days": 365
}
```

**Response (201):**
```json
{
    "credential_id": "cred-a1b2c3",
    "api_key": "fl_test_0e008e8xy0ab1234567890abcdef1234",
    "api_secret": "Kx8mP2qR...48-char-plaintext...",
    "status": "active",
    "expires_at": "2027-02-18T00:00:00Z"
}
```

**IMPORTANT:** The `api_secret` is shown **once**. Store it in the service's env vars or secure config. HeartBeat only stores the bcrypt hash.

### 3.4 How Services Authenticate to HeartBeat

Every request to HeartBeat (except `/health`, `/metrics`) must include:

```
Authorization: Bearer {api_key}:{api_secret}
```

HeartBeat validates:
1. Look up `api_key` in `api_credentials` table
2. bcrypt-verify `api_secret` against stored hash
3. Check `status` is `active`
4. Check `expires_at` is in the future
5. Stamp `last_used_at`

On failure → 401 with `{"error_code": "AUTH_FAILED", "message": "..."}`.

### 3.5 How Services Authenticate to Each Other

For service-to-service calls that don't go through HeartBeat (e.g., Float → Relay upload):

| Caller → Target | Auth Method | Credential Source |
|---|---|---|
| Float → Relay | HMAC-SHA256 (`X-API-Key`, `X-Timestamp`, `X-Signature`) | API key+secret from HeartBeat credential generation |
| Relay → HeartBeat | Bearer token (`Authorization: Bearer key:secret`) | Same credential |
| Core → HeartBeat | Bearer token | Same credential |
| HIS → HeartBeat | Bearer token (`Authorization: Bearer {HIS_HEARTBEAT_TOKEN}`) | Same credential |
| HeartBeat → Relay | Bearer token (`Authorization: Bearer {INTERNAL_SERVICE_TOKEN}`) | Shared internal token |

**HMAC-SHA256 Scheme (CANONICAL)**

This is the authoritative definition of the HMAC scheme used for Float → Relay communication. All implementations MUST use this exact scheme.

```
body_hash  = SHA256(request_body_bytes)
message    = "{api_key}:{timestamp}:{body_hash}"
signature  = HMAC-SHA256(api_secret_bytes, message_bytes)
```

**Required headers:**

| Header | Description | Example |
|---|---|---|
| `X-API-Key` | Client API key | `fl_test_0e008e8xy0ab1234...` |
| `X-Timestamp` | ISO 8601 UTC timestamp | `2026-02-19T10:00:00Z` |
| `X-Signature` | Hex-encoded HMAC-SHA256 | `a1b2c3d4e5f6...` |

**Verification (server side):**
1. Check `X-Timestamp` is within 5-minute window (replay prevention)
2. Look up `api_secret` for the given `X-API-Key`
3. Read raw request body bytes
4. Compute `body_hash = SHA256(raw_body)`
5. Compute `expected = HMAC-SHA256(api_secret, "{api_key}:{timestamp}:{body_hash}")`
6. Constant-time comparison of `X-Signature` against `expected`

On failure → 401 with `{"error_code": "AUTHENTICATION_FAILED", "message": "..."}`.

> **WARNING**: The Float SDK's current `relay_client.py` uses a DIFFERENT signing scheme (`timestamp + raw_body`). This is WRONG per this contract. The SDK must be updated to use the canonical scheme above.

**The Installer seeds all credentials at install time.** Services receive their key+secret via env vars.

### 3.6 Credential Push (After Rotation)

When HeartBeat rotates a credential, it pushes the update to affected services:

```
POST /internal/refresh-cache
```

This calls every active Relay instance's `/internal/refresh-cache` endpoint (discovered via registry). Relay hot-reloads the new credentials without restart.

### 3.7 End-to-End Payload Encryption (E2EE)

HeartBeat defines and owns the payload encryption protocol used for data in transit between Float SDK and Relay. This provides confidentiality beyond TLS — protecting against compromised intermediaries and at-rest inspection.

#### 3.7.1 Protocol: NaCl X25519 + XSalsa20-Poly1305

The encryption uses NaCl "Box" construction:
- **Key exchange**: X25519 (Curve25519 Diffie-Hellman)
- **Symmetric encryption**: XSalsa20 stream cipher
- **Authentication**: Poly1305 MAC (authenticated encryption)

This is implemented via `PyNaCl` (Python binding of `libsodium`).

#### 3.7.2 Wire Format

Encrypted payloads use this binary format:

```
[1 byte: version][32 bytes: ephemeral_public_key][N bytes: encrypted_payload]
```

| Field | Size | Description |
|---|---|---|
| `version` | 1 byte | Protocol version. Currently `0x01`. |
| `ephemeral_public_key` | 32 bytes | Sender's ephemeral X25519 public key (per-request, one-time use) |
| `encrypted_payload` | Variable | NaCl Box ciphertext (includes Poly1305 MAC) |

#### 3.7.3 Encryption Flow (Client → Relay)

```
1. Client (Float SDK) generates ephemeral X25519 keypair
2. Client loads Relay's static public key (from HeartBeat config)
3. Client computes shared secret: NaCl.Box(ephemeral_private, relay_public)
4. Client encrypts request body: ciphertext = box.encrypt(plaintext)
5. Client constructs wire format: [0x01][ephemeral_public][ciphertext]
6. Client sends with header: X-Encrypted: true
```

```
1. Relay reads X-Encrypted header
2. Relay parses wire format: version, ephemeral_public, ciphertext
3. Relay loads its static private key
4. Relay computes shared secret: NaCl.Box(relay_private, ephemeral_public)
5. Relay decrypts: plaintext = box.decrypt(ciphertext)
6. Relay proceeds with plaintext body (HMAC verification happens on decrypted body)
```

#### 3.7.4 Key Distribution

| Key | Owner | Storage | Distribution |
|---|---|---|---|
| Relay static keypair | HeartBeat generates at install time | Private key: Relay env var (`RELAY_PRIVATE_KEY_PATH`) or in-memory (dev). Public key: HeartBeat `service_config`. | Public key served via `GET /api/registry/config/relay` |
| Ephemeral keypair | Client (Float SDK) | Generated per-request, never stored | Public key embedded in wire format |

**Relay's public key lifecycle:**
1. Installer generates X25519 keypair via `nacl.public.PrivateKey.generate()`
2. Private key → written to file referenced by `RELAY_PRIVATE_KEY_PATH`
3. Public key (32 bytes, base64-encoded) → stored in HeartBeat `service_config`:
   ```
   service_name: "relay"
   config_key: "encryption_public_key"
   value: "base64-encoded-32-bytes"
   ```
4. Float SDK fetches public key at startup: `GET /api/registry/config/relay` → reads `encryption_public_key`
5. On key rotation: HeartBeat updates config → pushes `POST /internal/refresh-cache` to all Relay instances → Float SDK re-fetches on next startup

#### 3.7.5 Headers

| Header | Value | Meaning |
|---|---|---|
| `X-Encrypted` | `true` | Request body is encrypted per this protocol |
| `X-Encrypted` | `false` (or absent) | Request body is plaintext |

#### 3.7.6 Enforcement Configuration

| Setting | Env var | Default | Description |
|---|---|---|---|
| Require encryption | `RELAY_REQUIRE_ENCRYPTION` | `true` | When `true`, Relay rejects requests without `X-Encrypted: true` with `403 ENCRYPTION_REQUIRED` |
| Private key path | `RELAY_PRIVATE_KEY_PATH` | `""` | Path to Relay's static private key file. Empty = auto-generate ephemeral key (dev/test only) |

**Production**: `require_encryption=true`, static key from file.
**Development**: `require_encryption=false`, ephemeral key auto-generated.

#### 3.7.7 Implementation Status

| Component | Status |
|---|---|
| Relay decryption (`src/crypto/envelope.py`) | **Implemented** — full encrypt/decrypt with wire format |
| Float SDK encryption | **NOT implemented** — must be built |
| Key distribution via HeartBeat config | **NOT implemented** — config table exists, key entry does not |
| Key rotation workflow | **NOT implemented** — designed, not built |

### 3.8 User Authentication

> ⚠️ **This section is superseded by Part 4.**
>
> As of v3.2, HeartBeat **fully owns** all user authentication, tenancy
> governance, license enforcement, and enrollment. The previous stub in
> this section (which assigned auth to Core/Float) is no longer correct.
>
> **See:** `HEARTBEAT_SERVICE_CONTRACT_PART4.md` — the authoritative
> reference for:
> - JWT issuance and the replacement of `X-User-ID`
> - Role hierarchy (Owner, Admin, Operator, Support)
> - Step-up authentication and session policies
> - SSE stream authentication and filtering
> - Token introspection for service-to-service verification
> - Tenancy enrollment (every new component activates through HeartBeat)
> - License enforcement (Ed25519-signed, fully offline)
> - All-Father key (Pronalytics emergency override)
> - Service lifecycle management (process restart, OS-level startup)
> - auth.db schema
> - Full auth API endpoint reference
>
> **`X-User-ID` header is deprecated.** Services must migrate to JWT.
> See Part 4 §1 for migration guidance and the canonical JWT model.
>
> **Rule:** Part 4 supersedes any auth-related content in Parts 1, 2, or 3.

---

## 4. Per-Service Configuration

### 4.1 Existing: service_config (registry.db)

The `service_config` table in `registry.db` stores key-value pairs scoped by service name:

```
GET /api/registry/config/{service_name}
```

**Response:**
```json
{
    "service_name": "float-sdk",
    "config": {
        "tenant_id": "pikwik-001",
        "instance_id": "0e008e8xy0",
        "data_base_path": "C:\\HeliumData\\pikwik-001",
        "tier": "standard",
        "relay_url": "http://localhost:8082"
    }
}
```

### 4.2 Config Key Naming Convention

All config keys use **flat names** scoped by `service_name`:

| service_name | config_key | value | Notes |
|---|---|---|---|
| `_shared` | `tenant_id` | `pikwik-001` | Tenant-wide, all services read this |
| `_shared` | `tier` | `standard` | Tier affects limits, features, deployment |
| `_shared` | `data_base_path` | `C:\HeliumData\pikwik-001` | Root for all tenant data |
| `float-sdk` | `instance_id` | `0e008e8xy0` | Per-Float-instance (Installer generates) |
| `relay` | `max_file_size_mb` | `50` | Relay-specific config |
| `relay` | `encryption_public_key` | `base64...` | Relay's E2EE public key (see §3.7) |
| `core` | `processing_timeout_seconds` | `300` | Core-specific config |

**Resolution order for any service:**
1. Check `service_config` where `service_name = '{my_service}'` — service-specific
2. Check `service_config` where `service_name = '_shared'` — tenant-wide defaults
3. Fall back to env var (`HELIUM_{KEY}`)
4. Fall back to code default

### 4.3 Multi-Instance Config (Float SDK)

When a tenant has multiple Float instances, each needs its own `instance_id`:

```
service_name = "float-sdk"          → shared Float config (relay_url, etc.)
service_name = "float-sdk:0e008e8xy0" → instance-specific (instance_id, sync_db_path)
service_name = "float-sdk:7f3a2b1cd9" → another instance
```

The colon `:` separates service name from instance qualifier. The SDK calls:
```
GET /api/registry/config/float-sdk:0e008e8xy0
```

HeartBeat looks up `service_name = 'float-sdk:0e008e8xy0'` first, then falls back to `'float-sdk'` for shared keys.

### 4.4 New: config.db (Phase 2)

A dedicated 3rd database for platform-wide configuration:

| Table | Purpose |
|---|---|
| `config_entries` | Per-service key-value config (migrated from registry.db `service_config`) |
| `tier_limits` | Daily limits, retention, max satellites per tier |
| `feature_flags` | Enable/disable features by name + minimum tier |
| `database_catalog` | Every database in the platform (see Section 5) |

**API Endpoints:**
```
GET  /api/config/{service}/{key}        — Read config value
PUT  /api/config/{service}/{key}        — Update config value
GET  /api/config/{service}              — List all config for a service
GET  /api/tiers/{tier}/limits           — Get tier limits
PUT  /api/tiers/{tier}/limits           — Update tier limit
GET  /api/features                      — List feature flags
PUT  /api/features/{feature}            — Toggle feature flag
```

---

## 5. Database Catalog

### 5.1 The Problem

Today, every service hardcodes its database path (or reads from env var). HeartBeat has no idea what databases exist, where they are, or who owns them. There is:
- No central inventory of databases
- No mapping from logical name → physical file → tenant
- No credential association for database access
- No way to coordinate schema migrations across services

### 5.2 The Solution: database_catalog Table

Every database in the platform gets registered in HeartBeat's `config.db`:

```sql
CREATE TABLE database_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Logical identity
    db_logical_name TEXT NOT NULL,           -- "sync", "invoices", "blob", "his_reference"
    db_category TEXT NOT NULL,               -- "operational", "reference", "audit", "config"
    tenant_id TEXT NOT NULL,                 -- "pikwik-001" or "global"
    owner_service TEXT NOT NULL,             -- "float-sdk", "core", "heartbeat", "his"

    -- Physical location
    db_physical_name TEXT NOT NULL,          -- "sync_pikwik-001_0e008e8xy0.db"
    db_path TEXT NOT NULL,                   -- "C:\HeliumData\pikwik-001\sync_pikwik-001_0e008e8xy0.db"
    db_engine TEXT NOT NULL DEFAULT 'sqlite', -- "sqlite" | "postgresql"

    -- Access control
    credential_id TEXT,                      -- References api_credentials.id in registry.db
    is_encrypted BOOLEAN DEFAULT 0,          -- SQLCipher encrypted (future)

    -- State
    status TEXT NOT NULL DEFAULT 'active',
    schema_version TEXT,                     -- Last applied migration version
    size_bytes INTEGER,                      -- Last known size

    -- Metadata
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(db_logical_name, tenant_id, owner_service)
);
```

### 5.3 Naming Convention

```
{db_type}_{tenant_id}.db                    — shared per-tenant databases
{db_type}_{tenant_id}_{instance_id}.db      — per-instance databases
```

| Database | Scope | Owner | Example |
|---|---|---|---|
| `sync` | Per Float instance | float-sdk | `sync_pikwik-001_0e008e8xy0.db` |
| `core_queue` | Per tenant | float-sdk | `core_queue_pikwik-001.db` |
| `invoices` | Per tenant | core | `invoices_pikwik-001.db` (SQLite) or `invoices_pikwik_001` (PostgreSQL) |
| `blob` | Per installation | heartbeat | `blob.db` (no tenant suffix — one per Primary) |
| `registry` | Per installation | heartbeat | `registry.db` (one per Primary) |
| `config` | Per installation | heartbeat | `config.db` (one per Primary) |
| `his_reference` | Per tenant | his | `his_reference_pikwik-001.db` |

### 5.4 API Endpoints

```
GET  /api/databases                          — List all registered databases
GET  /api/databases/{tenant_id}              — List databases for a tenant
GET  /api/databases/{tenant_id}/{db_name}    — Get specific database info
POST /api/databases/register                 — Register a new database
PUT  /api/databases/{id}/status              — Update status (migrating, archived)
```

### 5.5 Registration Flow

**At install time** (Installer runs):
```
POST /api/databases/register
{
    "db_logical_name": "sync",
    "db_category": "operational",
    "tenant_id": "pikwik-001",
    "owner_service": "float-sdk",
    "db_physical_name": "sync_pikwik-001_0e008e8xy0.db",
    "db_path": "C:\\HeliumData\\pikwik-001\\sync_pikwik-001_0e008e8xy0.db",
    "db_engine": "sqlite",
    "credential_id": "cred-fl-001",
    "description": "Float SDK sync database for workstation A"
}
```

**At service startup** (safety net):
```python
# Float SDK startup pseudocode
config = GET /api/registry/config/float-sdk:0e008e8xy0

# Option A: Construct path from components
tenant_id = config["tenant_id"]
instance_id = config["instance_id"]
base_path = config["data_base_path"]
sync_path = f"{base_path}/sync_{tenant_id}_{instance_id}.db"

# Option B: Get explicit path from database catalog
db_info = GET /api/databases/pikwik-001/sync
sync_path = db_info["db_path"]

# Either way: open the database
if not os.path.exists(sync_path):
    logger.warning("Expected pre-installed database not found, creating from schema")
    create_from_schema(sync_path)
```

### 5.6 SQLCipher (Future)

When `is_encrypted = true` in the catalog:
- The database file is encrypted with SQLCipher
- HeartBeat stores the encryption key in `api_credentials` (referenced by `credential_id`)
- Services call `GET /api/databases/{tenant_id}/{db_name}` to get the path
- The encryption key is returned ONLY via the API response (never stored in env vars)
- The API response decrypts the key server-side — services receive plaintext key over HTTPS
- Services use `PRAGMA key = '{key}'` after opening the SQLite connection

**For now:** `is_encrypted = false` for all databases. SQLCipher support is a Phase 3 feature. Services should plan for it but don't need to implement it yet.

---

## 6. Service Config Resolution — Complete Flow

Here's the full startup sequence every service should follow:

```
Step 1: REGISTER WITH HEARTBEAT
    POST /api/registry/register
    → Receive full service catalog (know all peer URLs)

Step 2: FETCH MY CONFIG
    GET /api/registry/config/{my_service}
    GET /api/registry/config/_shared
    → Know tenant_id, tier, data paths, feature flags

Step 3: REGISTER MY DATABASES
    POST /api/databases/register  (for each DB I own)
    → HeartBeat now knows all my databases

Step 4: RESOLVE DATABASE PATHS
    GET /api/databases/{tenant_id}  (optional — can construct from config)
    → Open databases, verify integrity

Step 5: START SERVING
    Ready to handle requests
    Begin periodic health reporting: POST /api/registry/health/{instance_id}
```

---

## 7. Current API Summary (Phase 1 — Already Implemented)

| # | Method | Endpoint | Purpose | Auth |
|---|---|---|---|---|
| 1 | POST | `/api/registry/register` | Service self-registration + peer catalog | Bearer |
| 2 | GET | `/api/registry/discover` | Full service catalog | Bearer |
| 3 | GET | `/api/registry/discover/{name}` | Service-specific catalog | Bearer |
| 4 | POST | `/api/registry/health/{id}` | Health report | Bearer |
| 5 | GET | `/api/registry/config/{name}` | Service config key-values | Bearer |
| 6 | POST | `/api/registry/credentials/generate` | Create API key+secret | Bearer |
| 7 | POST | `/api/registry/credentials/{id}/rotate` | Rotate credentials | Bearer |
| 8 | POST | `/api/registry/credentials/{id}/revoke` | Revoke credentials | Bearer |
| 9 | GET | `/api/registry/credentials/{name}` | List credentials (no secrets) | Bearer |
| 10 | POST | `/api/blobs/write` | Write file(s) to storage | Bearer |
| 11 | POST | `/api/blobs/register` | Register blob metadata (idempotent) | Bearer |
| 12 | GET | `/api/v1/heartbeat/blob/{uuid}/status` | Get blob status | Bearer |
| 13 | POST | `/api/v1/heartbeat/blob/{uuid}/status` | Update blob status | Bearer |
| 14 | GET | `/api/dedup/check` | Check file hash duplicate | Bearer |
| 15 | POST | `/api/dedup/record` | Record processed hash | Bearer |
| 16 | GET | `/api/limits/daily` | Check daily usage | Bearer |
| 17 | POST | `/api/audit/log` | Log audit event (fire-and-forget) | Bearer |
| 18 | POST | `/api/metrics/report` | Report metrics (fire-and-forget) | Bearer |
| 19 | GET | `/health` | Health check (DB + storage) | None |
| 20 | GET | `/` | Service info | None |

### Phase 2 Additions (Planned)

| # | Method | Endpoint | Purpose | Auth |
|---|---|---|---|---|
| 21 | GET | `/metrics` | Prometheus scrape | None |
| 22 | GET | `/api/v1/events/blobs` | SSE event stream | Bearer |
| 23-26 | * | `/api/config/*` | Config CRUD | Bearer |
| 27-29 | * | `/api/tiers/*` | Tier limits | Bearer |
| 30-31 | * | `/api/features/*` | Feature flags | Bearer |
| 32-35 | * | `/api/databases/*` | Database catalog | Bearer |
| 36-40 | * | `/api/submissions/*` | Submission queue | Bearer |
| 41-43 | * | `/api/v1/heartbeat/reconciliation/*` | Reconciliation | Bearer |
| 44-46 | * | `/primary/satellites/*` | Primary/Satellite mgmt | Bearer |
| 47-50 | * | `/satellite/*` | Satellite proxy endpoints | Bearer |
| 51 | GET | `/api/audit/verify` | Audit chain verification | Bearer |
| 52-53 | * | `/api/architecture/*` | Service boundary metadata | None |

---

## 8. Environment Variables Reference

### HeartBeat's Own Config

| Env Var | Default | Description |
|---|---|---|
| `HEARTBEAT_MODE` | `primary` | `primary` or `satellite` |
| `HEARTBEAT_HOST` | `0.0.0.0` | Bind host |
| `HEARTBEAT_PORT` | `9000` | Bind port |
| `HEARTBEAT_BLOB_DB_PATH` | auto-detect | Path to blob.db |
| `HEARTBEAT_REGISTRY_DB_PATH` | auto-detect | Path to registry.db |
| `HEARTBEAT_CONFIG_DB_PATH` | auto-detect | Path to config.db (Phase 2) |
| `HEARTBEAT_BLOB_STORAGE_ROOT` | auto-detect | Filesystem blob root |
| `HEARTBEAT_PRIMARY_URL` | (empty) | Hub URL (Satellite mode only) |
| `HEARTBEAT_AUTH_ENABLED` | `true` | Enable Bearer auth |
| `HEARTBEAT_RETENTION_YEARS` | `7` | FIRS retention period |
| `HEARTBEAT_DEFAULT_DAILY_LIMIT` | `1000` | Default daily file limit |

### What Other Services Need

| Service | Env Var | Value | Purpose |
|---|---|---|---|
| Relay | `RELAY_HEARTBEAT_API_URL` | `http://localhost:9000` | HeartBeat base URL |
| Relay | `RELAY_INTERNAL_SERVICE_TOKEN` | `{token}` | Auth for internal endpoints |
| Relay | `RELAY_PRIVATE_KEY_PATH` | `{path}` | E2EE private key file |
| Core | `HELIUM_HEARTBEAT_URL` | `http://localhost:9000` | HeartBeat base URL |
| Core | `HELIUM_HEARTBEAT_TOKEN` | `{api_key}:{api_secret}` | Auth |
| HIS | `HIS_HEARTBEAT_URL` | `http://localhost:9000` | HeartBeat base URL |
| HIS | `HIS_HEARTBEAT_TOKEN` | `{api_key}:{api_secret}` | Auth |
| Float SDK | `HELIUM_HEARTBEAT_URL` | `http://localhost:9000` | HeartBeat base URL |
| Float SDK | `HELIUM_API_KEY` | `{api_key}` | Auth key |
| Float SDK | `HELIUM_API_SECRET` | `{api_secret}` | Auth secret |

---

## 9. Superseded Documentation

The following documents are **superseded** by this contract. Teams should use this document as the authoritative reference:

| Old Document | Status | What Changed |
|---|---|---|
| `HEARTBEAT_INTEGRATION.md` v2.0 | Partially superseded | API reference still valid, but registry/credential sections replaced by this doc |
| `REVAMP_HANDOVER.md` | Superseded for registry design | MinIO references removed (filesystem storage), Parent-Client renamed to Primary/Satellite |
| `REVAMP_PHASE_2.md` | Still valid for implementation details | This doc adds database catalog (not in Phase 2 spec) |
| SDK team's `HEARTBEAT_REGISTRY_NOTE.md` | Addressed | Section 5 answers all their questions |

---

*End of Part 1. See Part 2 for DataBox + BulkContainer SDK Integration,*
*Part 3 for Observability, Updates & Lifecycle,*
*Part 4 for Authentication, Tenancy Governance & License Enforcement.*

---
*Maintained by: Pronalytics Limited — Helium Core Team*
*Company note: All WestMetro references in this document to be updated to Pronalytics Limited.*
