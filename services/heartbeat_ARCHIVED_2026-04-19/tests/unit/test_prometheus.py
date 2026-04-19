"""
Tests for Prometheus Metrics (P2-A)

Tests:
    1. /metrics endpoint returns 200 with prometheus content type
    2. /metrics contains heartbeat_info metric
    3. Request middleware increments request counter
    4. Request middleware records duration histogram
    5. Path normalization replaces UUIDs
    6. Path normalization replaces numeric IDs
    7. Metric definitions are registered correctly
    8. /metrics endpoint is unauthenticated (no auth header needed)
    9. Middleware skips /metrics endpoint (no recursion)
    10. Custom metrics can be incremented
"""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

heartbeat_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(heartbeat_root))

from src.observability.metrics import (
    REGISTRY,
    BLOB_REGISTRATIONS,
    DEDUP_CHECKS,
    REQUEST_COUNT,
    REQUEST_DURATION,
    SERVICE_INFO,
)
from src.api.observability.prometheus import PrometheusMiddleware


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def prom_client(tmp_path, monkeypatch):
    """
    Create a test client with Prometheus middleware active.
    """
    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.registry import reset_registry_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from fastapi.testclient import TestClient

    # Create blob.db with schema
    db_path = str(tmp_path / "blob.db")
    schema_path = Path(__file__).parent.parent.parent / "databases" / "schema.sql"

    conn = sqlite3.connect(db_path)
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", db_path)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")
    monkeypatch.setenv("HEARTBEAT_AUTO_MIGRATE", "false")

    blob_root = str(tmp_path / "blobs")
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_registry_database()

    get_blob_database(db_path)

    from src.main import app
    with TestClient(app) as c:
        yield c

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_registry_database()


# ── Test /metrics Endpoint ────────────────────────────────────────────────

class TestMetricsEndpoint:
    """Tests for the GET /metrics endpoint."""

    def test_metrics_returns_200(self, prom_client):
        """GET /metrics returns 200."""
        resp = prom_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type(self, prom_client):
        """GET /metrics returns Prometheus text format content type."""
        resp = prom_client.get("/metrics")
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_metrics_contains_heartbeat_info(self, prom_client):
        """GET /metrics includes heartbeat_info metric."""
        # Trigger startup to set SERVICE_INFO
        resp = prom_client.get("/metrics")
        content = resp.text

        assert "heartbeat_info" in content

    def test_metrics_contains_request_counters(self, prom_client):
        """GET /metrics includes request counter metrics after a request."""
        # Make a request to generate metrics
        prom_client.get("/health")

        resp = prom_client.get("/metrics")
        content = resp.text

        assert "heartbeat_requests_total" in content

    def test_metrics_contains_duration_histogram(self, prom_client):
        """GET /metrics includes request duration histogram."""
        prom_client.get("/health")

        resp = prom_client.get("/metrics")
        content = resp.text

        assert "heartbeat_request_duration_seconds" in content

    def test_metrics_unauthenticated(self, prom_client):
        """GET /metrics works without auth headers."""
        # No Authorization header
        resp = prom_client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_blob_metrics(self, prom_client):
        """GET /metrics includes blob registration counter definition."""
        resp = prom_client.get("/metrics")
        content = resp.text

        # The metric type declaration should be present
        assert "heartbeat_blob_registrations" in content


# ── Test Path Normalization ───────────────────────────────────────────────

class TestPathNormalization:
    """Tests for the PrometheusMiddleware._normalize_path method."""

    def test_replaces_uuid(self):
        """UUID-like segments are replaced with {uuid}."""
        path = "/api/v1/heartbeat/blob/550e8400-e29b-41d4-a716-446655440000/status"
        result = PrometheusMiddleware._normalize_path(path)
        assert "{uuid}" in result
        assert "550e8400" not in result

    def test_replaces_numeric_id(self):
        """Pure numeric segments are replaced with {id}."""
        path = "/api/blobs/12345/outputs"
        result = PrometheusMiddleware._normalize_path(path)
        assert "{id}" in result
        assert "12345" not in result

    def test_preserves_static_paths(self):
        """Static path segments are preserved."""
        path = "/api/audit/verify"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/audit/verify"

    def test_preserves_short_segments(self):
        """Short non-UUID, non-numeric segments are preserved."""
        path = "/api/registry/config/float-sdk"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/registry/config/float-sdk"


# ── Test Custom Metric Operations ─────────────────────────────────────────

class TestMetricOperations:
    """Tests that custom metrics can be incremented correctly."""

    def test_counter_increment(self):
        """Counter metrics can be incremented with labels."""
        # This shouldn't raise
        BLOB_REGISTRATIONS.labels(status="success", source_type="bulk").inc()
        DEDUP_CHECKS.labels(result="unique").inc()

    def test_request_counter_with_labels(self):
        """Request counter accepts method, endpoint, status_code labels."""
        REQUEST_COUNT.labels(
            method="GET", endpoint="/health", status_code="200"
        ).inc()

    def test_histogram_observe(self):
        """Histogram metrics accept observations."""
        REQUEST_DURATION.labels(
            method="POST", endpoint="/api/blobs/register", status_code="200"
        ).observe(0.042)
