"""
Tests for Metrics API (/api/metrics/*)

Tests POST /api/metrics/report endpoint.
"""

import pytest


class TestMetricsReport:
    """Tests for POST /api/metrics/report."""

    def test_report_ingestion_metrics(self, client, mock_storage):
        """Report ingestion metrics returns status=recorded."""
        response = client.post("/api/metrics/report", json={
            "metric_type": "ingestion",
            "values": {
                "files_count": 5,
                "total_size_mb": 12.5,
                "duration_s": 3.2,
            },
        })
        assert response.status_code == 201
        assert response.json()["status"] == "recorded"

    def test_report_error_metrics(self, client, mock_storage):
        """Report error metrics."""
        response = client.post("/api/metrics/report", json={
            "metric_type": "error",
            "values": {
                "error_code": "VALIDATION_FAILED",
                "error_count": 3,
                "service": "relay-api",
            },
        })
        assert response.status_code == 201

    def test_report_performance_metrics(self, client, mock_storage):
        """Report performance metrics."""
        response = client.post("/api/metrics/report", json={
            "metric_type": "performance",
            "values": {
                "avg_processing_s": 2.1,
                "p95_processing_s": 5.8,
                "queue_depth": 12,
            },
            "reported_by": "relay-bulk-1",
        })
        assert response.status_code == 201

    def test_report_metrics_missing_type(self, client, mock_storage):
        """Missing metric_type returns 422."""
        response = client.post("/api/metrics/report", json={
            "values": {"count": 1},
        })
        assert response.status_code == 422

    def test_report_metrics_missing_values(self, client, mock_storage):
        """Missing values returns 422."""
        response = client.post("/api/metrics/report", json={
            "metric_type": "ingestion",
        })
        assert response.status_code == 422

    def test_report_multiple_metrics(self, client, mock_storage):
        """Multiple metric reports all succeed."""
        for i in range(5):
            resp = client.post("/api/metrics/report", json={
                "metric_type": "ingestion",
                "values": {"files_count": i + 1},
            })
            assert resp.status_code == 201
