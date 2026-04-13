# Tenant Configuration Handoff Specification

**Date:** 2026-03-30 (revised)
**From:** Float/SDK team (Opus)
**To:** HeartBeat team
**Status:** DRAFT v2 — revised after SDK cross-reference audit

---

## Overview

Float needs a comprehensive tenant configuration payload delivered by HeartBeat
at startup and on-demand. This config covers: tenant identity, user profile,
branding, bank accounts, service endpoints, registrations, licensing, app
behaviour, and schema version management.

**Delivery mechanism:**
1. **On startup:** SDK fetches full config from HeartBeat REST endpoint
2. **On-demand:** SDK re-fetches when config is missing or stale
3. **Broadcast:** HeartBeat pushes SSE event when config changes (schema
   update, license change, etc.) — Float re-fetches on receipt

**Local storage:** SDK caches the config in `sync.db` (SQLite tables defined
below). Images (logo, signature, avatar) are stored as BLOBs.

---

## 1. HeartBeat REST API Contract

### GET `/api/v1/config/{float_id}`

Returns the full tenant configuration for a registered Float instance.

**Headers:**
- `Authorization: Bearer <JWT>` (standard HeartBeat JWT)
- `X-Float-Id: <float_id>`

**Response:** `200 OK` with JSON body (schema below)

### POST `/api/v1/config/register`

First-time Float registration. Called once per installation.

**Request body:**
```json
{
  "machine_guid": "...",
  "mac_address": "...",
  "computer_name": "...",
  "tenant_id": "...",
  "user_id": "..."
}
```

**Response:** `201 Created`
```json
{
  "float_id": "<HeartBeat-assigned UUID>",
  "registered_at": "2026-03-30T01:00:00Z"
}
```

The `float_id` is permanent for that installation. Re-registration on the same
machine (same machine_guid + mac_address) returns the existing float_id.

---

## 2. Full Config JSON Schema

This is the complete JSON response from `GET /api/v1/config/{float_id}`.
HeartBeat must build and serve this payload.

```json
{
  "config_version": "1.0.0",
  "generated_at": "2026-03-30T01:00:00Z",

  "float_instance": {
    "float_id": "float-abc-001",
    "machine_guid": "...",
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "computer_name": "PROBOOK",
    "registered_at": "2026-03-01T00:00:00Z",
    "tenant_id": "tenant-abbey-001"
  },

  "tenant": {
    "tenant_id": "tenant-abbey-001",
    "company_name": "Abbey Mortgage Bank PLC",
    "trading_name": "Abbey",
    "tin": "12345678-0001",
    "rc_number": "RC-123456",
    "address": "45 East 78th Street\nNew York, NY 10075",
    "city": "New York",
    "state_code": "NY",
    "country_code": "NG",
    "email": "contact@abbeymortgage.com",
    "phone": "+1 (212) 555-0123",
    "default_currency": "NGN",
    "default_due_date_days": 30,
    "invoice_prefix": "WM-ABB-"
  },

  "branding": {
    "logo_base64": "<base64-encoded PNG>",
    "logo_mime_type": "image/png",
    "signature_enabled": true,
    "signer_name": "James O. Adeyemi",
    "signer_title": "Chief Financial Officer",
    "signature_image_base64": "<base64-encoded PNG, ~400x120px>"
  },

  "user": {
    "user_id": "user-bob-001",
    "display_name": "Bob Nzelu",
    "email": "bob@abbeymortgage.com",
    "role": "Owner",
    "title": "Managing Director",
    "phone": "+234 801 234 5678",
    "avatar_base64": "<base64-encoded JPEG/PNG, optional>",
    "avatar_mime_type": "image/jpeg",
    "permissions": [
      "invoices:read",
      "invoices:write",
      "invoices:delete",
      "invoices:admin",
      "reports:read",
      "admin:full",
      "uploads:write",
      "uploads:delete",
      "customers:read",
      "customers:write",
      "inventory:read",
      "inventory:write",
      "settings:read",
      "settings:write"
    ]
  },

  "bank_accounts": [
    {
      "bank_name": "Guaranty Trust Bank",
      "account_name": "Abbey Mortgage Bank PLC",
      "account_number": "0028893389",
      "bank_code": "058",
      "currency": "NGN",
      "is_primary": true,
      "display_order": 0
    },
    {
      "bank_name": "First Bank of Nigeria",
      "account_name": "Abbey Mortgage Bank PLC",
      "account_number": "2033445566",
      "bank_code": "011",
      "currency": "NGN",
      "is_primary": false,
      "display_order": 1
    }
  ],

  "service_endpoints": [
    {
      "service_name": "relay",
      "api_url": "http://127.0.0.1:8082",
      "sse_url": null,
      "api_key": "test-key-001",
      "api_secret": "test-secret-001"
    },
    {
      "service_name": "heartbeat",
      "api_url": "http://127.0.0.1:9000",
      "sse_url": "http://127.0.0.1:9000/api/v1/events/stream",
      "api_key": "test-hb-key-001",
      "api_secret": "test-hb-secret-001"
    },
    {
      "service_name": "core",
      "api_url": "http://127.0.0.1:8080",
      "sse_url": "http://127.0.0.1:8080/api/sync/events",
      "api_key": null,
      "api_secret": null
    },
    {
      "service_name": "his",
      "api_url": "http://127.0.0.1:8090",
      "sse_url": null,
      "api_key": null,
      "api_secret": null
    }
  ],

  "registrations": [
    {
      "authority": "FIRS",
      "registration_id": "FIRS-TIN-12345678-0001",
      "registration_date": "2020-01-15",
      "expiry_date": null,
      "status": "active",
      "metadata": {}
    },
    {
      "authority": "CAC",
      "registration_id": "RC-123456",
      "registration_date": "2015-06-01",
      "expiry_date": null,
      "status": "active",
      "metadata": {
        "company_type": "PLC",
        "date_of_incorporation": "2015-06-01"
      }
    }
  ],

  "license": {
    "license_id": "lic-abbey-001",
    "tenant_id": "tenant-abbey-001",
    "tier": "standard",
    "max_users": 10,
    "max_invoices_monthly": 5000,
    "features": {
      "bulk_upload": true,
      "pdf_export": true,
      "email_send": true,
      "api_access": false,
      "multi_currency": false,
      "advanced_reports": false
    },
    "issued_at": "2026-01-01T00:00:00Z",
    "expires_at": "2027-01-01T00:00:00Z",
    "status": "active",
    "signature": "<HMAC-SHA256 of license payload for tamper detection>"
  },

  "behaviour": {
    "security": {
      "pin_max_attempts": 5,
      "pin_typein_interval_hours": 5.0,
      "inactivity_timeout_minutes": 60,
      "lull_timeout_seconds": 60,
      "deactivate_timeout_seconds": 10,
      "set_new_pin_timeout_minutes": 40,
      "session_check_interval_seconds": 30,
      "session_timeout_hours": 8
    },
    "sync": {
      "connection_timeout_seconds": 30,
      "sync_timeout_seconds": 60,
      "polling_fallback_enabled": true,
      "polling_interval_seconds": 30
    },
    "uploads": {
      "max_file_size_mb": 5,
      "max_batch_size_mb": 10,
      "daily_upload_limit": 100,
      "allowed_extensions": [".pdf", ".xml", ".json", ".csv", ".xlsx"],
      "bulk_preview_timeout_seconds": 310
    },
    "cache": {
      "max_invoices": 5000,
      "search_cache_ttl_seconds": 60,
      "search_cache_max_size": 1000,
      "blob_file_cache_ttl_hours": 48
    },
    "rate_limits": {
      "per_minute": 100,
      "per_hour": 1000
    }
  },

  "schema": {
    "sync_db_version": "5.2",
    "canonical_invoice_version": "2.1.2.0",
    "migration_available": false,
    "migration_url": null
  }
}
```

---

## 3. sync.db Table Definitions (SDK Side)

These tables are created in sync.db by the SDK SchemaManager. HeartBeat does
NOT create these — HeartBeat only serves the JSON above; the SDK persists it.

### 3.1 `tenant_config` (main table — 1 row per tenant)

```sql
CREATE TABLE IF NOT EXISTS tenant_config (
    tenant_id               TEXT PRIMARY KEY,
    company_name            TEXT NOT NULL,
    trading_name            TEXT,

    -- Tax & Corporate identity
    tin                     TEXT,
    rc_number               TEXT,

    -- Contact
    address                 TEXT,
    city                    TEXT,
    state_code              TEXT,
    country_code            TEXT DEFAULT 'NG',
    email                   TEXT,
    phone                   TEXT,

    -- Invoicing defaults
    default_currency        TEXT DEFAULT 'NGN',
    default_due_date_days   INTEGER DEFAULT 30,
    invoice_prefix          TEXT,

    -- Branding (BLOBs)
    logo_image              BLOB,
    logo_mime_type           TEXT,
    signature_enabled       INTEGER DEFAULT 1,
    signer_name             TEXT,
    signer_title            TEXT,
    signature_image         BLOB,

    -- Schema tracking
    config_version          TEXT,
    fetched_at              TEXT
);
```

### 3.2 `float_instance` (1 row — this Float installation)

```sql
CREATE TABLE IF NOT EXISTS float_instance (
    float_id                TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    machine_guid            TEXT,
    mac_address             TEXT,
    computer_name           TEXT,
    registered_at           TEXT
);
```

### 3.3 `float_user` (1 row per user — currently single-user)

```sql
CREATE TABLE IF NOT EXISTS float_user (
    user_id                 TEXT PRIMARY KEY,
    float_id                TEXT NOT NULL REFERENCES float_instance(float_id),
    display_name            TEXT NOT NULL,
    email                   TEXT NOT NULL,
    role                    TEXT NOT NULL
        CHECK(role IN ('Owner', 'Admin', 'Operator', 'Support')),
    title                   TEXT,
    phone                   TEXT,
    avatar_image            BLOB,
    avatar_mime_type        TEXT,

    -- Cached permissions (JSON array of scope strings)
    permissions             TEXT DEFAULT '[]',
    permissions_updated_at  TEXT
);
```

### 3.4 `tenant_bank_accounts` (multiple rows)

```sql
CREATE TABLE IF NOT EXISTS tenant_bank_accounts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    bank_name               TEXT NOT NULL,
    account_name            TEXT NOT NULL,
    account_number          TEXT NOT NULL,
    bank_code               TEXT,
    currency                TEXT DEFAULT 'NGN',
    is_primary              INTEGER DEFAULT 0,
    display_order           INTEGER DEFAULT 0
);
```

### 3.5 `tenant_service_endpoints` (1 row per service)

```sql
CREATE TABLE IF NOT EXISTS tenant_service_endpoints (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    service_name            TEXT NOT NULL
        CHECK(service_name IN ('relay', 'heartbeat', 'core', 'his')),
    api_url                 TEXT NOT NULL,
    sse_url                 TEXT,
    api_key                 TEXT,
    api_secret              TEXT,

    UNIQUE(tenant_id, service_name)
);
```

### 3.6 `tenant_registrations` (1 row per authority)

```sql
CREATE TABLE IF NOT EXISTS tenant_registrations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id               TEXT NOT NULL REFERENCES tenant_config(tenant_id),
    authority               TEXT NOT NULL
        CHECK(authority IN ('FIRS', 'CAC', 'STATE_IRS')),
    registration_id         TEXT,
    registration_date       TEXT,
    expiry_date             TEXT,
    status                  TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'expired', 'suspended')),
    metadata                TEXT,

    UNIQUE(tenant_id, authority)
);
```

---

## 4. Invoice Source Model

Invoices carry a three-field source model to track both **how** an invoice
entered the system and **which system** sent it:

| Field | Column | Description | Examples |
|-------|--------|-------------|---------|
| `source` | `source TEXT` | Ingestion method | `"bulk_upload"`, `"api"`, `"odbc"`, `"jdbc"`, `"listener"`, `"email"`, `"manual"` |
| `source_id` | `source_id TEXT` | Originating system's unique ID | Float ID, ERP system ID, integration connector ID |
| `source_name` | `source_name TEXT` | Human-readable originating system name | `"Float (PROBOOK)"`, `"SAP R/3"`, `"QuickBooks"` |

**Schema change needed:** Add `source_name TEXT` to the invoices table
(currently only `source` and `source_id` exist).

**How Float populates these on upload:**
- `source` = `"bulk_upload"` (or `"manual"` for single invoice creation)
- `source_id` = the `float_id` from `float_instance` table
- `source_name` = `"Float ({computer_name})"` built from config

**How other integrations populate:**
- ERP connector: `source="odbc"`, `source_id="erp-sap-001"`, `source_name="SAP R/3"`
- Email ingestion: `source="email"`, `source_id="mailbox-invoices@co.ng"`, `source_name="Email Inbox"`
- API submission: `source="api"`, `source_id=<api_key_id>`, `source_name=<api_client_name>`

---

## 5. license.db (Separate Database — Stubbed)

Stored separately from sync.db for security isolation. The license payload is
signed by HeartBeat; the SDK verifies the HMAC on every read.

```sql
-- license.db
CREATE TABLE IF NOT EXISTS license (
    license_id              TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL,
    tier                    TEXT NOT NULL DEFAULT 'standard'
        CHECK(tier IN ('test', 'standard', 'pro', 'enterprise')),
    max_users               INTEGER DEFAULT 10,
    max_invoices_monthly    INTEGER DEFAULT 5000,
    features                TEXT DEFAULT '{}',
    issued_at               TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    status                  TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'expired', 'suspended', 'revoked')),
    signature               TEXT NOT NULL,
    fetched_at              TEXT
);
```

**Verification flow:**
1. SDK reads license row
2. Recomputes HMAC-SHA256 over (license_id + tenant_id + tier + max_users +
   max_invoices_monthly + features + issued_at + expires_at + status) using
   a shared secret from HeartBeat
3. Compares against `signature` — mismatch = tampered, block app

---

## 6. behaviour.json (App Behaviour Config)

Stored as a JSON file at `{data_dir}/behaviour.json`. Fetched from HeartBeat
alongside the main config. The SDK reads it at startup; the UI reads individual
values as needed.

**Why a file, not a table?** Behaviour config is a flat key-value tree that
changes infrequently and is read frequently. A JSON file avoids SQLite overhead
for simple lookups and is easy to inspect/debug.

See Section 2 `"behaviour"` key for the full schema.

**Refresh:** Overwritten when HeartBeat broadcasts a behaviour config update
via SSE. The SDK compares `config_version` before overwriting.

### Defaults & Rationale

All values below are hardcoded in the SDK as fallbacks when behaviour.json is
missing (first launch before HeartBeat delivers config).

#### Security Defaults

| Setting | Default | Rationale |
|---------|:-------:|-----------|
| `pin_max_attempts` | **5** | Banking industry standard (iOS/Android use 5-10). After 5 failures: lock + require admin reset. |
| `pin_typein_interval_hours` | **5.0** | Force PIN re-entry every 5h of active use. Balances security (session hijacking window) vs UX (not too frequent). |
| `inactivity_timeout_minutes` | **60** | Full session lock after 1h idle. FIRS e-invoicing compliance requires session timeout; 60min matches banking apps (CBN guidelines). |
| `lull_timeout_seconds` | **60** | Blur/hide sensitive data after 1min without interaction. Protects against shoulder-surfing in open offices. Short enough to catch walk-aways, long enough not to trigger while reading. |
| `deactivate_timeout_seconds` | **10** | Enter lull state 10s after Float loses OS focus (user switched to another app). Prevents data exposure when user alt-tabs away. |
| `set_new_pin_timeout_minutes` | **40** | Time window to complete PIN setup flow. Generous to allow the user to think about a good PIN without pressure. |
| `session_check_interval_seconds` | **30** | SDK polls session validity (JWT expiry, server revocation) every 30s. Frequent enough to catch revoked sessions quickly; lightweight check (single bool). |
| `session_timeout_hours` | **8** | Full session re-authentication window. After 8 hours of continuous use, user must re-authenticate (fresh JWT). Matches banking industry 8-hour workday session limits. |

#### Sync Defaults

| Setting | Default | Rationale |
|---------|:-------:|-----------|
| `connection_timeout_seconds` | **30** | Standard HTTP timeout. SSE and REST calls fail after 30s. Covers poor Nigerian network conditions without making users wait too long. |
| `sync_timeout_seconds` | **60** | Full sync operation timeout. A full resync with 5000 invoices can take 30-40s on slow connections; 60s gives headroom. |
| `polling_fallback_enabled` | **true** | When SSE drops (firewall, proxy), SDK falls back to polling. Must be true for reliability — SSE is blocked by some corporate networks. |
| `polling_interval_seconds` | **30** | Poll frequency when SSE is unavailable. 30s balances freshness (user sees updates within half a minute) vs server load (2 req/min per client). |

#### Upload Defaults

| Setting | Default | Rationale |
|---------|:-------:|-----------|
| `max_file_size_mb` | **5** | Per-file upload limit. Invoice PDFs are typically 50KB-2MB. 5MB per file prevents oversized scans and abuse while covering all realistic invoice documents. |
| `max_batch_size_mb` | **10** | Total batch upload limit. A bulk upload of 3 files at 5MB each would be rejected; encourages splitting large batches. Keeps Relay ingestion pipeline responsive. |
| `daily_upload_limit` | **100** | Per-tenant daily invoice upload quota. Standard tier limit — prevents runaway automated uploads. Pro/Enterprise tiers can override higher. |
| `allowed_extensions` | **[".pdf", ".xml", ".json", ".csv", ".xlsx"]** | Core invoice document formats. PDF (scans/exports), XML (UBL/FIRS), JSON (API), CSV/XLSX (bulk). Other formats are irrelevant to invoice processing. |
| `bulk_preview_timeout_seconds` | **310** | ~5min timeout for HIS to process a bulk upload preview. Large batches (50+ files) with OCR/Textract can take 3-4min. 310s gives buffer without appearing hung. |

#### Cache Defaults

| Setting | Default | Rationale |
|---------|:-------:|-----------|
| `max_invoices` | **5000** | Max invoices cached in sync.db. Covers 6-12 months for most SMBs. Pro/Enterprise tiers override to 10,000. Older invoices are evicted LRU. |
| `search_cache_ttl_seconds` | **60** | FTS5 search results cached for 1 minute. Search data goes stale quickly (new uploads, status changes). 60s prevents redundant queries during a single search session. |
| `search_cache_max_size` | **1000** | Max distinct search queries cached. At ~1KB per result set, this caps memory at ~1MB. LRU eviction when full. |
| `blob_file_cache_ttl_hours` | **48** | Downloaded files (PDFs, originals) stay on disk for 48h. Covers the common pattern of "download, review, come back tomorrow." Auto-cleanup prevents disk bloat. |

#### Rate Limit Defaults

| Setting | Default | Rationale |
|---------|:-------:|-----------|
| `per_minute` | **100** | Standard tier: 100 API calls/min. Prevents runaway loops while allowing normal batch operations. Pro=500, Enterprise=1000. |
| `per_hour` | **1000** | Standard tier: 1000 API calls/hour. Prevents sustained abuse. A full sync + 50 uploads + browsing uses ~200-300 calls/hour; 1000 leaves ample room. |

---

## 7. Schema Version Management & Auto-Migration

### How it works

1. **sync.db tracks its own version** in `sync_state` table (existing):
   `key='schema_version', value='5.2'`

2. **HeartBeat publishes the current canonical version** in the config
   response: `schema.sync_db_version` and `schema.canonical_invoice_version`

3. **On startup**, SDK compares:
   - Local `sync_state.schema_version` vs HeartBeat `schema.sync_db_version`
   - If local < remote → migration needed

4. **Migration execution** (safe):
   - SDK runs migrations sequentially (5.2 → 5.3 → 5.4 etc.)
   - Each migration is a Python function in `sdk/database/migrations/`
   - Wrapped in a transaction — rollback on failure
   - `sync_state.schema_version` updated only after success
   - App continues on old schema if migration fails (degraded but functional)

5. **HeartBeat SSE broadcast**: When a new schema is published, HeartBeat sends:
   ```json
   {
     "event": "schema.updated",
     "data": {
       "sync_db_version": "5.3",
       "canonical_invoice_version": "2.1.3.0",
       "migration_available": true
     }
   }
   ```
   SDK receives this, re-fetches config, and triggers migration if needed.

### Schema version fields (on config response)

| Field | Type | Description |
|-------|------|-------------|
| `sync_db_version` | string | Current sync.db schema version (e.g., "5.2") |
| `canonical_invoice_version` | string | Canonical invoice schema (e.g., "2.1.2.0") |
| `migration_available` | bool | True if local version < server version |
| `migration_url` | string? | URL to fetch migration SQL (future — currently NULL) |

---

## 8. SSE Events HeartBeat Must Broadcast

| Event | Trigger | SDK Action |
|-------|---------|------------|
| `config.updated` | Tenant config changes (name, address, branding, etc.) | Re-fetch full config |
| `license.updated` | License tier/status/expiry changes | Re-fetch license, verify signature |
| `behaviour.updated` | App behaviour settings change | Re-fetch behaviour.json |
| `schema.updated` | New sync.db schema published | Re-fetch config, run migration |
| `user.updated` | User role/permissions change | Re-fetch user section |
| `bank_accounts.updated` | Bank account added/removed/changed | Re-fetch bank_accounts array |
| `auth.cipher_refresh` | Cipher key rotation (~9 min cycle) | Replace current sync.db cipher key |

**Note:** `auth.cipher_refresh` is **planned / not yet implemented**. Currently the
SDK uses a hardcoded dev cipher key (`DEV_CIPHER_KEY` in `helium_sdk.py`). When
HeartBeat implements cipher delivery (Story 7 / WS3), the SDK will read the key
from this SSE event instead. The event payload should contain the new cipher key
and a rotation timestamp.

---

## 9. What HeartBeat Needs To Build

1. **Database tables** to store all tenant config data (HeartBeat's own DB —
   not the SQLite tables above, which are SDK-side cache)

2. **REST endpoints:**
   - `POST /api/v1/config/register` — Float instance registration
   - `GET /api/v1/config/{float_id}` — Full config fetch
   - `GET /api/v1/license/{tenant_id}` — License fetch (signed)
   - `GET /api/v1/behaviour/{tenant_id}` — Behaviour config fetch

3. **SSE events** (Section 8 above)

4. **Admin API** (for tenant provisioning / packager):
   - CRUD for tenant details, bank accounts, service endpoints, registrations
   - License issuance and renewal
   - Behaviour config updates
   - User management (role, permissions)

---

## 10. Migration Path (What Changes in Float/SDK)

Once HeartBeat serves this config, the SDK team will:

1. Add the 6 new tables (Section 3) to `schema.py`
2. Add `source_name TEXT` column to invoices table (Section 4)
3. Build a `ConfigService` that fetches, caches, and exposes config
4. Replace all hardcoded `_TENANT` dicts, `"Abbey Mortgage"` strings, dev
   identity dicts, and localhost URLs with `ConfigService` lookups
5. Wire `TopBar.set_tenant_name()`, `set_identity()`, `set_identity_avatar()`
   to real config data
6. Wire `InvoicePopup` bank details, signer info, seller_tin to config
7. Wire `data_service.py` to read tenant details from config instead of
   hardcoding
8. Populate `source`, `source_id`, `source_name` on invoice upload using
   config values (float_id, computer_name)
9. Create `licence.db` and verification logic
10. Create `behaviour.json` reader with hardcoded fallbacks
11. Wire schema version comparison and safe auto-migration on startup

**This spec does NOT require Float/SDK code changes.** It defines what
HeartBeat must serve so the SDK team can wire it up.

---

## 11. Pending Architectural Decisions

### 11.1 HIS Service Endpoint Routing

The `service_endpoints` array includes an `his` entry (currently stubbed at
`http://127.0.0.1:8090`). The architectural decision on whether HIS capabilities
should be exposed as:

- **Dedicated HIS endpoints** — separate service with its own URL, auth, and SSE
- **Core endpoints** — HIS features folded into Core's API surface
- **Spread across both** — some HIS operations via Core, others standalone

...is **still under review**. Until this is resolved:

1. The `his` entry remains in the spec as a placeholder
2. SDK does NOT have an HIS client — no code consumes this endpoint yet
3. HeartBeat should store the entry but it will not be used until routing is decided
4. When the decision is made, update this spec and add the corresponding SDK client

---

## 12. SDK Code Changes Required (Cross-Reference Notes)

These are SDK-side inconsistencies discovered during the cross-reference audit
(2026-03-30). They do NOT affect HeartBeat's implementation but must be resolved
when the SDK team wires up ConfigService.

### 12.1 SSE URL Path Standardization

**Canonical paths** (agreed 2026-03-30):
- Core SSE: `/api/sync/events` (as used in `sync/sync_client.py`)
- HeartBeat SSE: `/api/v1/events/stream` (as used in `sync/sync_client.py`)

**Files that need updating:**
- `config.py` — `TierConfig.core_sse_url` uses `/sse` suffix, should use `/api/sync/events`
- `config.py` — `TierConfig.heartbeat_sse_url` uses `/sse` suffix, should use `/api/v1/events/stream`
- `database/seed_sync_db.py` — config_cache entries use `/sse/stream`, should match canonical

### 12.2 Core Port Typo

`helium_sdk.py` line 379 has a hardcoded fallback `core_url = "http://127.0.0.1:8081"`.
All other sources (config.py tier defaults, seed_sync_db, this spec) use port **8080**.
Fix to `8080`.

### 12.3 File Size Limits

New limits (agreed 2026-03-30): **5 MB per file, 10 MB per batch**.

**Files that need updating:**
- `clients/relay_client.py` — `MAX_FILE_SIZE_MB = 50` → change to `5`, add `MAX_BATCH_SIZE_MB = 10`
- `database/seed_sync_db.py` — config_cache `max_file_size_mb = 10` → change to `5`, add `max_batch_size_mb` entry
- Float UI bulk upload container — enforce 5 MB per file and 10 MB per batch at the UI layer

### 12.4 Remove FIRS Config from Seed

`database/seed_sync_db.py` config_cache contains `firs_endpoint_test` and
`firs_business_id`. The SDK does NOT call FIRS directly — that is Edge's
responsibility. Remove these entries from the seed.

### 12.5 Lull & Deactivate Timer Implementation

`behaviour.security.lull_timeout_seconds` (60) and `deactivate_timeout_seconds` (10)
are defined in this spec and delivered by HeartBeat, but the SDK's `session/session_timers.py`
has no implementation of these timers. The Float UI layer should read these from
`behaviour.json` and implement:
- **Lull timer:** blur/hide sensitive data after 60s without user interaction
- **Deactivate timer:** enter lull state 10s after Float loses OS focus

The SDK stores these values (passthrough); Float UI consumes them directly.
