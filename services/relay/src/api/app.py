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

from ..config import RelayConfig
from ..config_cache import ConfigCache
from ..core.tenant import load_tenants
from ..clients.core import CoreClient
from ..clients.heartbeat import HeartBeatClient
from ..clients.redis_client import RedisClient
from ..core.irn import IRNGenerator
from ..core.module_cache import TransformaModuleCache
from ..core.qr import QRGenerator
from ..errors import RelayError
from ..services.bulk import BulkService
from ..services.external import ExternalService
from ..services.ingestion import IngestionService
from .middleware import BodyCacheMiddleware, TraceIDMiddleware, relay_error_handler
from .routes.health import router as health_router
from .routes.ingest import router as ingest_router
from .routes.internal import router as internal_router
from .routes.metrics import router as metrics_router

logger = logging.getLogger(__name__)


def create_app(
    config: Optional[RelayConfig] = None,
    api_key_secrets: Optional[Dict[str, str]] = None,
) -> FastAPI:
    """
    Create and configure the Relay-API FastAPI application.

    Args:
        config: RelayConfig (defaults to from_env()).
        api_key_secrets: API key → secret mapping (defaults to empty).

    Returns:
        Configured FastAPI app, ready to run.
    """
    if config is None:
        config = RelayConfig.from_env()

    if api_key_secrets is None:
        api_key_secrets = {}

        # Multi-tenant mode: load from tenants.json
        if config.tenants_file:
            try:
                tenant_registry = load_tenants(config.tenants_file)
                for api_key, tenant in tenant_registry.items():
                    api_key_secrets[api_key] = tenant.api_secret
                logger.info(f"Loaded {len(tenant_registry)} tenants from {config.tenants_file}")
            except Exception as e:
                logger.error(f"Failed to load tenants file: {e}")
                tenant_registry = {}
        else:
            tenant_registry = {}

        # Fallback: single dev API key from environment
        if not api_key_secrets:
            dev_key = os.environ.get("RELAY_DEV_API_KEY", "")
            dev_secret = os.environ.get("RELAY_DEV_API_SECRET", "")
            if dev_key and dev_secret:
                api_key_secrets[dev_key] = dev_secret
                logger.info(f"Loaded dev API key: {dev_key[:8]}...")

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
        ingestion = IngestionService(config, heartbeat, core, redis_client=redis_client)
        irn_gen = IRNGenerator(module_cache)
        qr_gen = QRGenerator(module_cache)
        bulk_service = BulkService(ingestion, core)
        external_service = ExternalService(ingestion, core, irn_gen, qr_gen)

        # Store in app state
        app.state.config = config
        app.state.api_key_secrets = api_key_secrets
        app.state.tenant_registry = tenant_registry if config.tenants_file else {}
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
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(BodyCacheMiddleware)

    # Error handlers
    app.add_exception_handler(RelayError, relay_error_handler)

    # Routes
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(ingest_router)
    app.include_router(internal_router)

    return app
