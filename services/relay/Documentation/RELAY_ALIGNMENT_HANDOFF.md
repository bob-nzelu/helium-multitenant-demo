# Relay Service — Alignment Handoff

**Date:** 2026-03-31
**From:** HeartBeat audit session
**Status:** Changes needed in Relay (some already done, others pending)

---

## Summary

During the HeartBeat canonical schema audit (2026-03-29 to 2026-03-31), several
Relay files were updated and gaps were identified. This document captures
everything the Relay team needs to know.

---

## 1. Changes Already Made (Verify These)

### 1a. Metadata Enrichment — `src/services/ingestion.py`

**What changed:** Step 4 (write_blob) and Step 6 (register_blob) now enrich
per-file metadata with canonical display IDs before sending to HeartBeat.

**Step 4 (write_blob):**
- Extracts `file_display_ids[]` and `batch_display_id` from SDK metadata
- Per file: injects `file_display_id`, `batch_display_id`, `source_document_id = data_uuid`
- HeartBeat uses these to create canonical `file_entries` records with dual identity

**Step 6 (register_blob):**
- Same enrichment as Step 4
- `_register_blob()` now accepts `data_uuid` and `file_index` parameters

**Why:** HeartBeat's blob schema migrated from `blob_entries` (flat, file-centric)
to `file_entries` (batch-centric, dual identity). SDK needs `file_display_id` in
SSE events to correlate server state with local optimistic records.

### 1b. Queue Mode Mapping — `src/api/routes/ingest.py`

**What changed:** Route now auto-injects `queue_mode` and `connection_type`
into metadata based on `call_type`:

```
call_type="bulk"     → queue_mode="bulk",  connection_type="manual"
call_type="external"  → queue_mode="api",   connection_type="api"
```

SDK can also send these in metadata (takes precedence if present).

### 1c. Webhook Receiver — `src/api/routes/internal.py`

**What changed:** Added standard webhook endpoint alongside legacy endpoint:

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/webhook/config_changed` | **NEW** — Standard webhook per WEBHOOK_CONFIG_CONTRACT.md |
| `POST /internal/refresh-cache` | **LEGACY** — Kept for backward compat |

The new webhook supports selective refresh based on `changed[]` categories:
- `transforma_config` / `eic_config` → Refresh Transforma modules + FIRS keys
- `tier_settings` → Logged, read per-request from HeartBeat
- `tenant_details` → Logged, informational
- `registry` → Should re-fetch service URLs (not yet implemented)

---

## 2. Changes Needed (Not Yet Done)

### 2a. Full Config Fetch at Startup (P2)

**Gap:** Relay fetches Transforma modules at startup but NOT the full tenant
config from HeartBeat.

**What to build:**

```python
# In src/api/app.py lifespan startup, after module_cache.load_all():

from src.config_cache import ConfigCache

config_cache = ConfigCache(heartbeat_client)
await config_cache.load()
app.state.config_cache = config_cache
```

**ConfigCache class** (new file `src/config_cache.py`):

```python
class ConfigCache:
    def __init__(self, heartbeat_client):
        self._heartbeat = heartbeat_client
        self._config = {}

    async def load(self):
        try:
            self._config = await self._heartbeat.fetch_config()
        except Exception:
            logger.warning("Config fetch failed — starting with defaults")

    async def refresh(self, changed=None):
        self._config = await self._heartbeat.fetch_config()

    def get(self, key, default=None):
        return self._config.get(key, default)
```

**HeartBeat client needs new method** (add to `src/clients/heartbeat.py`):

```python
async def fetch_config(self) -> dict:
    """GET /api/v1/heartbeat/config — Full tenant config."""
    async def _fetch():
        http = self._get_http()
        headers = self.get_auth_headers()  # Bearer api_key:api_secret
        resp = await http.get("/api/v1/heartbeat/config", headers=headers)
        self._raise_for_status(resp, "fetch_config")
        return resp.json()
    return await self.call_with_retries(_fetch)
```

**Priority:** LOW — Relay works today by reading limits per-request from
HeartBeat. The config cache is an optimization and alignment with the unified
bootstrap pattern.

### 2b. Webhook: Handle `registry` Category

**Gap:** When HeartBeat sends `changed: ["registry"]`, Relay should re-fetch
service discovery data (updated URLs, rotated API keys).

**What to build:**

In `src/api/routes/internal.py`, add to `webhook_config_changed()`:

```python
if "registry" in payload.changed:
    # Re-fetch service discovery (updated URLs, rotated keys)
    await config_cache.refresh(changed=["registry"])
    actions.append("registry_refreshed")
```

**Priority:** MEDIUM — Needed for API key rotation and service URL changes.

### 2c. Periodic Config Freshness Check (P3)

**Safety net** for missed webhooks. Every 5 minutes, compare local config
timestamp against HeartBeat:

```python
# Background task in app.py lifespan
async def config_freshness_check():
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            remote_version = await heartbeat.fetch_config_version()
            if remote_version != config_cache.version:
                await config_cache.refresh()
        except Exception:
            pass  # Non-critical
```

**Priority:** LOW — Webhooks are the primary mechanism. This is the fallback.

---

## 3. HeartBeat Contract Reference

### Endpoints Relay calls:

| Endpoint | Method | Auth | Status |
|----------|--------|------|--------|
| `/api/blobs/write` | POST multipart | Optional JWT | Working |
| `/api/blobs/register` | POST JSON | Optional JWT | Working |
| `/api/dedup/check` | GET | None | Working |
| `/api/dedup/record` | POST JSON | None | Working |
| `/api/limits/daily` | GET | None | Working |
| `/api/audit/log` | POST JSON | None | Working |
| `/api/metrics/report` | POST JSON | None | Working |
| `/api/platform/transforma/config` | GET | Bearer key:secret | Working |
| `/health` | GET | None | Working |
| `/api/v1/heartbeat/config` | GET | Bearer key:secret | **NEW** — needs Relay client method |

### HeartBeat calls Relay:

| Endpoint | Method | Auth | Status |
|----------|--------|------|--------|
| `/api/v1/webhook/config_changed` | POST JSON | Bearer internal_token | **NEW** — implemented |
| `/internal/refresh-cache` | POST | Bearer internal_token | Legacy — still works |

### Key Schema Change:

HeartBeat's blob database migrated from `blob_entries` to `file_entries`
(canonical v1.4.0). This does NOT affect Relay's API calls — the HTTP
contract is unchanged. Relay sends blob_uuid, HeartBeat stores it in
file_entries.blob_uuid. Transparent to Relay.

### Metadata Fields HeartBeat Now Expects:

| Field | Source | Required? |
|-------|--------|-----------|
| `user_trace_id` | SDK | Optional |
| `x_trace_id` | SDK/Relay | Optional |
| `helium_user_id` | SDK/JWT | Optional |
| `float_id` | SDK | Optional |
| `session_id` | SDK/JWT | Optional |
| `machine_guid` | SDK | Optional |
| `mac_address` | SDK | Optional |
| `computer_name` | SDK | Optional |
| `file_display_id` | SDK (per-file) | Optional — Relay enriches from `file_display_ids[]` |
| `batch_display_id` | SDK | Optional — Relay passes through |
| `source_document_id` | Relay (= data_uuid) | Relay injects automatically |
| `queue_mode` | SDK or Relay auto | Relay injects from call_type |
| `connection_type` | SDK or Relay auto | Relay injects from call_type |

---

## 4. Files Modified in This Audit

| File | Changes |
|------|---------|
| `src/services/ingestion.py` | Metadata enrichment (Step 4 + Step 6), updated docstring |
| `src/api/routes/ingest.py` | queue_mode/connection_type mapping, updated docstring |
| `src/api/routes/internal.py` | Standard webhook endpoint + legacy backward compat |

---

## 5. Testing Checklist

- [ ] Verify metadata enrichment: upload via Float, check HeartBeat's file_entries has file_display_id
- [ ] Verify queue_mode mapping: bulk upload → queue_mode="bulk", API upload → queue_mode="api"
- [ ] Verify webhook: call `POST /api/v1/webhook/config_changed` with `changed: ["transforma_config"]`, confirm module cache refreshes
- [ ] Verify legacy webhook: call `POST /internal/refresh-cache`, confirm still works
- [ ] (Future) Verify config fetch: when `fetch_config()` is added, confirm startup loads full config
