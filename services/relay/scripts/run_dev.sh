#!/usr/bin/env bash
# Relay-API — Local Dev Server (Bash/WSL)
# Usage: bash scripts/run_dev.sh
# Smoke: python scripts/test_relay.py

# Server
export RELAY_HOST=127.0.0.1
export RELAY_PORT=8082
export RELAY_INSTANCE_ID=relay-dev-1
export RELAY_WORKERS=1

# Auth (dev credentials)
export RELAY_DEV_API_KEY=test-key-001
export RELAY_DEV_API_SECRET=test-secret-001
export RELAY_INTERNAL_SERVICE_TOKEN=dev-token-123

# Encryption off for local dev
export RELAY_REQUIRE_ENCRYPTION=false

# Redis (optional — leave empty for graceful degradation)
# To enable: export RELAY_REDIS_URL=redis://localhost:6379/0
export RELAY_REDIS_URL=

echo ""
echo "  Relay-API Dev Server"
echo "  URL:     http://127.0.0.1:8082"
echo "  Swagger: http://127.0.0.1:8082/docs"
echo "  Redis:   disabled (graceful degradation)"
echo "  Press Ctrl+C to stop"
echo ""

# Note: --reload and --workers > 1 are mutually exclusive in uvicorn.
python -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8082 --reload
