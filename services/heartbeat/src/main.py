"""
HeartBeat Service - FastAPI Application

Main entry point for HeartBeat service (Primary mode).
Runs on port 9000 (configurable via environment).

Endpoints:
    # Original Phase 2 (unchanged)
    POST /api/v1/heartbeat/blob/register     (blob registration — legacy URL)
    GET  /api/v1/heartbeat/blob/{blob_uuid}  (get blob info)
    GET  /api/v1/heartbeat/blob/health       (health check — legacy)

    # Internal Service API (matches Relay HeartBeatClient)
    POST /api/blobs/write                    (write file to blob storage)
    POST /api/blobs/register                 (register blob metadata)
    GET  /api/dedup/check                    (check duplicate hash)
    POST /api/dedup/record                   (record processed hash)
    GET  /api/limits/daily                   (check daily usage limit)
    POST /api/audit/log                      (log audit event)
    POST /api/metrics/report                 (report metrics)

    # Blob Status (Float SDK + Core)
    GET  /api/v1/heartbeat/blob/{uuid}/status
    POST /api/v1/heartbeat/blob/{uuid}/status

    # Auth (Part 4)
    POST /api/auth/login                    (local credential login)
    POST /api/auth/token/refresh            (refresh session token)
    POST /api/auth/logout                   (revoke session)
    POST /api/auth/introspect               (service-to-service token verify)

    # Platform Services (Transforma)
    GET  /api/platform/transforma/config    (Transforma modules + FIRS keys)

    # Readiness & Lifecycle
    GET  /api/status/readiness              (aggregate platform readiness — no auth)
    GET  /api/lifecycle/services             (list managed services)
    GET  /api/lifecycle/services/{name}      (single service details)
    POST /api/lifecycle/services/{name}/start  (start service)
    POST /api/lifecycle/services/{name}/stop   (stop service)
    POST /api/lifecycle/services/{name}/restart (restart service)
    GET  /api/lifecycle/startup-order        (startup priority order)

    # Service Registry & Discovery
    POST /api/registry/register              (service self-registers)
    GET  /api/registry/discover              (full catalog)
    GET  /api/registry/discover/{name}       (service-specific)
    POST /api/registry/health/{id}           (report health)
    GET  /api/registry/config/{name}         (service config)
    POST /api/registry/credentials/generate  (new API key)
    POST /api/registry/credentials/{id}/rotate
    POST /api/registry/credentials/{id}/revoke
    GET  /api/registry/credentials/{name}    (list credentials)

    # Root
    GET  /health                             (service health check)
    GET  /                                   (service info)

Usage:
    python -m heartbeat.main

    or

    uvicorn heartbeat.main:app --host 0.0.0.0 --port 9000
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    register_router,
    auth_router,
    blobs_router,
    dedup_router,
    limits_router,
    audit_router,
    metrics_router,
    blob_status_router,
    registry_router,
)
from .config import get_config
from .database import get_blob_database
from .database.migrator import DatabaseMigrator
from .api.internal.audit_verify import router as audit_verify_router
from .api.internal.config_api import router as config_api_router
from .api.internal.tenant_config import router as tenant_config_router
from .api.internal.security_api import router as security_api_router
from .api.internal.sse_events import router as sse_events_router
from .api.internal.reconciliation import router as reconciliation_router
from .api.internal.blob_outputs import router as blob_outputs_router
from .api.internal.cache_refresh import router as cache_refresh_router
from .api.internal.architecture import router as architecture_router
from .api.internal.schema_api import router as schema_api_router
from .api.primary.endpoints import router as primary_router
from .api.satellite.endpoints import router as satellite_router
from .api.observability.prometheus import router as prometheus_router
from .api.observability.prometheus import PrometheusMiddleware
from .observability.metrics import SERVICE_INFO


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


# Lifespan context manager (startup/shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.

    Startup:
        - Load config
        - Initialize blob database (create + schema)
        - Run blob.db migrations (P2-C migrator)
        - Initialize registry database (Primary only)
        - Run registry.db migrations (Primary only)
        - Initialize config database (Primary only)
        - Run config.db migrations (Primary only)
        - Initialize filesystem blob storage (Primary only)
        - Initialize schema registry (load canonical SQL from databases/schemas/)
        - Initialize schema notifier (Primary only — HTTP callbacks + SSE push)
    Shutdown:
        - Cleanup all singletons (including schema notifier + registry)
    """
    config = get_config()

    # Startup
    logger.info(f"HeartBeat service starting up (mode={config.mode})...")

    # Initialize blob database
    db_path = config.get_blob_db_path()
    logger.info(f"Initializing blob database at {db_path}")
    db = get_blob_database(db_path)

    # Run blob.db migrations
    if config.auto_migrate:
        try:
            import os as _os
            blob_migrations_dir = _os.path.join(
                config.get_migrations_base_dir(), "blob"
            )
            blob_migrator = DatabaseMigrator(
                db_path=db_path,
                migrations_dir=blob_migrations_dir,
                db_name="blob",
            )
            results = blob_migrator.apply_pending()
            if results:
                applied = sum(1 for r in results if r.status == "applied")
                logger.info(f"Blob migrations: {applied} applied")

            # Check for drift
            has_drift, drift_details = blob_migrator.detect_drift()
            if has_drift:
                for detail in drift_details:
                    logger.warning(f"Blob migration drift: {detail}")
        except Exception as e:
            logger.warning(f"Blob migration failed (non-fatal): {e}")

    # Test database connection
    try:
        result = db.execute_query("SELECT COUNT(*) as count FROM file_entries")
        blob_count = result[0]["count"] if result else 0
        logger.info(f"Database connected successfully ({blob_count} blobs tracked)")
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")

    # Initialize registry database (Primary mode only)
    if config.is_primary:
        try:
            from .database.registry import get_registry_database
            registry_path = config.get_registry_db_path()
            logger.info(f"Initializing registry database at {registry_path}")
            reg_db = get_registry_database(registry_path)
            logger.info("Registry database initialized")
        except Exception as e:
            logger.warning(f"Registry database initialization failed: {e}")

    # Run registry.db migrations (Primary mode only)
    if config.is_primary and config.auto_migrate:
        try:
            import os as _os
            registry_migrations_dir = _os.path.join(
                config.get_migrations_base_dir(), "registry"
            )
            registry_path = config.get_registry_db_path()
            registry_migrator = DatabaseMigrator(
                db_path=registry_path,
                migrations_dir=registry_migrations_dir,
                db_name="registry",
            )
            results = registry_migrator.apply_pending()
            if results:
                applied = sum(1 for r in results if r.status == "applied")
                logger.info(f"Registry migrations: {applied} applied")

            has_drift, drift_details = registry_migrator.detect_drift()
            if has_drift:
                for detail in drift_details:
                    logger.warning(f"Registry migration drift: {detail}")
        except Exception as e:
            logger.warning(f"Registry migration failed (non-fatal): {e}")

    # Initialize config database (Primary mode only)
    if config.is_primary:
        try:
            from .database.config_db import get_config_database
            config_path = config.get_config_db_path()
            logger.info(f"Initializing config database at {config_path}")
            config_db = get_config_database(config_path)
            logger.info("Config database initialized")
        except Exception as e:
            logger.warning(f"Config database initialization failed: {e}")

    # Run config.db migrations (Primary mode only)
    if config.is_primary and config.auto_migrate:
        try:
            import os as _os
            config_migrations_dir = _os.path.join(
                config.get_migrations_base_dir(), "config"
            )
            config_path = config.get_config_db_path()
            config_migrator = DatabaseMigrator(
                db_path=config_path,
                migrations_dir=config_migrations_dir,
                db_name="config",
            )
            results = config_migrator.apply_pending()
            if results:
                applied = sum(1 for r in results if r.status == "applied")
                logger.info(f"Config migrations: {applied} applied")

            has_drift, drift_details = config_migrator.detect_drift()
            if has_drift:
                for detail in drift_details:
                    logger.warning(f"Config migration drift: {detail}")
        except Exception as e:
            logger.warning(f"Config migration failed (non-fatal): {e}")

    # Initialize license database (Primary mode only)
    # In production, license.db is created by the Installer and is immutable.
    # In dev mode, HeartBeat creates + seeds it via migrations.
    if config.is_primary and config.auto_migrate:
        try:
            import os as _os
            license_migrations_dir = _os.path.join(
                config.get_migrations_base_dir(), "license"
            )
            license_path = config.get_license_db_path()
            license_migrator = DatabaseMigrator(
                db_path=license_path,
                migrations_dir=license_migrations_dir,
                db_name="license",
            )
            results = license_migrator.apply_pending()
            if results:
                applied = sum(1 for r in results if r.status == "applied")
                logger.info(f"License migrations: {applied} applied")

            has_drift, drift_details = license_migrator.detect_drift()
            if has_drift:
                for detail in drift_details:
                    logger.warning(f"License migration drift: {detail}")

            logger.info(f"License database initialized at {license_path}")
        except Exception as e:
            logger.warning(f"License database initialization failed: {e}")

    # Initialize PostgreSQL connection pool (Primary mode only)
    if config.is_primary:
        try:
            from .database.pg_connection import get_pg_pool
            pg_dsn = config.get_pg_dsn()
            logger.info(f"Initializing PostgreSQL pool: {config.pg_host}:{config.pg_port}/{config.pg_database}")
            get_pg_pool(dsn=pg_dsn)
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.warning(f"PostgreSQL pool initialization failed: {e}")

    # Initialize PostgreSQL auth database (Primary mode only)
    if config.is_primary:
        try:
            from .database.pg_auth import get_pg_auth_database
            pg_auth_db = get_pg_auth_database()
            logger.info("PostgreSQL auth database layer initialized")
        except Exception as e:
            logger.warning(f"PostgreSQL auth database initialization failed: {e}")

    # Initialize JWT manager (Primary mode only)
    if config.is_primary:
        try:
            from .auth.jwt_manager import get_jwt_manager
            jwt_mgr = get_jwt_manager(
                private_key_path=config.get_jwt_private_key_path(),
                public_key_path=config.get_jwt_public_key_path(),
            )
            logger.info("JWT manager initialized (Ed25519)")
        except Exception as e:
            logger.warning(f"JWT manager initialization failed: {e}")

    # Initialize filesystem blob storage (Primary mode only)
    if config.is_primary:
        try:
            from .clients.filesystem_client import FilesystemBlobClient, set_filesystem_client
            storage_root = config.get_blob_storage_root()
            fs_client = FilesystemBlobClient(storage_root)
            set_filesystem_client(fs_client)
            logger.info(f"Filesystem blob storage initialized at {storage_root}")
        except Exception as e:
            logger.warning(f"Filesystem blob storage initialization failed: {e}")

    # Initialize Wazuh security event emitter (P2-B)
    try:
        from .observability.wazuh import init_wazuh_emitter
        import os as _os
        wazuh_log_dir = _os.path.join(
            _os.path.dirname(_os.path.dirname(__file__)),
            "logs",
        )
        wazuh_log_path = _os.path.join(wazuh_log_dir, "security_events.jsonl")
        init_wazuh_emitter(
            db_path=config.get_blob_db_path(),
            log_path=wazuh_log_path,
        )
        logger.info(f"Wazuh event emitter initialized (log: {wazuh_log_path})")
    except Exception as e:
        logger.warning(f"Wazuh emitter initialization failed (non-fatal): {e}")

    # Initialize PrimaryClient (Satellite mode only)
    if config.is_satellite and config.primary_url:
        try:
            from .clients.primary_client import init_primary_client
            init_primary_client(config.primary_url)
            logger.info(f"PrimaryClient initialized → {config.primary_url}")
        except Exception as e:
            logger.warning(f"PrimaryClient initialization failed: {e}")

    # Initialize Schema Registry (loads canonical SQL files from databases/schemas/)
    try:
        from .schemas import get_schema_registry
        schemas_dir = config.get_schemas_dir()
        registry = get_schema_registry(schemas_dir)
        registry.load()
        schemas = registry.list_schemas()
        logger.info(f"Schema registry initialized: {len(schemas)} schema(s) from {schemas_dir}")
        for s in schemas:
            logger.info(f"  Schema: {s['name']} v{s['version']}")
    except Exception as e:
        logger.warning(f"Schema registry initialization failed (non-fatal): {e}")

    # Initialize Schema Notifier (Primary mode only — needs registry database)
    if config.is_primary:
        try:
            from .schemas.notifier import init_schema_notifier
            from .database.registry import get_registry_database
            registry_db = get_registry_database()
            init_schema_notifier(registry_db=registry_db)
            logger.info("Schema notifier initialized (HTTP callbacks + SSE)")
        except Exception as e:
            logger.warning(f"Schema notifier initialization failed (non-fatal): {e}")

    # Initialize Event Ledger + pruner (SSE Spec Section 4)
    try:
        from .database.event_ledger import get_event_ledger, get_ledger_pruner
        ledger = get_event_ledger(config.get_blob_db_path())
        pruner = get_ledger_pruner()
        await pruner.start()
        logger.info("Event ledger initialized (48h retention, 6h prune cycle)")
    except Exception as e:
        logger.warning(f"Event ledger initialization failed: {e}")

    # Sync P2-D EventBus counter from ledger (prevents restart reset)
    try:
        from .events import get_event_bus as get_p2d_event_bus
        p2d_bus = get_p2d_event_bus()
        p2d_bus.sync_counter_from_ledger()
    except Exception as e:
        logger.warning(f"P2-D EventBus counter sync failed (non-fatal): {e}")

    # Initialize SSE producer + cipher text scheduler (Primary mode only)
    if config.is_primary:
        try:
            from .sse.producer import get_event_bus, get_cipher_scheduler
            event_bus = get_event_bus()
            cipher_scheduler = get_cipher_scheduler()
            await cipher_scheduler.start()
            logger.info(
                f"SSE event bus initialized "
                f"(cipher window={config.cipher_window_seconds}s)"
            )
        except Exception as e:
            logger.warning(f"SSE producer initialization failed: {e}")

    # Initialize Keep Alive Manager (Primary mode only)
    if config.is_primary:
        try:
            from .keepalive.manager import get_keepalive_manager
            keepalive = get_keepalive_manager()
            await keepalive.start()
            managed_count = len(keepalive._handles)
            logger.info(
                f"Keep Alive manager started "
                f"({managed_count} service(s), tier={config.tier})"
            )
        except Exception as e:
            logger.warning(f"Keep Alive manager failed (non-fatal): {e}")

    # Set Prometheus service info
    SERVICE_INFO.info({
        "version": "2.0.0",
        "mode": config.mode,
        "service": "heartbeat",
        "tier": config.tier,
    })

    logger.info("HeartBeat service startup complete")

    yield

    # Shutdown
    logger.info("HeartBeat service shutting down...")

    # Stop Keep Alive manager (reverse priority shutdown of child services)
    try:
        from .keepalive.manager import get_keepalive_manager, reset_keepalive_manager
        keepalive = get_keepalive_manager()
        await keepalive.stop()
        reset_keepalive_manager()
        logger.info("Keep Alive manager shut down")
    except Exception:
        pass

    # Stop ledger pruner
    try:
        from .database.event_ledger import get_ledger_pruner, reset_event_ledger
        pruner = get_ledger_pruner()
        await pruner.stop()
        reset_event_ledger()
        logger.info("Event ledger pruner shut down")
    except Exception:
        pass

    # Stop SSE cipher text scheduler
    try:
        from .sse.producer import get_cipher_scheduler, reset_sse_producer
        scheduler = get_cipher_scheduler()
        await scheduler.stop()
        reset_sse_producer()
        logger.info("SSE producer shut down")
    except Exception:
        pass

    from .clients.filesystem_client import reset_filesystem_client
    from .clients.primary_client import reset_primary_client
    from .database.config_db import reset_config_database
    from .database.pg_auth import reset_pg_auth_database
    from .database.pg_connection import reset_pg_pool
    from .auth.jwt_manager import reset_jwt_manager
    from .observability.wazuh import reset_wazuh_emitter
    from .events import reset_event_bus
    from .schemas.registry import reset_schema_registry
    from .schemas.notifier import reset_schema_notifier
    reset_filesystem_client()
    reset_primary_client()
    reset_config_database()
    reset_pg_auth_database()
    reset_pg_pool()
    reset_jwt_manager()
    reset_wazuh_emitter()
    reset_event_bus()
    reset_schema_notifier()
    reset_schema_registry()
    logger.info("HeartBeat service shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="HeartBeat Service",
    description=(
        "HeartBeat Primary — Helium's blob storage, service registry, "
        "and observability hub. Manages filesystem blob storage, deduplication, "
        "daily limits, audit trails, metrics, and API credential management."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# CORS middleware (allow all origins for development)
# TODO: Restrict origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus request instrumentation middleware (P2-A)
app.add_middleware(PrometheusMiddleware)


# ── Include all routers ─────────────────────────────────────────────────

# Original Phase 2 (unchanged — legacy URLs)
app.include_router(register_router)

# Internal Service API (matches Relay HeartBeatClient)
app.include_router(blobs_router)
app.include_router(dedup_router)
app.include_router(limits_router)
app.include_router(audit_router)
app.include_router(metrics_router)

# Blob Status (Float SDK + Core)
app.include_router(blob_status_router)

# Auth (Part 4 -- login, refresh, logout, introspect, stepup, policy)
app.include_router(auth_router)

# SSE Stream (authenticated, multiplexed events)
from .api.sse import router as sse_stream_router
app.include_router(sse_stream_router)

# Service Registry & Discovery
app.include_router(registry_router)

# Audit Verification (Q4 — Immutability)
app.include_router(audit_verify_router)

# Config API (Q5 — config.db CRUD)
app.include_router(config_api_router)

# Tenant Config API (Float/SDK config + backend webhook config)
app.include_router(tenant_config_router)

# Security Events API (P2-B — Wazuh integration)
app.include_router(security_api_router)

# SSE Event Streaming (P2-D — real-time blob events)
app.include_router(sse_events_router)

# Reconciliation (P2-E — blob consistency checks)
app.include_router(reconciliation_router)

# Blob Outputs (P2-F — processed output tracking)
app.include_router(blob_outputs_router)

# Cache Refresh (P2-F — push invalidation to services)
app.include_router(cache_refresh_router)

# Architecture Metadata (Q3 — static service boundary docs)
app.include_router(architecture_router)

# Schema Registry API (canonical schema serving)
app.include_router(schema_api_router)

# Platform Services (Transforma modules, FIRS keys)
from .api.platform.platform_services import router as platform_router
app.include_router(platform_router)

# Readiness (unauthenticated — used by Float/Installer/tray)
from .api.internal.readiness import router as readiness_router
app.include_router(readiness_router)

# Lifecycle (service start/stop/restart — admin only)
from .api.internal.lifecycle import router as lifecycle_router
app.include_router(lifecycle_router)

# Primary/Satellite (Q6 — deployment mode endpoints)
app.include_router(primary_router)
app.include_router(satellite_router)

# Event Publishing (Core/Edge → HeartBeat SSE pipeline)
from .api.internal.events_publish import router as events_publish_router
app.include_router(events_publish_router)

# Prometheus Metrics (P2-A — unauthenticated scrape endpoint)
app.include_router(prometheus_router)


# ── Root Endpoints ──────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """
    Service health check.

    Matches Relay HeartBeatClient.health_check() contract.
    Returns: {status, mode, service, storage, database, timestamp}
    """
    config = get_config()

    # Check database
    db_healthy = False
    try:
        db = get_blob_database()
        db.execute_query("SELECT 1")
        db_healthy = True
    except Exception:
        pass

    # Check filesystem blob storage (Primary only)
    storage_healthy = False
    if config.is_primary:
        try:
            from .clients.filesystem_client import get_filesystem_client
            fs = get_filesystem_client()
            if fs:
                storage_healthy = await fs.is_healthy()
        except Exception:
            pass
    else:
        storage_healthy = None  # Not applicable for Satellite

    overall = "healthy" if db_healthy else "degraded"
    if config.is_primary and not storage_healthy:
        overall = "degraded"

    return {
        "status": overall,
        "mode": config.mode,
        "service": "heartbeat",
        "storage": "connected" if storage_healthy else ("n/a" if storage_healthy is None else "disconnected"),
        "database": "connected" if db_healthy else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
async def root():
    """Root endpoint — service information."""
    config = get_config()
    return {
        "service": "heartbeat",
        "mode": config.mode,
        "version": "2.0.0",
        "description": "HeartBeat Primary — blob storage, service registry, observability",
        "endpoints": {
            "health": "GET /health",
            "blob_write": "POST /api/blobs/write",
            "blob_register": "POST /api/blobs/register",
            "dedup_check": "GET /api/dedup/check",
            "dedup_record": "POST /api/dedup/record",
            "daily_limit": "GET /api/limits/daily",
            "audit_log": "POST /api/audit/log",
            "metrics_report": "POST /api/metrics/report",
            "blob_status": "GET/POST /api/v1/heartbeat/blob/{uuid}/status",
            "legacy_register": "POST /api/v1/heartbeat/blob/register",
            "registry_register": "POST /api/registry/register",
            "registry_discover": "GET /api/registry/discover",
            "registry_credentials": "POST /api/registry/credentials/generate",
            "schemas_list": "GET /api/schemas",
            "schema_get": "GET /api/schemas/{name}",
            "schema_sql": "GET /api/schemas/{name}/sql",
        },
    }


# Run with uvicorn (development convenience — production uses CLI)
if __name__ == "__main__":
    import uvicorn

    config = get_config()

    logger.info(f"Starting HeartBeat service on {config.host}:{config.port}")

    uvicorn.run(
        "src.main:app",
        host=config.host,
        port=config.port,
        reload=True,
        log_level="info",
    )
