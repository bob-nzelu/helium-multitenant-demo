# Relay-API Documentation

**Status**: Implementation complete (Phase 1 + Phase 2)
**Tests**: 342 passing | 97% coverage
**Entry point**: `src.api.app:create_app()`

---

## Source Structure

```
src/
  api/          FastAPI app, routes, middleware, Pydantic models
  clients/      HTTP clients for Core + HeartBeat (stub responses)
  core/         Auth, validation, dedup, module cache, IRN/QR generation
  crypto/       NaCl envelope encryption, key management
  services/     Ingestion pipeline, bulk flow, external flow
  poller/       Polling sources (placeholder — future phase)
  config.py     RelayConfig dataclass with from_env()
  errors.py     RelayError hierarchy (30+ error types)
```

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/ingest` | HMAC-SHA256 | File upload (bulk or external) |
| POST | `/internal/refresh-cache` | Bearer token | HeartBeat pushes module refresh |

## Architectural Docs

| Document | Purpose |
|----------|---------|
| [RELAY_SERVICE_CONTRACT.md](RELAY_SERVICE_CONTRACT.md) | **Authoritative** — API contracts, data flow, field definitions (v2.0) |
| [RELAY_DECISIONS.md](RELAY_DECISIONS.md) | Binding design decisions (non-negotiable, v1.1) |
| [CORE_INTEGRATION_REQUIREMENTS.md](CORE_INTEGRATION_REQUIREMENTS.md) | API contracts Core must implement |
| [FLOAT_INTEGRATION_GUIDE.md](FLOAT_INTEGRATION_GUIDE.md) | Float desktop UI integration patterns |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Running the service locally and in production |

## Quick Commands

```bash
# Run tests
python -m pytest --tb=short -q

# Run with coverage
python -m pytest --cov=src --cov-report=term-missing

# Start server (dev)
uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8082 --reload
```
