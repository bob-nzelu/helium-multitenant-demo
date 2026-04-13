#!/usr/bin/env bash
# HeartBeat Dev Server — Primary mode with Filesystem Blob Storage
# Run from HeartBeat root: bash scripts/run_dev.sh
# No Docker required — uses local filesystem for blob storage.

# ── Service Config ──────────────────────────────────────────────
export HEARTBEAT_MODE=primary
export HEARTBEAT_HOST=127.0.0.1
export HEARTBEAT_PORT=9000

# ── Blob Storage (Filesystem) ──────────────────────────────────
# Default: data/dev_blobs (relative to HeartBeat root)
# 14 placeholder blobs + metadata sidecars pre-seeded here.
export HEARTBEAT_BLOB_STORAGE_ROOT=data/dev_blobs

# ── Database ────────────────────────────────────────────────────
# blob.db:     auto-created from databases/schema.sql + seed.sql
# registry.db: auto-created from databases/registry_schema.sql + registry_seed.sql
# auth.db:     auto-created from databases/migrations/auth/*.sql

# ── Limits ──────────────────────────────────────────────────────
export HEARTBEAT_DEFAULT_DAILY_LIMIT=1000
export HEARTBEAT_AUTH_ENABLED=true

# ── Auth (Part 4) ─────────────────────────────────────────────
# auth.db created automatically with migrations on first start.
# Ed25519 keys generated on first start at databases/keys/.
# No encryption key = unencrypted auth.db (dev only).
export HEARTBEAT_AUTH_DB_KEY=""
export HEARTBEAT_SESSION_HOURS=8
export HEARTBEAT_JWT_EXPIRY_MINUTES=30

echo ""
echo "  HeartBeat Primary — Dev Server"
echo "  ──────────────────────────────"
echo "  URL:      http://127.0.0.1:9000"
echo "  Swagger:  http://127.0.0.1:9000/docs"
echo "  ReDoc:    http://127.0.0.1:9000/redoc"
echo "  Mode:     primary (local)"
echo "  Storage:  Filesystem (data/dev_blobs)"
echo ""
echo "  Databases:"
echo "    blob.db      — File metadata (12 tables, 14 seed blobs)"
echo "    registry.db  — Service discovery + API credentials"
echo "    auth.db      — Users, sessions, roles, permissions"
echo "    license.db   — License terms + limits (Pro tier, immutable)"
echo ""
echo "  Test User (Owner):"
echo "    email:    bob.nzelu@pronalytics.ng"
echo "    password: 1234%%%"
echo "    role:     owner (full access)"
echo "    tenant:   helium-dev"
echo ""
echo "  License (Dev):"
echo "    tier:         pro"
echo "    float_seats:  1"
echo "    max_owners:   1"
echo "    demo_mode:    true"
echo "    tenant:       helium-dev"
echo ""
echo "  Service Credentials (Float SDK):"
echo "    api_key:    fl_test_float001"
echo "    api_secret: secret-float-sdk-dev-001"
echo ""
echo "  Auth Endpoints:"
echo "    POST /api/auth/login         — Login (email + password)"
echo "    POST /api/auth/token/refresh — Refresh JWT"
echo "    POST /api/auth/logout        — Revoke session"
echo "    POST /api/auth/introspect    — Service-to-service verify"
echo ""

python -m uvicorn src.main:app --host 127.0.0.1 --port 9000 --reload
