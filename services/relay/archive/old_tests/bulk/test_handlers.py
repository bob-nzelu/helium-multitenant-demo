"""
Integration Tests for FastAPI HTTP Handlers

Tests the HTTP endpoints:
- POST /api/ingest
- POST /api/finalize
- GET /api/status/{queue_id}
- GET /health
- GET /metrics

Target Coverage: 95%+
"""

import pytest
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from io import BytesIO
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthEndpoint:
    """Tests for GET /health endpoint."""

    def test_health_returns_200(self, test_client):
        """Health endpoint should return 200 OK."""
        response = test_client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status(self, test_client):
        """Health response should include status."""
        response = test_client.get("/health")
        data = response.json()

        assert "status" in data
        assert data["status"] in ["healthy", "degraded"]

    def test_health_returns_instance_id(self, test_client):
        """Health response should include instance ID."""
        response = test_client.get("/health")
        data = response.json()

        assert "instance_id" in data
        assert data["instance_id"] == "relay-bulk-test-1"

    def test_health_returns_services_status(self, test_client):
        """Health response should include services status."""
        response = test_client.get("/health")
        data = response.json()

        assert "services" in data
        assert "core_api" in data["services"]
        assert "heartbeat" in data["services"]

    def test_health_includes_trace_id(self, test_client):
        """Health response should include trace ID in headers."""
        response = test_client.get("/health")
        assert "X-Trace-ID" in response.headers


# =============================================================================
# Metrics Endpoint Tests
# =============================================================================

class TestMetricsEndpoint:
    """Tests for GET /metrics endpoint."""

    def test_metrics_returns_200(self, test_client):
        """Metrics endpoint should return 200 OK."""
        response = test_client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_placeholder_message(self, test_client):
        """Metrics currently returns placeholder (to be implemented)."""
        response = test_client.get("/metrics")
        data = response.json()

        # Phase 1B left this as placeholder
        assert "status" in data
        assert data["status"] == "not_implemented"


# =============================================================================
# Status Endpoint Tests
# =============================================================================

class TestStatusEndpoint:
    """Tests for GET /api/status/{queue_id} endpoint."""

    def test_status_returns_200(self, test_client):
        """Status endpoint should return 200 OK."""
        response = test_client.get("/api/status/queue_123")
        assert response.status_code == 200

    def test_status_includes_queue_id(self, test_client):
        """Status response should include queue_id."""
        response = test_client.get("/api/status/queue_abc")
        data = response.json()

        assert "queue_id" in data
        assert data["queue_id"] == "queue_abc"

    def test_status_placeholder_message(self, test_client):
        """Status currently returns placeholder (to be implemented in Phase 1C)."""
        response = test_client.get("/api/status/queue_123")
        data = response.json()

        # Phase 1B left this as placeholder
        assert data["status"] == "not_implemented"


# =============================================================================
# Ingest Endpoint Tests
# =============================================================================

class TestIngestEndpoint:
    """Tests for POST /api/ingest endpoint."""

    def _make_auth_headers(self, api_key_secrets, body: bytes, api_key: str = "test_api_key_123"):
        """Helper to create valid auth headers."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        secret = api_key_secrets[api_key]
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-API-Key": api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def test_ingest_missing_auth_returns_401(self, test_client, sample_pdf_content):
        """Missing auth headers should return 401."""
        response = test_client.post(
            "/api/ingest",
            files={"files": ("invoice.pdf", sample_pdf_content)},
            data={"company_id": "company_123"},
        )

        # FastAPI returns 422 for missing required headers
        assert response.status_code in [401, 422]

    def test_ingest_invalid_api_key_returns_401(
        self, test_client, sample_pdf_content, api_key_secrets
    ):
        """Invalid API key should return 401."""
        # Create headers with invalid API key
        body = b"test"  # Note: actual body will differ due to multipart
        headers = {
            "X-API-Key": "invalid_key",
            "X-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "X-Signature": "invalid_signature",
        }

        response = test_client.post(
            "/api/ingest",
            files={"files": ("invoice.pdf", sample_pdf_content)},
            data={"company_id": "company_123"},
            headers=headers,
        )

        assert response.status_code == 401
        data = response.json()
        assert data["error_code"] == "INVALID_API_KEY"

    def test_ingest_expired_timestamp_returns_401(
        self, test_client, sample_pdf_content, api_key_secrets
    ):
        """Expired timestamp should return 401."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        # 10 minutes ago (outside 5-minute window)
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        timestamp = expired_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        body = b"test"
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "X-API-Key": api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

        response = test_client.post(
            "/api/ingest",
            files={"files": ("invoice.pdf", sample_pdf_content)},
            data={"company_id": "company_123"},
            headers=headers,
        )

        assert response.status_code == 401
        data = response.json()
        assert data["error_code"] == "TIMESTAMP_EXPIRED"

    def test_ingest_no_files_returns_400(self, test_client, api_key_secrets):
        """No files should return 400."""
        # Note: FastAPI may return 422 if files field is required
        body = b"test"
        headers = self._make_auth_headers(api_key_secrets, body)

        response = test_client.post(
            "/api/ingest",
            data={"company_id": "company_123"},
            headers=headers,
        )

        # Could be 400 or 422 depending on FastAPI validation
        assert response.status_code in [400, 422]

    def test_ingest_invalid_extension_returns_400(self, test_client, api_key_secrets):
        """Invalid file extension should return 400."""
        body = b"test"
        headers = self._make_auth_headers(api_key_secrets, body)

        response = test_client.post(
            "/api/ingest",
            files={"files": ("malware.exe", b"MZ\x90")},
            data={"company_id": "company_123"},
            headers=headers,
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "INVALID_FILE_TYPE"

    def test_ingest_too_many_files_returns_400(
        self, test_client, api_key_secrets, sample_pdf_content
    ):
        """More than 3 files should return 400."""
        body = b"test"
        headers = self._make_auth_headers(api_key_secrets, body)

        # Create 4 files (exceeds limit of 3)
        files = [
            ("files", ("file1.pdf", sample_pdf_content)),
            ("files", ("file2.pdf", sample_pdf_content + b"1")),
            ("files", ("file3.pdf", sample_pdf_content + b"2")),
            ("files", ("file4.pdf", sample_pdf_content + b"3")),
        ]

        response = test_client.post(
            "/api/ingest",
            files=files,
            data={"company_id": "company_123"},
            headers=headers,
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "TOO_MANY_FILES"

    def test_ingest_includes_trace_id(self, test_client, api_key_secrets, sample_pdf_content):
        """Response should include trace ID."""
        body = b"test"
        headers = self._make_auth_headers(api_key_secrets, body)

        response = test_client.post(
            "/api/ingest",
            files={"files": ("invoice.pdf", sample_pdf_content)},
            data={"company_id": "company_123"},
            headers=headers,
        )

        assert "X-Trace-ID" in response.headers


# =============================================================================
# Finalize Endpoint Tests
# =============================================================================

class TestFinalizeEndpoint:
    """Tests for POST /api/finalize endpoint."""

    def _make_auth_headers(self, api_key_secrets, body: bytes, api_key: str = "test_api_key_123"):
        """Helper to create valid auth headers."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        secret = api_key_secrets[api_key]
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return {
            "X-API-Key": api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def test_finalize_missing_auth_returns_401(self, test_client):
        """Missing auth headers should return 401/422."""
        body = json.dumps({
            "batch_id": "batch_123",
            "queue_ids": ["queue_1"],
        }).encode()

        response = test_client.post(
            "/api/finalize",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code in [401, 422]

    def test_finalize_invalid_api_key_returns_401(self, test_client, api_key_secrets):
        """Invalid API key should return 401."""
        body = json.dumps({
            "batch_id": "batch_123",
            "queue_ids": ["queue_1"],
        }).encode()

        headers = {
            "X-API-Key": "invalid_key",
            "X-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "X-Signature": "invalid_signature",
            "Content-Type": "application/json",
        }

        response = test_client.post(
            "/api/finalize",
            content=body,
            headers=headers,
        )

        assert response.status_code == 401

    def test_finalize_missing_batch_id_returns_422(self, test_client, api_key_secrets):
        """Missing batch_id should return 422."""
        body = json.dumps({
            "queue_ids": ["queue_1"],
        }).encode()

        headers = self._make_auth_headers(api_key_secrets, body)
        headers["Content-Type"] = "application/json"

        response = test_client.post(
            "/api/finalize",
            content=body,
            headers=headers,
        )

        assert response.status_code == 422

    def test_finalize_missing_queue_ids_returns_422(self, test_client, api_key_secrets):
        """Missing queue_ids should return 422."""
        body = json.dumps({
            "batch_id": "batch_123",
        }).encode()

        headers = self._make_auth_headers(api_key_secrets, body)
        headers["Content-Type"] = "application/json"

        response = test_client.post(
            "/api/finalize",
            content=body,
            headers=headers,
        )

        assert response.status_code == 422


# =============================================================================
# Error Handler Tests
# =============================================================================

class TestErrorHandlers:
    """Tests for custom error handlers."""

    def test_trace_id_in_error_response(self, test_client, api_key_secrets):
        """Error responses should include request_id (trace_id)."""
        body = b"test"
        headers = {
            "X-API-Key": "invalid_key",
            "X-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "X-Signature": "invalid_signature",
        }

        response = test_client.post(
            "/api/ingest",
            files={"files": ("test.pdf", b"content")},
            data={"company_id": "company_123"},
            headers=headers,
        )

        data = response.json()
        assert "request_id" in data

    def test_error_response_format(self, test_client, api_key_secrets):
        """Error responses should follow standard format."""
        headers = {
            "X-API-Key": "invalid_key",
            "X-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "X-Signature": "invalid_signature",
        }

        response = test_client.post(
            "/api/ingest",
            files={"files": ("test.pdf", b"content")},
            data={"company_id": "company_123"},
            headers=headers,
        )

        data = response.json()

        # Standard error format
        assert "status" in data
        assert data["status"] == "error"
        assert "error_code" in data
        assert "message" in data
        assert "timestamp" in data


# =============================================================================
# Middleware Tests
# =============================================================================

class TestMiddleware:
    """Tests for middleware functionality."""

    def test_trace_id_generated_when_missing(self, test_client):
        """Trace ID should be generated if not provided."""
        response = test_client.get("/health")
        trace_id = response.headers.get("X-Trace-ID")

        assert trace_id is not None
        assert trace_id.startswith("req_")

    def test_trace_id_preserved_when_provided(self, test_client):
        """Provided trace ID should be preserved."""
        custom_trace_id = "custom-trace-123"
        response = test_client.get(
            "/health",
            headers={"X-Trace-ID": custom_trace_id}
        )

        assert response.headers.get("X-Trace-ID") == custom_trace_id
