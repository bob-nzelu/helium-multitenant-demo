"""Tests for middleware — TraceID, logging, CORS."""

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI, Request

from src.middleware.trace_id import TraceIDMiddleware
from src.middleware.logging import RequestLoggingMiddleware
from src.middleware.cors import configure_cors
from src.config import CoreConfig


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with middleware for testing."""
    app = FastAPI()

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/metrics")
    async def metrics():
        return {"metrics": "ok"}

    @app.get("/test")
    async def test_endpoint(request: Request):
        trace_id = request.state.trace_id if hasattr(request.state, "trace_id") else None
        return {"trace_id": trace_id}

    app.add_middleware(TraceIDMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    configure_cors(app, CoreConfig())
    return app


@pytest.fixture
def app():
    return _make_test_app()


@pytest.mark.asyncio
class TestTraceIDMiddleware:
    """Test X-Trace-ID injection and passthrough."""

    async def test_generates_trace_id_when_absent(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/test")
        assert resp.status_code == 200
        assert "x-trace-id" in resp.headers
        assert len(resp.headers["x-trace-id"]) > 0

    async def test_preserves_trace_id_when_present(self, app):
        my_trace = "my-custom-trace-id-123"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/test", headers={"X-Trace-ID": my_trace})
        assert resp.headers["x-trace-id"] == my_trace
        body = resp.json()
        assert body["trace_id"] == my_trace

    async def test_trace_id_in_response_body(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/test")
        body = resp.json()
        assert body["trace_id"] is not None
        assert body["trace_id"] == resp.headers["x-trace-id"]


@pytest.mark.asyncio
class TestRequestLoggingMiddleware:
    """Test request logging skips health/metrics."""

    async def test_health_endpoint_works(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    async def test_metrics_endpoint_works(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/metrics")
        assert resp.status_code == 200

    async def test_normal_endpoint_works(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/test")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestCORSMiddleware:
    """Test CORS headers."""

    async def test_cors_headers_present(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.options(
                "/test",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    async def test_cors_wildcard_in_dev(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/test", headers={"Origin": "http://anything.com"})
        assert resp.headers.get("access-control-allow-origin") == "*"

    async def test_cors_custom_origins(self):
        config = CoreConfig(cors_origins="http://app.helium.ng,http://localhost:3000")
        app = FastAPI()

        @app.get("/test")
        async def test_ep():
            return {"ok": True}

        configure_cors(app, config)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/test",
                headers={"Origin": "http://app.helium.ng"},
            )
        assert resp.headers.get("access-control-allow-origin") == "http://app.helium.ng"
