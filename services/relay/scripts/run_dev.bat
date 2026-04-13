@echo off
REM Relay-API — Local Dev Server (Windows)
REM Usage: scripts\run_dev.bat
REM Smoke: python scripts/test_relay.py

REM Server
set RELAY_HOST=127.0.0.1
set RELAY_PORT=8082
set RELAY_INSTANCE_ID=relay-dev-1
set RELAY_WORKERS=1

REM Auth (dev credentials)
set RELAY_DEV_API_KEY=test-key-001
set RELAY_DEV_API_SECRET=test-secret-001
set RELAY_INTERNAL_SERVICE_TOKEN=dev-token-123

REM Encryption off for local dev
set RELAY_REQUIRE_ENCRYPTION=false

REM Redis (optional — leave empty for graceful degradation)
REM To enable: set RELAY_REDIS_URL=redis://localhost:6379/0
set RELAY_REDIS_URL=

echo.
echo  Relay-API Dev Server
echo  URL:     http://127.0.0.1:8082
echo  Swagger: http://127.0.0.1:8082/docs
echo  Redis:   disabled (graceful degradation)
echo  Press Ctrl+C to stop
echo.

REM Note: --reload and --workers > 1 are mutually exclusive in uvicorn.
python -m uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8082 --reload
