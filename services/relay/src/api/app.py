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

import httpx
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI

from ..config import RelayConfig
from ..config_cache import ConfigCache
from ..core.tenant import load_tenants
from ..clients.core import CoreClient
from ..clients.heartbeat import HeartBeatClient
from ..clients.introspect import IntrospectClient
from ..clients.redis_client import RedisClient
from ..core.irn import IRNGenerator
from ..core.jwks_cache import JWKSCache
from ..core.module_cache import TransformaModuleCache
from ..core.oauth_validator import OAuthTokenValidator
from ..core.qr import QRGenerator
from ..db.ledger import IngestLedger
from ..errors import RelayError
from ..observability.startup_checks import (
    check_clock_skew_against_heartbeat,
    validate_signing_key_shape,
)
from ..services.amqp_consumer import AMQPConsumer
from ..services.batch_external import BatchExternalService
from ..services.bulk import BulkService
from ..services.external import ExternalService
from ..services.finalize import FinalizeService
from ..services.ingestion import IngestionService
from ..services.status_service import StatusService
from .middleware import BodyCacheMiddleware, TraceIDMiddleware, relay_error_handler
from .routes.artifacts import router as artifacts_router
from .version_drift import VersionDriftError, version_drift_error_handler
from .routes.duplicate import router as duplicate_router
from .routes.finalize import router as finalize_router
from .routes.health import router as health_router
from .routes.ingest import router as ingest_router
from .routes.internal import router as internal_router
from .routes.metrics import router as metrics_router
from .routes.status import router as status_router
from .routes.webhook import router as webhook_router
from .webhook_auth import build_webhook_verifier

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

        # CSSV1 R9.1 + R9.2 — defensive startup checks before any HB
        # call. Bail loudly on a malformed signing key (R9.1) or a
        # major clock skew (R9.2); soft-warn on degraded dev mode +
        # transient HB unreachability.
        validate_signing_key_shape(config.heartbeat_s2s_signing_key)
        await check_clock_skew_against_heartbeat(config.heartbeat_api_url)

        # Clients — HMAC s2s post-cutover 2026-05-08 (HMAC_S2S_MIGRATION_SPEC).
        # The signing key comes from RELAY_S2S_SIGNING_KEY (config field
        # ``heartbeat_s2s_signing_key``); HB rejects Bearer api_key:api_secret
        # with 401 BEARER_S2S_REMOVED.
        heartbeat = HeartBeatClient(
            heartbeat_api_url=config.heartbeat_api_url,
            timeout=config.request_timeout_s,
            service_api_key=config.heartbeat_api_key,
            service_api_secret=config.heartbeat_api_secret,
            service_signing_key=config.heartbeat_s2s_signing_key,
        )
        introspect_client = IntrospectClient(
            heartbeat_url=config.heartbeat_api_url,
            service_api_key=config.heartbeat_api_key,
            service_api_secret=config.heartbeat_api_secret,
            service_signing_key=config.heartbeat_s2s_signing_key,
            timeout_s=config.request_timeout_s,
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

        # relay.db ingest ledger (Q28 / Frontdoor §2, §6) — durable write-first
        # idempotency. Guarded: only instantiated when RELAY_DB_PATH is set;
        # otherwise the ledger is disabled and the synchronous ingest path runs
        # exactly as before. The per-tenant SQLite file is the LOCKED all-PG
        # exception (do NOT migrate to PG — Frontdoor §2.3).
        ingest_ledger: Optional[IngestLedger] = None
        if config.relay_db_path:
            try:
                # Retention/prune knobs (Q28 ratify rider — Bob 2026-06-20).
                # The prune is WRITE-TRIGGERED inside the ledger (no timer):
                # piggybacked on every Nth record_received, plus a one-shot
                # prune on startup. retention_days is clamped < 30 in config.
                ingest_ledger = IngestLedger(
                    config.relay_db_path,
                    retention_days=config.relay_db_retention_days,
                    max_rows=config.relay_db_max_rows,
                    prune_every_n=config.relay_db_prune_every_n,
                )
                logger.info(
                    f"Ingest ledger enabled — {config.relay_db_path} "
                    f"(retention={config.relay_db_retention_days}d, "
                    f"max_rows={config.relay_db_max_rows}, "
                    f"prune_every_n={config.relay_db_prune_every_n})"
                )
            except Exception as e:
                # A bad path must not take down the Frontdoor. Degrade to
                # ledger-disabled (the synchronous path is still correct) and
                # surface loudly in deploy logs.
                logger.error(
                    f"Ingest ledger failed to open ({config.relay_db_path}) — "
                    f"running WITHOUT durable idempotency: {e}"
                )
                ingest_ledger = None
        else:
            logger.info("Ingest ledger disabled (RELAY_DB_PATH unset)")

        # Webhook receiver (L5) — verifies HB's Ed25519-signed webhooks
        # against HB's published webhook public key, fetched by ``kid`` via a
        # JWKSCache. Its own httpx client (closed on shutdown) so the JWKS
        # fetch is isolated from the s2s clients. PROVISIONAL contract — see
        # webhook_auth.py NEEDS-FROM-HB. No symmetric HMAC path exists.
        webhook_jwks_http = httpx.AsyncClient(timeout=config.request_timeout_s)
        webhook_verifier = build_webhook_verifier(config, webhook_jwks_http)

        # Service layer
        ingestion = IngestionService(
            config, heartbeat, core,
            redis_client=redis_client,
            ledger=ingest_ledger,
        )
        irn_gen = IRNGenerator(module_cache)
        qr_gen = QRGenerator(module_cache)
        bulk_service = BulkService(ingestion, core)
        external_service = ExternalService(ingestion, core, irn_gen, qr_gen)
        batch_external_service = BatchExternalService(ingestion, core, irn_gen, qr_gen)
        finalize_service = FinalizeService(core)
        status_service = StatusService(heartbeat, core)

        # AMQP consumer (Q37 Gap #6) — optional, non-fatal.
        # The tenant_registry from load_tenants() is keyed by api_key; AMQP
        # messages identify tenants by tenant_id.  Build a tenant_id-keyed
        # index so _handle_message() can look up the tenant without scanning.
        _api_key_registry = tenant_registry if config.tenants_file else {}
        tenants_by_id = {t.tenant_id: t for t in _api_key_registry.values()}
        amqp_consumer = AMQPConsumer(config, batch_external_service, tenants_by_id)
        await amqp_consumer.start()

        # Store in app state
        app.state.config = config
        app.state.api_key_secrets = api_key_secrets
        app.state.tenant_registry = tenant_registry if config.tenants_file else {}
        app.state.heartbeat = heartbeat
        app.state.introspect_client = introspect_client
        app.state.core = core
        app.state.redis = redis_client
        app.state.config_cache = config_cache
        app.state.module_cache = module_cache
        app.state.ingest_ledger = ingest_ledger
        app.state.ingestion = ingestion
        app.state.bulk_service = bulk_service
        app.state.external_service = external_service
        app.state.batch_external_service = batch_external_service
        app.state.finalize_service = finalize_service
        app.state.status_service = status_service
        app.state.amqp_consumer = amqp_consumer
        app.state.webhook_verifier = webhook_verifier
        app.state.webhook_jwks_http = webhook_jwks_http

        # Q37 Gap #2 — OAuth JWKS validator (optional, gated on RELAY_JWKS_URL).
        # Instantiated only when the URL is configured; otherwise oauth_validator
        # is None and the dispatcher falls through to the introspect path for all
        # Bearer JWTs. The httpx client is closed in shutdown below.
        _jwks_http_client: Optional[httpx.AsyncClient] = None
        if config.jwks_url:
            _jwks_http_client = httpx.AsyncClient(timeout=10.0)
            jwks_cache = JWKSCache(
                jwks_url=config.jwks_url,
                http_client=_jwks_http_client,
            )
            app.state.oauth_validator = OAuthTokenValidator(jwks_cache)
            logger.info(f"OAuth JWKS validator enabled — {config.jwks_url}")
        else:
            app.state.oauth_validator = None
            logger.info("OAuth JWKS validator disabled (RELAY_JWKS_URL not set)")

        # Envelope placeholder (NaCl encryption configured later)
        app.state.envelope = None

        yield

        # ── Shutdown ─────────────────────────────────────────────────
        logger.info("Relay-API shutting down")
        await amqp_consumer.stop()
        await heartbeat.close()
        await introspect_client.close()
        await redis_client.close()
        await module_cache.cleanup()
        if _jwks_http_client is not None:
            await _jwks_http_client.aclose()
        if ingest_ledger is not None:
            ingest_ledger.close()
        await webhook_jwks_http.aclose()

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
    # §B-Drift: version_drift 409 has a bespoke body shape (no RelayError
    # wrapper) — rendered verbatim by its own handler.
    app.add_exception_handler(VersionDriftError, version_drift_error_handler)

    # Routes
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(ingest_router)
    app.include_router(finalize_router)
    app.include_router(internal_router)
    app.include_router(duplicate_router)
    app.include_router(artifacts_router)
    app.include_router(status_router)
    app.include_router(webhook_router)

    return app
