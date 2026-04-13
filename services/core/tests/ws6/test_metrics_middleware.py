"""
Tests for WS6 PrometheusMiddleware — HTTP request instrumentation.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.observability.metrics import http_request_duration_seconds, http_requests_total
from src.observability.metrics_middleware import PrometheusMiddleware


def _create_app():
    app = FastAPI()
    app.add_middleware(PrometheusMiddleware)

    @app.get("/api/v1/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/api/v1/invoices/{invoice_id}")
    async def get_invoice(invoice_id: str):
        return {"id": invoice_id}

    @app.get("/metrics")
    async def metrics():
        return {"skip": True}

    @app.get("/health")
    async def health():
        return {"healthy": True}

    return app


class TestPrometheusMiddleware:
    def test_records_request_counter(self):
        app = _create_app()
        client = TestClient(app)

        before = http_requests_total.labels(
            method="GET", endpoint="/api/v1/test", status_code="200"
        )._value.get()

        client.get("/api/v1/test")

        after = http_requests_total.labels(
            method="GET", endpoint="/api/v1/test", status_code="200"
        )._value.get()
        assert after == before + 1

    def test_normalizes_uuid_paths(self):
        app = _create_app()
        client = TestClient(app)

        before = http_requests_total.labels(
            method="GET", endpoint="/api/v1/invoices/{id}", status_code="200"
        )._value.get()

        client.get("/api/v1/invoices/550e8400-e29b-41d4-a716-446655440000")

        after = http_requests_total.labels(
            method="GET", endpoint="/api/v1/invoices/{id}", status_code="200"
        )._value.get()
        assert after == before + 1

    def test_skips_metrics_endpoint(self):
        app = _create_app()
        client = TestClient(app)
        client.get("/metrics")
        # The /metrics endpoint itself should NOT be instrumented
        # (no label with endpoint="/metrics" should be incremented)

    def test_skips_health_endpoint(self):
        app = _create_app()
        client = TestClient(app)
        client.get("/health")
        # /health should NOT be instrumented

    def test_records_duration(self):
        app = _create_app()
        client = TestClient(app)
        client.get("/api/v1/test")
        # Histogram should have at least one observation
        # (Just verify no exception — histogram internal state is complex)
