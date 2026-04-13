# Relay-API Deployment Guide

## Prerequisites

- Python 3.12+
- pip dependencies: `fastapi`, `uvicorn`, `httpx`, `python-multipart`, `pynacl`, `asgi-lifespan` (test only)
- Network access to Core API and HeartBeat API

## Local Development

```bash
cd Services/Relay

# Create virtualenv
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac

# Install
pip install fastapi uvicorn httpx python-multipart pynacl
pip install pytest pytest-asyncio pytest-cov pytest-timeout asgi-lifespan  # test deps

# Run tests
python -m pytest --cov=src --cov-report=term-missing

# Start dev server
uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8082 --reload
```

## Environment Variables

All settings loaded via `RelayConfig.from_env()`. Each maps to `RELAY_{FIELD_UPPER}`.

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_HOST` | `0.0.0.0` | Bind address |
| `RELAY_PORT` | `8082` | HTTP port |
| `RELAY_INSTANCE_ID` | `relay-api-1` | Unique instance identifier |

### Upstream Services

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_CORE_API_URL` | `http://localhost:8080` | Core API base URL |
| `RELAY_HEARTBEAT_API_URL` | `http://localhost:9000` | HeartBeat API base URL |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_REQUIRE_ENCRYPTION` | `true` | Reject unencrypted requests |
| `RELAY_PRIVATE_KEY_PATH` | `` | NaCl private key file (empty = ephemeral) |
| `RELAY_INTERNAL_SERVICE_TOKEN` | `` | Bearer token for `/internal/` endpoints |

### File Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_MAX_FILES` | `3` | Max files per upload |
| `RELAY_MAX_FILE_SIZE_MB` | `10.0` | Max size per file |
| `RELAY_MAX_TOTAL_SIZE_MB` | `30.0` | Max total upload size |
| `RELAY_ALLOWED_EXTENSIONS` | `.pdf,.xml,.json,.csv,.xlsx` | Comma-separated |

### Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_PREVIEW_TIMEOUT_S` | `300` | Bulk preview wait (5 min) |
| `RELAY_REQUEST_TIMEOUT_S` | `30` | General HTTP timeout |

### Module Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_MODULE_CACHE_REFRESH_INTERVAL_S` | `43200` | Transforma module refresh (12 hrs) |

## API Authentication

All `/api/` requests require HMAC-SHA256 headers:

```
X-API-Key:    <api_key>
X-Timestamp:  <ISO 8601 UTC>
X-Signature:  HMAC-SHA256(secret, "{api_key}:{timestamp}:{sha256(body)}")
```

Timestamp must be within 5 minutes of server time.

## Endpoints

### POST /api/ingest

File upload — multipart form with `files[]` and optional `caller_type` (`bulk` or `external`).

- **Bulk** (default): Ingests files, waits up to 5 min for Core preview
- **External**: Ingests files, generates IRN + QR code, returns immediately

### POST /internal/refresh-cache

HeartBeat pushes Transforma module updates. Requires `Authorization: Bearer <token>`.

## FrontDoor Deployment

Relay is part of the **FrontDoor** deployment bundle:
- Relay-API (this service)
- MinIO (blob storage)
- MinIO database
- HeartBeat instance

Desktop mode: optional Cloudflare/ngrok tunnel for external API exposure.

## Architecture Notes

- **Stateless**: All persistent state in MinIO (via HeartBeat) and Core
- **Graceful degradation**: HeartBeat down → bulk works, external returns 503
- **Module cache**: Transforma Python modules loaded from HeartBeat config.db, cached as temp files, refreshed every 12 hours or on HeartBeat push
- **Body caching**: `BodyCacheMiddleware` pre-reads the raw body so both HMAC auth and multipart form parsing can access it
