# Relay — Bootstrap Gap Note

**Date:** 2026-03-31
**Spec:** `Services/General_Docs/UNIFIED_SERVICE_BOOTSTRAP_SPEC.md`

---

## Current State

Relay currently:
- Reads HEARTBEAT_URL, API_KEY, API_SECRET from env vars (correct)
- Fetches Transforma modules at startup via `GET /api/platform/transforma/config` (partial)
- Has webhook receiver `POST /api/v1/webhook/config_changed` (correct)
- Does NOT fetch full tenant config at startup

## Gap

Relay should also call `GET /api/v1/heartbeat/config` at startup to cache:
- Tenant details (for audit trail context)
- Tier settings (upload limits — currently read per-request from HeartBeat)
- FIRS config (for IRN generation context)
- Feature flags (to gate features)

## Required Changes

1. Add `ConfigCache` class following the unified spec pattern
2. Call `GET /api/v1/heartbeat/config` in `app.py` lifespan startup
3. Update webhook handler to refresh the full config cache (not just Transforma)
4. Read tier limits from cache instead of per-request HeartBeat calls

## Priority

**LOW** — Relay works correctly today because it reads limits per-request
from HeartBeat. The full config cache is an optimization (fewer HTTP calls)
and alignment with the unified pattern. Not blocking.
