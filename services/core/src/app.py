"""
Core Service Application Factory

FastAPI app with lifespan managing: pool, scheduler, SSE manager.
Pattern follows Relay's create_app() convention.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src import __version__
from src.config import CoreConfig
from src.database.pool import close_pool, create_pool
from src.database.init import init_schemas
from src.errors import CoreError
from src.health import router as health_router
from src.ingestion.heartbeat_client import HeartBeatBlobClient
from src.ingestion.parsers.registry import create_default_registry
from src.ingestion.queue_scanner import QueueScanner
from src.ingestion.router import router as ingestion_router
from src.middleware.cors import configure_cors
from src.middleware.logging import RequestLoggingMiddleware
from src.middleware.trace_id import TraceIDMiddleware
from src.scheduler import create_scheduler, register_jobs, register_ws4_jobs, register_queue_jobs, register_sse_jobs, register_ws7_jobs, set_scheduler_deps
from src.sse.entity_events import EntityEventListener
from src.sse.event_ledger import EventLedger
from src.sse.manager import SSEConnectionManager
from src.sse.router import router as sse_router
from src.webhook import router as webhook_router
from src.config_cache import TenantConfigCache

# WS4: Entity CRUD routers
from src.api.invoices import router as invoices_router
from src.api.customers import router as customers_router
from src.api.inventory import router as inventory_router
from src.api.entities import router as entities_router
from src.api.search import router as search_router

# WS7: Reports & Statistics
from src.reports.router import router as reports_router
from src.reports.statistics_router import router as statistics_router

# WS6: Observability
from src.observability.audit_logger import AuditLogger
from src.observability.audit_middleware import EntityAuditMiddleware
from src.observability.metrics import core_info
from src.observability.metrics_collector import MetricsCollector
from src.observability.metrics_middleware import PrometheusMiddleware
from src.observability.notification_service import NotificationService
from src.observability.router import router as observability_router

logger = structlog.get_logger()


def create_app(config: CoreConfig | None = None) -> FastAPI:
    """
    Factory function that creates and configures the FastAPI app.

    Usage:
        uvicorn src.app:create_app --factory --host 0.0.0.0 --port 8080
    """
    if config is None:
        config = CoreConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ── STARTUP ──────────────────────────────────────────────────
        app.state.start_time = time.monotonic()
        app.state.config = config

        # Configure structlog
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                structlog._log_levels.NAME_TO_LEVEL[config.log_level.lower()]
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Database pool
        pool = await create_pool(config)
        app.state.pool = pool

        # Initialize schemas
        await init_schemas(pool)

        # SSE manager
        sse_manager = SSEConnectionManager(
            buffer_size=config.sse_buffer_size,
            heartbeat_interval=config.sse_heartbeat_interval,
        )
        await sse_manager.start_heartbeat()
        app.state.sse_manager = sse_manager

        # SSE Event Ledger (SSE_SPEC Section 4)
        event_ledger = EventLedger(
            retention_hours=config.sse_ledger_retention_hours,
        )
        app.state.event_ledger = event_ledger

        # Wire ledger into SSE manager for automatic ledger writes on publish
        sse_manager.set_ledger(event_ledger, pool)
        await sse_manager.sync_sequence_from_ledger()

        # Scheduler (APScheduler v4 requires async context manager)
        scheduler = create_scheduler()
        app.state.scheduler = scheduler

        if scheduler:
            await scheduler.__aenter__()
            set_scheduler_deps(pool, event_ledger=event_ledger)
            await register_jobs(scheduler)
            await register_ws4_jobs(scheduler, pool)
            await register_queue_jobs(scheduler, pool)
            await register_sse_jobs(scheduler, pool, event_ledger)

        # WS1: HeartBeat blob client
        heartbeat_client = HeartBeatBlobClient(
            base_url=config.heartbeat_url,
            api_key=config.heartbeat_api_key,
            timeout=config.blob_fetch_timeout,
        )
        app.state.heartbeat_client = heartbeat_client

        # Tenant config cache — fetch full config from HeartBeat at startup
        config_cache = TenantConfigCache(heartbeat_client)
        await config_cache.load()  # Non-fatal: falls back to env var defaults
        app.state.config_cache = config_cache

        # HIS Intelligence Feedback client
        his_feedback_client = None
        if config.his_feedback_enabled:
            from src.processing.his_feedback_client import HISFeedbackClient
            his_feedback_client = HISFeedbackClient(
                base_url=config.his_base_url,
                api_key=config.his_api_key,
                timeout=config.his_feedback_timeout,
            )
        app.state.his_feedback_client = his_feedback_client

        # WS6: Audit logger (fire-and-forget, pool-based) — created early so
        # downstream services can use it
        audit_logger = AuditLogger(pool)
        app.state.audit_logger = audit_logger

        # WS6: Notification service
        notification_service = NotificationService(pool, sse_manager)
        app.state.notification_service = notification_service

        # WS7: Register scheduled report jobs (needs heartbeat_client,
        # notification_service, sse_manager, audit_logger — all created above)
        if scheduler:
            set_scheduler_deps(
                pool, event_ledger=event_ledger,
                heartbeat_client=heartbeat_client,
                notification_service=notification_service,
                sse_manager=sse_manager,
                audit_logger=audit_logger,
            )
            await register_ws7_jobs(
                scheduler, pool,
                heartbeat_client=heartbeat_client,
                notification_service=notification_service,
                sse_manager=sse_manager,
                audit_logger=audit_logger,
            )
            await scheduler.start_in_background()

        # WS6: Metrics collector (background gauge updates)
        metrics_collector = MetricsCollector(
            pool, interval=config.metrics_collect_interval
        )
        await metrics_collector.start()
        app.state.metrics_collector = metrics_collector

        # WS6: Set core_info Prometheus gauge
        core_info.info({"version": __version__, "schema_version": "2.1.1.0"})

        # WS1: Parser registry
        parser_registry = create_default_registry()
        app.state.parser_registry = parser_registry

        # WS1: Queue scanner (safety-net background task)
        queue_scanner = QueueScanner(
            pool=pool,
            config=config,
            sse_manager=sse_manager,
            heartbeat_client=heartbeat_client,
            parser_registry=parser_registry,
            audit_logger=audit_logger,
            notification_service=notification_service,
        )
        await queue_scanner.start()
        app.state.queue_scanner = queue_scanner

        # WS4: Entity event listener (pg_notify → SSE)
        entity_listener = EntityEventListener(pool, sse_manager)
        await entity_listener.start()
        app.state.entity_listener = entity_listener

        # WS6: Audit system.startup
        await audit_logger.log(
            event_type="system.startup",
            entity_type="system",
            action="PROCESS",
            metadata={"version": __version__, "port": config.port},
        )

        logger.info(
            "core_service_started",
            version=__version__,
            port=config.port,
        )

        yield

        # ── SHUTDOWN ─────────────────────────────────────────────────
        # WS6: Audit system.shutdown
        uptime = time.monotonic() - app.state.start_time
        await audit_logger.log(
            event_type="system.shutdown",
            entity_type="system",
            action="PROCESS",
            metadata={"uptime_seconds": round(uptime, 1)},
        )
        await metrics_collector.stop()

        await entity_listener.stop()
        await queue_scanner.stop()
        await heartbeat_client.close()
        if his_feedback_client:
            await his_feedback_client.close()
        if scheduler:
            try:
                await scheduler.__aexit__(None, None, None)
            except Exception:
                pass
        await sse_manager.stop_heartbeat()
        await sse_manager.drain()
        await close_pool(pool)
        logger.info("core_service_stopped")

    app = FastAPI(
        title="Helium Core Service",
        version=__version__,
        lifespan=lifespan,
    )

    # Middleware (LIFO order: last added = outermost = runs first)
    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    configure_cors(app, config)
    app.add_middleware(EntityAuditMiddleware)  # WS6: entity CRUD audit
    app.add_middleware(PrometheusMiddleware)  # outermost — wraps all requests

    # Error handler
    @app.exception_handler(CoreError)
    async def core_error_handler(request: Request, exc: CoreError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    # Routers
    app.include_router(health_router)
    app.include_router(sse_router)
    app.include_router(ingestion_router)

    # WS4: Entity CRUD + Search routers
    app.include_router(invoices_router)
    app.include_router(customers_router)
    app.include_router(inventory_router)
    app.include_router(entities_router)
    app.include_router(search_router)

    # WS6: Observability (audit, notifications, metrics)
    app.include_router(observability_router)

    # WS7: Reports & Statistics
    app.include_router(reports_router)
    app.include_router(statistics_router)

    # Webhook (HeartBeat config change notifications)
    app.include_router(webhook_router)

    return app
