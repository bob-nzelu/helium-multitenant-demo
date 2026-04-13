# Helium Cross-Service Config Webhook Contract

**Version:** 1.0
**Date:** 2026-03-31
**Scope:** ALL Helium services (Core, Edge, Relay, HIS)

---

## Overview

HeartBeat is the single source of truth for tenant configuration (config.db).
All downstream services:
1. Fetch full config from HeartBeat at startup
2. Cache it in memory for zero-latency reads
3. Expose ONE webhook endpoint that HeartBeat calls when config changes
4. Re-fetch the full config on webhook trigger

---

## Endpoints

### Service → HeartBeat (fetch config)

```
GET /api/v1/heartbeat/config
Authorization: Bearer {api_key}

Response 200:
{
    "tier": "standard",
    "company_name": "Acme Corp",
    "company_id": "acme-001",
    "tier_settings": {
        "max_workers": 10,
        "max_file_size_mb": 50,
        "max_invoices_per_batch": 1000,
        "pipeline_soft_timeout": 280
    },
    "eic_config": { ... },
    "tenant_details": { ... },
    "transforma_config": { ... },
    ...
}
```

### HeartBeat → Service (notify change)

```
POST /api/v1/webhook/config_changed
Content-Type: application/json

{
    "changed": ["tier_settings", "eic_config"],
    "timestamp": "2026-03-31T10:00:00Z",
    "source": "config.db"
}

Response 200:
{
    "status": "ok",
    "refreshed": true,
    "message": "Config refreshed (tier_settings, eic_config)"
}
```

---

## `changed` Field Values

| Value | Description | Consumers |
|-------|-------------|-----------|
| `tier_settings` | Worker counts, batch limits, timeouts | Core, Edge |
| `eic_config` | EIC signing certificates and endpoints | Edge |
| `tenant_details` | Company name, TIN, address (seller/buyer party) | Core, Edge |
| `transforma_config` | Transforma script settings | Core |
| `rbac_roles` | Role/permission changes | Core, Relay |
| `notification_settings` | Alert preferences | Core |

---

## Implementation Pattern (per service)

```python
# 1. At startup — fetch + cache
config_cache = TenantConfigCache(heartbeat_client)
await config_cache.load()  # Non-fatal on failure

# 2. Runtime — read from cache
tier = config_cache.get("tier", "standard")
max_workers = config_cache.get_section("tier_settings").get("max_workers", 10)

# 3. Webhook — re-fetch on change
@router.post("/api/v1/webhook/config_changed")
async def config_changed(body):
    await config_cache.refresh(changed=body.changed)
```

---

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| HeartBeat down at startup | Cache stays empty, service uses env var defaults |
| HeartBeat down on webhook | Refresh fails, service keeps previous cached config |
| Webhook never arrives | Config stays at startup values (stale but functional) |
| HeartBeat sends unknown `changed` value | Ignored — full config re-fetched regardless |

---

## Security

- Webhook endpoint is internal-only (not exposed to internet)
- Bearer token validation on HeartBeat API calls
- No config values in webhook payload (trigger only, fetch separately)
