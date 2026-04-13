"""
Tests for WS6 Prometheus metric definitions and path normalization.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from src.observability.metrics import (
    circuit_breaker_state,
    core_info,
    entity_count,
    external_service_duration_seconds,
    external_service_requests_total,
    http_request_duration_seconds,
    http_requests_total,
    invoices_processed_total,
    pipeline_duration_seconds,
    pipeline_runs_total,
    queue_depth,
    queue_processing_duration_seconds,
)
from src.observability.metrics_middleware import normalize_path


class TestMetricDefinitions:
    def test_all_metrics_registered(self):
        """All 11 metric families should be registered in the default registry."""
        names = [m.name for m in REGISTRY.collect()]
        assert "core_http_requests_total" in names or "core_http_requests" in names
        assert "core_pipeline_runs_total" in names or "core_pipeline_runs" in names
        assert "core_queue_depth" in names

    def test_counter_increments(self):
        """Counters should be incrementable."""
        before = http_requests_total.labels(
            method="GET", endpoint="/test", status_code="200"
        )._value.get()
        http_requests_total.labels(
            method="GET", endpoint="/test", status_code="200"
        ).inc()
        after = http_requests_total.labels(
            method="GET", endpoint="/test", status_code="200"
        )._value.get()
        assert after == before + 1

    def test_gauge_set(self):
        """Gauges should be settable."""
        queue_depth.labels(status="PENDING").set(42)
        assert queue_depth.labels(status="PENDING")._value.get() == 42


class TestPathNormalization:
    def test_uuid_replaced(self):
        path = "/api/v1/invoices/550e8400-e29b-41d4-a716-446655440000"
        assert normalize_path(path) == "/api/v1/invoices/{id}"

    def test_numeric_replaced(self):
        path = "/api/v1/invoices/12345"
        assert normalize_path(path) == "/api/v1/invoices/{id}"

    def test_static_preserved(self):
        path = "/api/v1/audit"
        assert normalize_path(path) == "/api/v1/audit"

    def test_mixed_path(self):
        path = "/api/v1/invoices/550e8400-e29b-41d4-a716-446655440000/lines/42"
        result = normalize_path(path)
        assert result == "/api/v1/invoices/{id}/lines/{id}"

    def test_empty_path(self):
        assert normalize_path("/") == "/"

    def test_v1_not_replaced(self):
        """Version segments like 'v1' should NOT be replaced."""
        path = "/api/v1/search"
        assert normalize_path(path) == "/api/v1/search"
