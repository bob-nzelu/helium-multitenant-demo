"""
Tests for Audit API (/api/audit/*)

Tests POST /api/audit/log endpoint.
"""

import pytest


class TestAuditLog:
    """Tests for POST /api/audit/log."""

    def test_log_audit_event(self, client, mock_storage):
        """Log audit event returns status=logged and audit_id."""
        response = client.post("/api/audit/log", json={
            "service": "relay-api",
            "event_type": "file.ingested",
            "user_id": "user-123",
            "details": {"filename": "invoice.pdf", "size": 2048},
        })
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "logged"
        assert isinstance(data["audit_id"], int)
        assert data["audit_id"] > 0

    def test_log_audit_minimal(self, client, mock_storage):
        """Audit event with only required fields succeeds."""
        response = client.post("/api/audit/log", json={
            "service": "core",
            "event_type": "batch.completed",
        })
        assert response.status_code == 201
        assert response.json()["status"] == "logged"

    def test_log_audit_with_trace(self, client, mock_storage):
        """Audit event with trace_id for correlation."""
        response = client.post("/api/audit/log", json={
            "service": "relay-api",
            "event_type": "auth.failed",
            "trace_id": "trace-abc-123",
            "ip_address": "192.168.1.1",
            "details": {"reason": "invalid_api_key"},
        })
        assert response.status_code == 201

    def test_log_audit_sequential_ids(self, client, mock_storage):
        """Multiple audit events get sequential IDs."""
        ids = []
        for i in range(3):
            resp = client.post("/api/audit/log", json={
                "service": "relay-api",
                "event_type": f"event.{i}",
            })
            ids.append(resp.json()["audit_id"])

        assert ids[1] > ids[0]
        assert ids[2] > ids[1]

    def test_log_audit_missing_service(self, client, mock_storage):
        """Missing required 'service' field returns 422."""
        response = client.post("/api/audit/log", json={
            "event_type": "file.ingested",
        })
        assert response.status_code == 422

    def test_log_audit_missing_event_type(self, client, mock_storage):
        """Missing required 'event_type' field returns 422."""
        response = client.post("/api/audit/log", json={
            "service": "relay-api",
        })
        assert response.status_code == 422

    def test_log_audit_complex_details(self, client, mock_storage):
        """Details dict with nested data stores correctly."""
        response = client.post("/api/audit/log", json={
            "service": "core",
            "event_type": "processing.complete",
            "details": {
                "blob_uuid": "abc-123",
                "outputs": ["firs_invoices", "report"],
                "duration_seconds": 12.5,
            },
        })
        assert response.status_code == 201
