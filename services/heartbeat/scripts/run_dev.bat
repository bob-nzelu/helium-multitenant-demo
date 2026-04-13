@echo off
REM HeartBeat Dev Server — Primary mode with Filesystem Blob Storage
REM Run from HeartBeat root: scripts\run_dev.bat
REM No Docker required — uses local filesystem for blob storage.

REM ── Service Config ──────────────────────────────────────────────
set HEARTBEAT_MODE=primary
set HEARTBEAT_HOST=127.0.0.1
set HEARTBEAT_PORT=9000

REM ── Blob Storage (Filesystem) ──────────────────────────────────
REM Default: data\dev_blobs (relative to HeartBeat root)
REM 14 placeholder blobs + metadata sidecars pre-seeded here.
set HEARTBEAT_BLOB_STORAGE_ROOT=data\dev_blobs

REM ── Database ────────────────────────────────────────────────────
REM blob.db:     auto-created from databases/schema.sql + seed.sql
REM registry.db: auto-created from databases/registry_schema.sql + registry_seed.sql
REM auth.db:     auto-created from databases/migrations/auth/*.sql

REM ── Limits ──────────────────────────────────────────────────────
set HEARTBEAT_DEFAULT_DAILY_LIMIT=1000
set HEARTBEAT_AUTH_ENABLED=true

REM ── Auth (Part 4) ─────────────────────────────────────────────
REM auth.db created automatically with migrations on first start.
REM Ed25519 keys generated on first start at databases\keys\.
REM No encryption key = unencrypted auth.db (dev only).
set HEARTBEAT_AUTH_DB_KEY=
set HEARTBEAT_SESSION_HOURS=8
set HEARTBEAT_JWT_EXPIRY_MINUTES=30

echo.
echo   HeartBeat Primary — Dev Server
echo   ──────────────────────────────
echo   URL:      http://127.0.0.1:9000
echo   Swagger:  http://127.0.0.1:9000/docs
echo   ReDoc:    http://127.0.0.1:9000/redoc
echo   Mode:     primary (local)
echo   Storage:  Filesystem (data\dev_blobs)
echo.
echo   Databases:
echo     blob.db      — File metadata (12 tables, 14 seed blobs)
echo     registry.db  — Service discovery + API credentials
echo     auth.db      — Users, sessions, roles, permissions
echo     license.db   — License terms + limits (Pro tier, immutable)
echo.
echo   Test User (Owner):
echo     email:    bob.nzelu@pronalytics.ng
echo     password: 1234%%%%%%
echo     role:     owner (full access)
echo     tenant:   helium-dev
echo.
echo   License (Dev):
echo     tier:         pro
echo     float_seats:  1
echo     max_owners:   1
echo     demo_mode:    true
echo     tenant:       helium-dev
echo.
echo   Service Credentials (Float SDK):
echo     api_key:    fl_test_float001
echo     api_secret: secret-float-sdk-dev-001
echo.
echo   Auth Endpoints:
echo     POST /api/auth/login         — Login (email + password)
echo     POST /api/auth/token/refresh — Refresh JWT
echo     POST /api/auth/logout        — Revoke session
echo     POST /api/auth/introspect    — Service-to-service verify
echo.

python -m uvicorn src.main:app --host 127.0.0.1 --port 9000 --reload
