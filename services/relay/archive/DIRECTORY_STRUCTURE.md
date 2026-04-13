# Helium Relay Service - Directory Structure

**Last Updated**: 2026-01-31
**Status**: BINDING SPECIFICATION

---

## Overview

All Relay service source code lives in `Services/Relay/src/`. This document defines the organization and structure.

Each service (Relay, Core, Edge, HeartBeat) is self-contained with its own `src/`, `tests/`, `config/`, and `docker/` directories.

---

## Root Directory Structure

```
Helium/Services/Relay/
├── src/                                    # All Relay source code
│   ├── __init__.py
│   ├── base.py                             # BaseRelayService (inherited by all relay types)
│   ├── factory.py                          # RelayServiceFactory (creates instances)
│   ├── exceptions.py                       # Relay-specific exceptions
│   │
│   ├── bulk/                               # Bulk Upload Relay (Phase 1B - SONNET)
│   │   ├── __init__.py
│   │   ├── service.py                      # RelayBulkService class
│   │   ├── validation.py                   # File validation pipeline
│   │   ├── deduplication.py                # Deduplication logic
│   │   └── handlers.py                     # HTTP request handlers
│   │
│   ├── queue/                              # Internal Queue Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py                      # RelayQueueService (raises NotImplementedError)
│   │   └── README.md                       # Deferral explanation
│   │
│   ├── watcher/                            # File System Watcher Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── README.md
│   │
│   ├── dbc/                                # Database Connectivity Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── README.md
│   │
│   ├── api/                                # Webhook/API Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── README.md
│   │
│   ├── polling/                            # Time-based Polling Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── README.md
│   │
│   ├── email/                              # Email Processing Relay (Stub - Phase 2+)
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── README.md
│   │
│   └── services/                           # RELAY-SPECIFIC infrastructure (NOT shared)
│       ├── __init__.py
│       │
│       ├── clients/                        # Inter-service HTTP clients
│       │   ├── __init__.py
│       │   ├── base_client.py              # Base HTTP client with exponential backoff retry
│       │   ├── core_api_client.py          # Core API client (Relay calls Core)
│       │   ├── heartbeat_client.py         # HeartBeat API client (blob, dedup, limits)
│       │   └── audit_client.py             # Audit logging client (fire-and-forget)
│       │
│       ├── registry/                       # Service Discovery (Eureka)
│       │   ├── __init__.py
│       │   ├── eureka_client.py            # Real Eureka client (Pro/Enterprise)
│       │   ├── eureka_mock.py              # Mock Eureka client (Test/Standard, hardcoded localhost)
│       │   └── factory.py                  # Factory: Returns Mock or Real based on config
│       │
│       └── errors/                         # Relay-specific error definitions
│           ├── __init__.py
│           ├── exceptions.py               # All error codes as exception classes
│           └── handlers.py                 # Standardized error response formatting
│
├── tests/                                  # Relay tests (Phase 1C - OPUS)
│   ├── __init__.py
│   ├── conftest.py                         # Pytest configuration & fixtures
│   ├── unit/
│   │   ├── test_relay_base.py              # BaseRelayService tests
│   │   ├── test_relay_bulk.py              # RelayBulkService tests
│   │   ├── test_validation.py              # File validation tests
│   │   ├── test_deduplication.py           # Deduplication logic tests
│   │   ├── test_base_client.py             # Retry logic tests
│   │   ├── test_core_api_client.py         # Core API client tests
│   │   ├── test_heartbeat_client.py        # HeartBeat client tests
│   │   ├── test_eureka_client.py           # Eureka client tests
│   │   └── test_eureka_mock.py             # Mock Eureka tests
│   │
│   ├── integration/
│   │   ├── test_relay_to_core.py           # Relay → Core integration
│   │   ├── test_relay_to_heartbeat.py      # Relay → HeartBeat integration
│   │   └── test_bulk_upload_flow.py        # End-to-end upload flow
│   │
│   └── fixtures/
│       ├── factories.py                    # Test factories for objects
│       ├── mocks.py                        # Mock clients
│       └── sample_files.py                 # Sample invoice files for testing
│
├── docker/                                 # Relay Docker deployment (Pro/Enterprise)
│   ├── Dockerfile                          # Build Relay container
│   ├── docker-compose.yml                  # Local development setup
│   └── entrypoint.sh                       # Container startup script
│
├── Documentation/                          # Relay documentation (already exists)
│   ├── RELAY_CLAUDE_PROTOCOL.md            # Non-negotiable protocol for Claude
│   ├── RELAY_DECISIONS.md                  # Design decisions (binding)
│   ├── RELAY_PHASES.md                     # Phase breakdown (1A, 1B, 1C)
│   ├── RELAY_ARCHITECTURE.md               # Relay service architecture
│   ├── RELAY_BULK_SPEC.md                  # Bulk upload API specification
│   ├── RELAY_SERVICE_TYPES.md              # Stub specifications (Queue, Watcher, DBC, API, Polling, Email)
│   ├── README.md                           # Quick start guide
│   ├── IMPLEMENTATION_READY.md             # Implementation status
│   └── WORKSTREAM_MONITOR/
│       ├── RELAY_PHASE_1A_STATUS.md        # Phase 1A progress (HAIKU - COMPLETE)
│       ├── RELAY_PHASE_1B_STATUS.md        # Phase 1B progress (SONNET - upcoming)
│       └── RELAY_PHASE_1C_STATUS.md        # Phase 1C progress (OPUS - upcoming)
│
├── requirements.txt                        # Python dependencies (Relay-specific)
├── setup.py                                # Package setup
├── main.py                                 # Relay service entry point (FastAPI startup)
├── README.md                               # Service overview
└── .gitignore

```

---

## Source Code Organization Details

### **Phase 1A - Base Architecture (HAIKU)** ✅ COMPLETE

```
src/
├── base.py                  # BaseRelayService
├── factory.py               # RelayServiceFactory
├── exceptions.py            # Relay exceptions
│
├── bulk/
│   └── __init__.py          # (stub, awaiting Phase 1B)
│
├── queue/, watcher/, dbc/, api/, polling/, email/
│   └── All have service.py with NotImplementedError (stubs for Phase 2+)
│
└── services/
    ├── clients/
    │   ├── base_client.py           # Exponential backoff retry logic
    │   ├── core_api_client.py        # enqueue, process_preview, process_immediate, finalize
    │   ├── heartbeat_client.py       # write_blob, check_duplicate, check_daily_limit
    │   └── audit_client.py           # log_batch_ingestion_started, log_file_ingested, etc.
    │
    ├── registry/
    │   ├── eureka_mock.py            # Hardcoded localhost URLs
    │   ├── eureka_client.py          # Real Eureka discovery
    │   └── factory.py                # Selects Mock or Real based on config
    │
    └── errors/
        ├── exceptions.py             # 20+ error code classes
        └── handlers.py               # Response formatting
```

### **Phase 1B - Relay Bulk Upload (SONNET)** 🔄 UPCOMING

Will implement:
- `bulk/service.py` - RelayBulkService (inherits BaseRelayService)
- `bulk/validation.py` - File validation pipeline
- `bulk/handlers.py` - FastAPI HTTP endpoints (ingest, finalize, status, health, metrics)

### **Phase 1C - Integration & Testing (OPUS)** 🔄 UPCOMING

Will implement:
- `tests/` - Comprehensive test suite (90%+ coverage)
- `docker/` - Docker configuration for Pro/Enterprise

---

## Configuration

**IMPORTANT**: Relay does NOT have local config files.

Services read configuration from HeartBeat's `config.db` at runtime:
1. Admin Packager generates config JSON (installation time)
2. Installer stores config in `config.db` (HeartBeat owns this)
3. Relay queries HeartBeat: `GET /api/heartbeat/config/relay`
4. Relay initializes with config from database

See `Helium/HELIUM_OVERVIEW.md` for "CONFIGURATION MANAGEMENT FLOW" section.

---

## Testing Strategy

All tests (Phase 1C - OPUS):
- **Unit Tests** (80% of coverage): Test individual components in isolation
- **Integration Tests** (10% of coverage): Test Relay ↔ Core, Relay ↔ HeartBeat interaction
- **Fixtures**: Test data, mock clients, factory functions
- **Target**: 90%+ coverage across all Phase 1A + 1B code

---

## Dependencies

```
requirements.txt
├── fastapi==0.104.1                # Web framework (Relay HTTP server)
├── uvicorn==0.24.0                 # ASGI server
├── pydantic==2.5.0                 # Config validation
├── aiohttp==3.9.1                  # Async HTTP client (for Relay clients)
├── httpx==0.25.0                   # Sync HTTP client with retries
├── prometheus-client==0.19.0        # Prometheus metrics (/metrics endpoint)
├── python-json-logger==2.0.7        # Structured JSON logging (stdout)
├── cryptography==41.0.7             # HMAC signature verification
├── pytest==7.4.3                    # Testing (Phase 1C)
├── pytest-asyncio==0.21.1           # Async test support
├── pytest-cov==4.1.0                # Coverage reporting
└── [more as needed]
```

---

## Entry Points

### **Test/Standard Tier**

```bash
python -m helium.relay.bulk.main
    ↓
    Reads config from: config.db (provided by Admin Packager)
    Uses Registry: Mock Eureka (hardcoded localhost)
    Service Registry returns: localhost:8080 (Core), localhost:9000 (HeartBeat)
    Starts Relay: uvicorn on port 8082
```

### **Pro/Enterprise Tier**

```bash
docker run -e REGISTRY_TYPE=eureka -e EUREKA_URL=http://eureka:8761 helium-relay:1.0
    ↓
    Reads config from: config.db (shared database in Kubernetes)
    Uses Registry: Real Eureka (consul, etcd, or Spring Cloud)
    Service Registry returns: Dynamic service URLs
    Starts Relay: uvicorn on port 8082 (behind nginx load balancer)
```

---

## Summary Table

| Component | Location | Phase | Status |
|-----------|----------|-------|--------|
| **BaseRelayService** | `src/base.py` | 1A | ✅ Complete |
| **RelayServiceFactory** | `src/factory.py` | 1A | ✅ Complete |
| **Error Definitions** | `src/services/errors/` | 1A | ✅ Complete |
| **Service Clients** | `src/services/clients/` | 1A | ✅ Complete |
| **Service Registry** | `src/services/registry/` | 1A | ✅ Complete |
| **Relay Stubs** | `src/{bulk,queue,watcher,dbc,api,polling,email}/` | 1A | ✅ Complete |
| **RelayBulkService** | `src/bulk/service.py` | 1B | 🔄 Upcoming |
| **HTTP Handlers** | `src/bulk/handlers.py` | 1B | 🔄 Upcoming |
| **Test Suite** | `tests/` | 1C | 🔄 Upcoming |
| **Docker Config** | `docker/` | 1C | 🔄 Upcoming |

---

**All Relay code is self-contained in `Services/Relay/`. Each service (Core, Edge, HeartBeat) has its own equivalent directory structure.**

