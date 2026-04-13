"""
FastAPI Application Factory

create_app() builds the complete Relay-API application with:
    - Lifespan: startup loads module cache + keys, shutdown cleans up
    - Middleware: TraceID injection
    - Error handlers: RelayError → structured JSON
    - Routes: /health, /metrics, /api/ingest, /internal/refresh-cache
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import RelayConfig
from ..config_cache import ConfigCache
from ..clients.core import CoreClient
from ..clients.heartbeat import HeartBeatClient
from ..clients.redis_client import RedisClient
from ..core.module_cache import TransformaModuleCache
from ..core.tenant import TenantConfig, load_tenants
from ..errors import RelayError
from ..services.batch_store import BatchStore
from ..services.bulk import BulkService
from ..services.external import ExternalService
from ..services.ingestion import IngestionService
from .middleware import BodyCacheMiddleware, TraceIDMiddleware, relay_error_handler
from .routes.batches import router as batches_router
from .routes.health import router as health_router
from .routes.ingest import router as ingest_router
from .routes.internal import router as internal_router
from .routes.metrics import router as metrics_router

logger = logging.getLogger(__name__)


def create_app(
    config: Optional[RelayConfig] = None,
    tenant_registry: Optional[Dict[str, TenantConfig]] = None,
) -> FastAPI:
    """
    Create and configure the Relay-API FastAPI application.

    Args:
        config: RelayConfig (defaults to from_env()).
        tenant_registry: API key → TenantConfig mapping (defaults to tenants.json or env).

    Returns:
        Configured FastAPI app, ready to run.
    """
    if config is None:
        config = RelayConfig.from_env()

    if tenant_registry is None:
        tenants_file = os.environ.get("RELAY_TENANTS_FILE", "")
        if tenants_file and os.path.exists(tenants_file):
            tenant_registry = load_tenants(tenants_file)
            logger.info(f"Loaded {len(tenant_registry)} tenant(s) from {tenants_file}")
        else:
            # Fallback: single-tenant from RELAY_DEV_API_KEY/SECRET env vars
            tenant_registry = {}
            dev_key = os.environ.get("RELAY_DEV_API_KEY", "")
            dev_secret = os.environ.get("RELAY_DEV_API_SECRET", "")
            if dev_key and dev_secret:
                tenant_registry[dev_key] = TenantConfig(
                    tenant_id="dev",
                    api_key=dev_key,
                    api_secret=dev_secret,
                    service_id="DEV",
                    name="Dev Tenant",
                )
                logger.info(f"Loaded dev tenant from env: {dev_key[:8]}...")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup: load module cache. Shutdown: cleanup."""
        # ── Startup ──────────────────────────────────────────────────
        logger.info(f"Relay-API starting — {config.instance_id}:{config.port}")

        # Clients
        heartbeat = HeartBeatClient(
            heartbeat_api_url=config.heartbeat_api_url,
            timeout=config.request_timeout_s,
            service_api_key=config.heartbeat_api_key,
            service_api_secret=config.heartbeat_api_secret,
        )
        core = CoreClient(
            core_api_url=config.core_api_url,
            timeout=config.request_timeout_s,
            preview_timeout=config.preview_timeout_s,
        )

        # Redis (rate limiting)
        redis_client = RedisClient(
            redis_url=config.redis_url,
            prefix=config.redis_prefix,
            default_limit=config.rate_limit_daily,
        )
        await redis_client.connect()

        # Tenant config cache (full config from HeartBeat)
        config_cache = ConfigCache(heartbeat)
        await config_cache.load()
        if config_cache.is_loaded:
            logger.info(
                f"Config cache loaded — tenant={config_cache.get_tenant_name()}, "
                f"tier={config_cache.get_tenant_tier()}"
            )
        else:
            logger.warning("Config cache NOT loaded — running with defaults (degraded)")

        # Module cache
        module_cache = TransformaModuleCache(
            heartbeat,
            refresh_interval_s=config.module_cache_refresh_interval_s,
        )
        await module_cache.load_all()
        if module_cache.is_loaded:
            await module_cache.start_refresh_loop()
            logger.info("Module cache loaded + refresh loop started")
        else:
            logger.warning("Module cache NOT loaded — external flow will return 503")

        # Service layer
        batch_store = BatchStore()
        if config.database_url:
            await batch_store.connect(config.database_url)
            logger.info("BatchStore: PostgreSQL persistence enabled")
        else:
            logger.warning("BatchStore: no DATABASE_URL — running in-memory only (data lost on restart)")
        ingestion     = IngestionService(config, heartbeat, core, redis_client=redis_client)
        bulk_service  = BulkService(ingestion, core)
        external_service = ExternalService(
            ingestion,
            core_client=core,
            config=config,
            redis_client=redis_client,
            batch_store=batch_store,
        )

        # Store in app state
        app.state.batch_store = batch_store
        app.state.config = config
        app.state.tenant_registry = tenant_registry
        app.state.heartbeat = heartbeat
        app.state.core = core
        app.state.redis = redis_client
        app.state.config_cache = config_cache
        app.state.module_cache = module_cache
        app.state.ingestion = ingestion
        app.state.bulk_service = bulk_service
        app.state.external_service = external_service

        # Envelope placeholder (NaCl encryption configured later)
        app.state.envelope = None

        yield

        # ── Shutdown ─────────────────────────────────────────────────
        logger.info("Relay-API shutting down")
        await batch_store.close()
        await heartbeat.close()
        await redis_client.close()
        await module_cache.cleanup()

    app = FastAPI(
        title="Relay-API",
        version="2.0.0",
        description="Helium Relay — invoice ingestion gateway",
        lifespan=lifespan,
    )

    # Middleware (Starlette stacks: last added = outermost = runs first)
    # 1. TraceIDMiddleware: inject X-Trace-ID (inner)
    # 2. BodyCacheMiddleware: cache raw body so HMAC auth + form parsing
    #    can both read it without "Stream consumed" errors (outer)
    # 3. CORSMiddleware: allow browser dashboard + AB MFB web clients (outermost)
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(BodyCacheMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Error handlers
    app.add_exception_handler(RelayError, relay_error_handler)

    # Routes
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(ingest_router)
    app.include_router(batches_router)
    app.include_router(internal_router)

    return app
