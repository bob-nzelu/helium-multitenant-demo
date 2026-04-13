"""
Pytest Configuration and Fixtures for Relay Service Tests

This module provides:
- Mock clients for Core, HeartBeat, and Audit APIs
- Common test fixtures (files, configurations, auth headers)
- Test factory functions for creating test data
- Async test support via pytest-asyncio

Usage:
    @pytest.mark.asyncio
    async def test_something(mock_core_client, mock_heartbeat_client):
        result = await service.do_something()
        assert result["status"] == "success"
"""

import pytest
import sys
import os
import hashlib
import hmac
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple, Optional
from unittest.mock import AsyncMock, Mock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# =============================================================================
# Mock Clients
# =============================================================================

class MockCoreAPIClient:
    """
    Mock Core API Client for testing.

    Core API is NOT IMPLEMENTED, so we mock all responses.
    """

    def __init__(self):
        self.enqueue_calls = []
        self.process_preview_calls = []
        self.finalize_calls = []
        self.get_status_calls = []

        # Default responses (can be overridden per test)
        self.enqueue_response = None
        self.process_preview_response = None
        self.finalize_response = None
        self.get_status_response = None

        # Error simulation
        self.should_raise = None
        self.should_timeout = False

    async def enqueue(
        self,
        file_uuid: str,
        blob_path: str,
        original_filename: str,
        source: str,
        immediate_processing: bool = False,
    ) -> str:
        """Mock enqueue - returns queue_id"""
        self.enqueue_calls.append({
            "file_uuid": file_uuid,
            "blob_path": blob_path,
            "original_filename": original_filename,
            "source": source,
            "immediate_processing": immediate_processing,
        })

        if self.should_raise:
            raise self.should_raise

        if self.enqueue_response:
            return self.enqueue_response

        return f"queue_{uuid.uuid4().hex[:8]}"

    async def process_preview(self, queue_id: str) -> Dict[str, Any]:
        """Mock process_preview - returns preview data"""
        import asyncio

        self.process_preview_calls.append({"queue_id": queue_id})

        if self.should_timeout:
            await asyncio.sleep(1000)  # Will be cancelled by timeout

        if self.should_raise:
            raise self.should_raise

        if self.process_preview_response:
            return self.process_preview_response

        return {
            "status": "completed",
            "queue_id": queue_id,
            "statistics": {
                "invoices_processed": 1,
                "duplicates_detected": 0,
                "invoices_failed": 0,
                "red_flags": [],
            },
            "preview_data": {
                "invoices": [
                    {
                        "invoice_number": "INV-001",
                        "vendor": "Test Vendor",
                        "amount": 1000.00,
                        "currency": "NGN",
                        "date": "2026-01-31",
                    }
                ]
            }
        }

    async def finalize(
        self, queue_id: str, edits: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Mock finalize - returns finalization result"""
        self.finalize_calls.append({
            "queue_id": queue_id,
            "edits": edits,
        })

        if self.should_raise:
            raise self.should_raise

        if self.finalize_response:
            return self.finalize_response

        return {
            "status": "finalized",
            "queue_id": queue_id,
            "invoice_ids": [f"inv_{uuid.uuid4().hex[:8]}"],
            "message": "Invoice finalized successfully",
        }

    async def get_status(self, queue_id: str) -> Dict[str, Any]:
        """Mock get_status - returns processing status"""
        self.get_status_calls.append({"queue_id": queue_id})

        if self.should_raise:
            raise self.should_raise

        if self.get_status_response:
            return self.get_status_response

        return {
            "status": "completed",
            "queue_id": queue_id,
            "preview_available": True,
        }


class MockHeartBeatClient:
    """
    Mock HeartBeat Client for testing.

    HeartBeat is PARTIALLY IMPLEMENTED:
    - EXISTS: POST /api/v1/heartbeat/blob/register
    - MOCK: write_blob, check_daily_usage, check_duplicate, record_duplicate
    """

    def __init__(self):
        self.write_blob_calls = []
        self.register_blob_calls = []
        self.check_daily_usage_calls = []
        self.check_duplicate_calls = []
        self.record_duplicate_calls = []

        # Default responses
        self.write_blob_response = None
        self.check_daily_usage_response = None
        self.check_duplicate_response = None

        # Track registered duplicates (for test scenarios)
        self.registered_duplicates = set()

        # Error simulation
        self.should_raise = None
        self.should_be_unavailable = False

    async def write_blob(
        self,
        file_uuid: str,
        filename: str,
        data: bytes,
    ) -> str:
        """Mock write_blob - returns blob_path"""
        self.write_blob_calls.append({
            "file_uuid": file_uuid,
            "filename": filename,
            "data_size": len(data),
        })

        if self.should_be_unavailable:
            from src.services.errors import HeartBeatUnavailableError
            raise HeartBeatUnavailableError()

        if self.should_raise:
            raise self.should_raise

        if self.write_blob_response:
            return self.write_blob_response

        return f"/files_blob/{filename}"

    async def register_blob(
        self,
        file_uuid: str,
        blob_path: str,
        file_hash: str,
        company_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Mock register_blob - returns registration result"""
        self.register_blob_calls.append({
            "file_uuid": file_uuid,
            "blob_path": blob_path,
            "file_hash": file_hash,
            "company_id": company_id,
        })

        if self.should_raise:
            raise self.should_raise

        return {
            "status": "created",
            "blob_uuid": file_uuid,
            "retention_until": (datetime.now(timezone.utc) + timedelta(days=7*365)).isoformat(),
        }

    async def check_daily_usage(
        self,
        company_id: str,
        file_count: int,
    ) -> Dict[str, Any]:
        """Mock check_daily_usage - returns usage status"""
        self.check_daily_usage_calls.append({
            "company_id": company_id,
            "file_count": file_count,
        })

        if self.should_be_unavailable:
            # Graceful degradation - return allowed
            return {"status": "allowed", "degraded": True}

        if self.should_raise:
            raise self.should_raise

        if self.check_daily_usage_response:
            return self.check_daily_usage_response

        return {
            "status": "allowed",
            "company_id": company_id,
            "current_usage": 10,
            "daily_limit": 500,
            "remaining": 490,
            "resets_at": (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat() + "Z",
        }

    async def check_duplicate(self, file_hash: str) -> Dict[str, Any]:
        """Mock check_duplicate - returns duplicate status"""
        self.check_duplicate_calls.append({"file_hash": file_hash})

        if self.should_be_unavailable:
            # Graceful degradation - return not duplicate
            return {"is_duplicate": False, "degraded": True}

        if self.should_raise:
            raise self.should_raise

        if self.check_duplicate_response:
            return self.check_duplicate_response

        # Check if we've registered this as a duplicate
        if file_hash in self.registered_duplicates:
            return {
                "is_duplicate": True,
                "file_hash": file_hash,
                "queue_id": f"queue_original_{file_hash[:8]}",
                "original_upload_date": datetime.now(timezone.utc).isoformat() + "Z",
            }

        return {
            "is_duplicate": False,
            "file_hash": file_hash,
        }

    async def record_duplicate(
        self,
        file_hash: str,
        queue_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Mock record_duplicate - stores hash for future checks"""
        self.record_duplicate_calls.append({
            "file_hash": file_hash,
            "queue_id": queue_id,
        })

        if self.should_raise:
            raise self.should_raise

        self.registered_duplicates.add(file_hash)

        return {
            "status": "recorded",
            "file_hash": file_hash,
        }

    async def check_daily_limit(self, api_key: str) -> Dict[str, Any]:
        """Mock check_daily_limit (used by base.py)"""
        return {
            "limit_reached": False,
            "daily_limit": 500,
            "current_usage": 10,
        }


class MockAuditAPIClient:
    """Mock Audit API Client for testing."""

    def __init__(self):
        self.log_calls = []
        self.should_raise = None

    async def log(
        self,
        service: str,
        event_type: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mock log - records audit event"""
        self.log_calls.append({
            "service": service,
            "event_type": event_type,
            "user_id": user_id,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if self.should_raise:
            raise self.should_raise


# =============================================================================
# Pytest Fixtures - Mock Clients
# =============================================================================

@pytest.fixture
def mock_core_client():
    """Fixture providing a mock Core API client."""
    return MockCoreAPIClient()


@pytest.fixture
def mock_heartbeat_client():
    """Fixture providing a mock HeartBeat client."""
    return MockHeartBeatClient()


@pytest.fixture
def mock_audit_client():
    """Fixture providing a mock Audit API client."""
    return MockAuditAPIClient()


# =============================================================================
# Pytest Fixtures - Configuration
# =============================================================================

@pytest.fixture
def bulk_config():
    """Default configuration for bulk upload service."""
    return {
        "instance_id": "relay-bulk-test-1",
        "type": "bulk",
        "tier": "test",
        "max_files_per_request": 3,
        "max_file_size_mb": 10,
        "max_total_size_mb": 30,
        "allowed_extensions": [".pdf", ".xml", ".json", ".csv", ".xlsx"],
        "request_timeout_seconds": 300,
        "malware_scan_enabled": False,
        "malware_scanning": {
            "enabled": False,
            "clamd_host": "localhost",
            "clamd_port": 3310,
            "on_unavailable": "allow",
        },
    }


@pytest.fixture
def api_key_secrets():
    """API key to secret mapping for HMAC validation."""
    return {
        "test_api_key_123": "test_secret_abc123",
        "client_api_key_456": "client_secret_xyz789",
    }


# =============================================================================
# Pytest Fixtures - Test Data
# =============================================================================

@pytest.fixture
def sample_pdf_content():
    """Sample PDF file content (minimal valid PDF header)."""
    # Minimal PDF structure for testing
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


@pytest.fixture
def sample_csv_content():
    """Sample CSV file content."""
    return b"invoice_number,vendor,amount,currency,date\nINV-001,Test Vendor,1000.00,NGN,2026-01-31\n"


@pytest.fixture
def sample_json_content():
    """Sample JSON file content."""
    import json
    return json.dumps({
        "invoice_number": "INV-001",
        "vendor": "Test Vendor",
        "amount": 1000.00,
        "currency": "NGN",
        "date": "2026-01-31",
    }).encode()


@pytest.fixture
def sample_xml_content():
    """Sample XML file content."""
    return b'<?xml version="1.0"?>\n<invoice><number>INV-001</number><vendor>Test Vendor</vendor></invoice>'


@pytest.fixture
def sample_files(sample_pdf_content):
    """List of sample files for testing."""
    return [
        ("invoice1.pdf", sample_pdf_content),
    ]


@pytest.fixture
def multiple_files(sample_pdf_content, sample_csv_content):
    """Multiple files for batch testing."""
    return [
        ("invoice1.pdf", sample_pdf_content),
        ("data.csv", sample_csv_content),
    ]


@pytest.fixture
def oversized_file():
    """File that exceeds size limit (11MB)."""
    return ("large_file.pdf", b"x" * (11 * 1024 * 1024))


@pytest.fixture
def invalid_extension_file():
    """File with invalid extension."""
    return ("malware.exe", b"MZ\x90\x00")


# =============================================================================
# Pytest Fixtures - Authentication
# =============================================================================

def generate_hmac_signature(api_key: str, secret: str, timestamp: str, body: bytes) -> str:
    """Generate HMAC-SHA256 signature for request authentication."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return signature


@pytest.fixture
def valid_timestamp():
    """Current timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def expired_timestamp():
    """Timestamp that's 6 minutes old (outside 5-minute window)."""
    expired = datetime.now(timezone.utc) - timedelta(minutes=6)
    return expired.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def auth_headers(api_key_secrets, valid_timestamp):
    """
    Factory fixture for generating valid authentication headers.

    Usage:
        headers = auth_headers(body=b"request body")
    """
    def _make_headers(body: bytes, api_key: str = "test_api_key_123", timestamp: str = None):
        ts = timestamp or valid_timestamp
        secret = api_key_secrets[api_key]
        signature = generate_hmac_signature(api_key, secret, ts, body)

        return {
            "X-API-Key": api_key,
            "X-Timestamp": ts,
            "X-Signature": signature,
        }

    return _make_headers


# =============================================================================
# Pytest Fixtures - Service Instances
# =============================================================================

@pytest.fixture
def validation_pipeline(mock_heartbeat_client, bulk_config, api_key_secrets):
    """BulkValidationPipeline instance with mock dependencies."""
    from src.bulk.validation import BulkValidationPipeline

    return BulkValidationPipeline(
        heartbeat_client=mock_heartbeat_client,
        config=bulk_config,
        api_key_secrets=api_key_secrets,
        trace_id="test-trace-id",
    )


@pytest.fixture
def bulk_service(
    mock_core_client,
    mock_heartbeat_client,
    mock_audit_client,
    validation_pipeline,
    bulk_config,
):
    """RelayBulkService instance with mock dependencies."""
    from src.bulk.service import RelayBulkService

    return RelayBulkService(
        core_client=mock_core_client,
        heartbeat_client=mock_heartbeat_client,
        audit_client=mock_audit_client,
        validation_pipeline=validation_pipeline,
        config=bulk_config,
        trace_id="test-trace-id",
    )


# =============================================================================
# Pytest Fixtures - FastAPI Test Client
# =============================================================================

@pytest.fixture
def fastapi_app(bulk_service, validation_pipeline, bulk_config):
    """FastAPI application instance for testing."""
    from src.bulk.handlers import create_bulk_app

    return create_bulk_app(
        bulk_service=bulk_service,
        validation_pipeline=validation_pipeline,
        instance_id=bulk_config["instance_id"],
        config=bulk_config,
    )


@pytest.fixture
def test_client(fastapi_app):
    """
    FastAPI TestClient for HTTP endpoint testing.

    Note: Use httpx.AsyncClient for async tests.
    """
    from fastapi.testclient import TestClient
    return TestClient(fastapi_app)


@pytest.fixture
async def async_client(fastapi_app):
    """Async HTTP client for FastAPI testing."""
    from httpx import AsyncClient, ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://test"
    ) as client:
        yield client


# =============================================================================
# Test Helpers
# =============================================================================

def compute_file_hash(data: bytes) -> str:
    """Compute SHA256 hash of file data."""
    return hashlib.sha256(data).hexdigest()


def create_test_files(count: int, extension: str = ".pdf", size_bytes: int = 1000) -> List[Tuple[str, bytes]]:
    """Create multiple test files."""
    files = []
    for i in range(count):
        filename = f"test_file_{i+1}{extension}"
        # Use unique content to avoid dedup
        content = f"Test file {i+1} content - {uuid.uuid4()}".encode() + b"\x00" * (size_bytes - 50)
        files.append((filename, content))
    return files


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "load: marks tests as load tests"
    )
    config.addinivalue_line(
        "markers", "security: marks tests as security tests"
    )


# Enable async test support
pytest_plugins = ['pytest_asyncio']
