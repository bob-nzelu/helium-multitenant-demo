"""Tests for health and metrics endpoints."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from src.health import router as health_router
from src.models import HealthResponse


def _make_health_app(
    db_ok: bool = True,
    scheduler_ok: bool = True,
) -> FastAPI:
    """Create minimal app with health router and mocked state."""
    app = FastAPI()
    app.include_router(health_router)

    # Mock pool
    if db_ok:
        pool = MagicMock()
        pool.connection = _make_async_cm()
        app.state.pool = pool
    else:
        app.state.pool = None

    # Mock scheduler
    app.state.scheduler = MagicMock() if scheduler_ok else None

    # Start time
    app.state.start_time = time.monotonic()

    return app


def _make_async_cm():
    """Create mock async context manager for pool.connection()."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_conn():
        conn = AsyncMock()
        yield conn

    return mock_conn


@pytest.mark.asyncio
class TestHealthEndpoint:
    """Test GET /api/v1/health with all status combinations."""

    async def test_healthy(self):
        app = _make_health_app(db_ok=True, scheduler_ok=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["database"] == "connected"
        assert body["scheduler"] == "running"
        assert "version" in body
        assert "uptime_seconds" in body

    async def test_degraded_no_scheduler(self):
        app = _make_health_app(db_ok=True, scheduler_ok=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "connected"
        assert body["scheduler"] == "stopped"

    async def test_degraded_no_database(self):
        app = _make_health_app(db_ok=False, scheduler_ok=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "disconnected"
        assert body["scheduler"] == "running"

    async def test_unhealthy(self):
        app = _make_health_app(db_ok=False, scheduler_ok=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unhealthy"

    async def test_version_matches(self):
        from src import __version__
        app = _make_health_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.json()["version"] == __version__

    async def test_uptime_positive(self):
        app = _make_health_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.json()["uptime_seconds"] >= 0


@pytest.mark.asyncio
class TestMetricsEndpoint:
    """Test GET /api/v1/metrics."""

    async def test_metrics_returns_prometheus_format(self):
        app = FastAPI()
        app.include_router(health_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    async def test_metrics_contains_core_metrics(self):
        app = FastAPI()
        app.include_router(health_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/metrics")
        body = resp.text
        assert "helium_core_files_processed_total" in body or "helium_core" in body
