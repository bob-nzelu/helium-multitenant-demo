# ⚠️ RELAY CODE MIGRATION NOTICE

**Date**: 2026-01-31
**Status**: MIGRATION COMPLETED

---

## What Changed

All Relay service code has been **moved from `Services/Core/src/`** to **`Services/Relay/src/`**.

This aligns with Helium's service architecture where each service is self-contained:
- `Services/Relay/src/` - Relay service code
- `Services/Core/src/` - Core service code only
- `Services/Edge/src/` - Edge service code
- `Services/HeartBeat/src/` - HeartBeat service code

---

## What Moved

### ❌ Removed from Services/Core/src/

```
Services/Core/src/
├── relay/                    ❌ MOVED to Services/Relay/src/
├── services/                 ❌ MOVED to Services/Relay/src/services/ (Relay-specific)
└── config/                   ❌ MOVED to Services/Relay/config/ (for future use)
```

### ✅ Now Located in Services/Relay/src/

```
Services/Relay/src/
├── base.py                   ✅ BaseRelayService
├── factory.py                ✅ RelayServiceFactory
├── bulk/                     ✅ Bulk Upload service
├── queue/                    ✅ Queue service (stub)
├── watcher/                  ✅ Watcher service (stub)
├── dbc/                      ✅ DBC service (stub)
├── api/                      ✅ API service (stub)
├── polling/                  ✅ Polling service (stub)
├── email/                    ✅ Email service (stub)
└── services/                 ✅ Relay-specific clients, registry, errors
    ├── clients/
    ├── registry/
    └── errors/
```

---

## Why This Change

**Problem**: Relay code was incorrectly placed in Core service directory

**Reason for Change**:
- Each service is independent and self-contained
- Relay is NOT part of Core
- Clear service boundaries for multi-team development
- Easier to deploy Relay independently

**Benefits**:
✅ Relay team owns `Services/Relay/`
✅ Core team owns `Services/Core/`
✅ No confusion about service boundaries
✅ Easier to review and maintain code
✅ Clear integration points between services

---

## Important Notes for Other Teams

### For Core Team

✅ You can ignore `Services/Relay/` completely
✅ Core talks to Relay via HTTP APIs (documented in API_CONTRACTS.md)
✅ No shared code between Core and Relay (intentional)
✅ If you need shared utilities, create `Services/shared/` or `Services/common/`

### For Edge Team

📌 Follow the same pattern as Relay:
- All Edge code in `Services/Edge/src/`
- Edge-specific clients, registry, errors in `Services/Edge/src/services/`
- Edge talks to Core, HeartBeat via HTTP APIs

### For HeartBeat Team

📌 HeartBeat owns the shared infrastructure:
- `config.db` (configuration source of truth)
- `audit.db` (all services write here)
- `daily_usage.db` (usage tracking)
- `helium_blob.db` (blob storage metadata)

---

## Configuration

**IMPORTANT CHANGE**: Services no longer have local `config/` folders.

All services read configuration from **HeartBeat's `config.db`** at runtime:

```
Flow:
Admin Packager JSON → config.db (HeartBeat) → Services read from db
```

**For Relay**:
- ~~No local config files~~ ❌
- Relay queries HeartBeat at startup: `GET /api/heartbeat/config/relay`
- Relay reads: API keys, allowed extensions, max file size, service URLs, etc.

See `Helium/HELIUM_OVERVIEW.md` for "CONFIGURATION MANAGEMENT FLOW" section.

---

## File Locations Reference

### Relay Documentation

All moved to `Services/Relay/Documentation/`:
- `RELAY_CLAUDE_PROTOCOL.md`
- `RELAY_DECISIONS.md`
- `RELAY_PHASES.md`
- `RELAY_ARCHITECTURE.md`
- `RELAY_BULK_SPEC.md`
- `README.md`
- And more...

### Relay Source Code

All moved to `Services/Relay/src/`:
- `base.py`, `factory.py`, `exceptions.py`
- `bulk/`, `queue/`, `watcher/`, `dbc/`, `api/`, `polling/`, `email/`
- `services/clients/`, `services/registry/`, `services/errors/`

### Relay Tests

Will be in `Services/Relay/tests/` (Phase 1C)

---

## What Core Should Have

After migration, `Services/Core/src/` should contain ONLY:

```
Services/Core/src/
├── core/                     ✅ Core invoice processing service
├── tests/                    ✅ Core tests
└── ...
```

**NOT**:
- ❌ relay/
- ❌ relay-specific clients
- ❌ relay-specific error codes
- ❌ relay-specific config

---

## Questions?

If you have questions about:
- **Relay architecture**: See `Services/Relay/Documentation/`
- **Relay code structure**: See `Services/Relay/DIRECTORY_STRUCTURE.md`
- **Service integration**: See `API_CONTRACTS.md`
- **Configuration flow**: See `Helium/HELIUM_OVERVIEW.md`

---

**Migration completed on 2026-01-31**
**Performed by**: Claude Haiku 4.5 (Phase 1A)

