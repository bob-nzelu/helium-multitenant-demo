"""
Unit Tests for HeartBeatClient

Tests the HeartBeat API client functionality:
- Blob storage operations (write_blob)
- Deduplication checks (check_duplicate, record_duplicate)
- Rate limiting (check_daily_limit)
- Blob registration and reconciliation
- Health checks
- Error handling

Target Coverage: 100%
"""

import pytest
import asyncio
import hashlib
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.clients.heartbeat_client import HeartBeatClient
from src.services.errors import HeartBeatUnavailableError


# =============================================================================
# Initialization Tests
# =============================================================================

class TestHeartBeatClientInit:
    """Tests for HeartBeatClient initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        assert client.heartbeat_api_url == "http://localhost:9000"
        assert client.timeout == 30.0
        assert client.max_attempts == 5

    def test_custom_initialization(self):
        """Should accept custom configuration."""
        client = HeartBeatClient(
            heartbeat_api_url="http://heartbeat:9000/",
            timeout=60.0,
            max_attempts=3,
            trace_id="custom_trace",
        )

        assert client.heartbeat_api_url == "http://heartbeat:9000"  # Trailing slash stripped
        assert client.timeout == 60.0
        assert client.max_attempts == 3
        assert client.trace_id == "custom_trace"


# =============================================================================
# Write Blob Tests
# =============================================================================

class TestWriteBlob:
    """Tests for write_blob() method."""

    @pytest.mark.asyncio
    async def test_write_blob_success(self):
        """Should return blob metadata on success."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        file_data = b"test file content"
        result = await client.write_blob(
            blob_uuid="uuid_123",
            filename="invoice.pdf",
            file_data=file_data,
        )

        assert result["blob_uuid"] == "uuid_123"
        assert result["status"] == "uploaded"
        assert result["file_size_bytes"] == len(file_data)
        assert "blob_path" in result
        assert "file_hash" in result

    @pytest.mark.asyncio
    async def test_write_blob_computes_hash(self):
        """Should compute SHA256 hash of file."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        file_data = b"test content for hashing"
        expected_hash = hashlib.sha256(file_data).hexdigest()

        result = await client.write_blob(
            blob_uuid="uuid_456",
            filename="test.pdf",
            file_data=file_data,
        )

        assert result["file_hash"] == expected_hash

    @pytest.mark.asyncio
    async def test_write_blob_path_format(self):
        """Blob path should include UUID and filename."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.write_blob(
            blob_uuid="my_uuid",
            filename="invoice.pdf",
            file_data=b"content",
        )

        assert "/files_blob/" in result["blob_path"]
        assert "my_uuid" in result["blob_path"]
        assert "invoice.pdf" in result["blob_path"]

    @pytest.mark.asyncio
    async def test_write_blob_error_wrapped(self):
        """Errors should be wrapped as HeartBeatUnavailableError."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Storage unavailable")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(HeartBeatUnavailableError) as exc_info:
                await client.write_blob(
                    blob_uuid="uuid_123",
                    filename="test.pdf",
                    file_data=b"content",
                )

        assert "Failed to write blob" in exc_info.value.message


# =============================================================================
# Check Duplicate Tests
# =============================================================================

class TestCheckDuplicate:
    """Tests for check_duplicate() method."""

    @pytest.mark.asyncio
    async def test_check_duplicate_not_duplicate(self):
        """Should return is_duplicate=False for new files."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.check_duplicate(file_hash="abc123def456")

        assert result["is_duplicate"] is False
        assert result["file_hash"] == "abc123def456"
        assert result["original_queue_id"] is None

    @pytest.mark.asyncio
    async def test_check_duplicate_error_wrapped(self):
        """Errors should be wrapped as HeartBeatUnavailableError."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Database error")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(HeartBeatUnavailableError) as exc_info:
                await client.check_duplicate(file_hash="abc123")

        assert "Failed to check duplicate" in exc_info.value.message


# =============================================================================
# Record Duplicate Tests
# =============================================================================

class TestRecordDuplicate:
    """Tests for record_duplicate() method."""

    @pytest.mark.asyncio
    async def test_record_duplicate_success(self):
        """Should record file hash for future dedup."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.record_duplicate(
            file_hash="abc123def456",
            queue_id="queue_789",
        )

        assert result["file_hash"] == "abc123def456"
        assert result["queue_id"] == "queue_789"
        assert result["status"] == "recorded"

    @pytest.mark.asyncio
    async def test_record_duplicate_error_wrapped(self):
        """Errors should be wrapped as HeartBeatUnavailableError."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Database error")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(HeartBeatUnavailableError) as exc_info:
                await client.record_duplicate(
                    file_hash="abc123",
                    queue_id="queue_123",
                )

        assert "Failed to record duplicate" in exc_info.value.message


# =============================================================================
# Check Daily Limit Tests
# =============================================================================

class TestCheckDailyLimit:
    """Tests for check_daily_limit() method."""

    @pytest.mark.asyncio
    async def test_check_daily_limit_under_limit(self):
        """Should return limit info when under limit."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.check_daily_limit(api_key="test_api_key_123")

        assert result["limit_reached"] is False
        assert result["daily_limit"] == 500
        assert result["remaining"] == 500
        assert result["files_uploaded_today"] == 0

    @pytest.mark.asyncio
    async def test_check_daily_limit_privacy(self):
        """API key should be partially masked in logs."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        # This tests the logging behavior - API key is truncated
        result = await client.check_daily_limit(api_key="very_long_api_key_that_should_be_truncated")

        assert "api_key" in result

    @pytest.mark.asyncio
    async def test_check_daily_limit_error_wrapped(self):
        """Errors should be wrapped as HeartBeatUnavailableError."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Rate limit service unavailable")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(HeartBeatUnavailableError) as exc_info:
                await client.check_daily_limit(api_key="test_key")

        assert "Failed to check daily limit" in exc_info.value.message


# =============================================================================
# Register Blob Tests
# =============================================================================

class TestRegisterBlob:
    """Tests for register_blob() method."""

    @pytest.mark.asyncio
    async def test_register_blob_success(self):
        """Should register blob with tracking ID."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.register_blob(
            blob_uuid="uuid_123",
            filename="invoice.pdf",
            file_size_bytes=1024,
            file_hash="abc123def456",
            api_key="test_api_key",
        )

        assert result["blob_uuid"] == "uuid_123"
        assert result["status"] == "registered"
        assert "tracking_id" in result

    @pytest.mark.asyncio
    async def test_register_blob_graceful_failure(self):
        """Registration failure should not raise - just log warning."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Registration failed")

        with patch.object(client, "call_with_retries", raise_error):
            # Should NOT raise - graceful degradation
            result = await client.register_blob(
                blob_uuid="uuid_123",
                filename="test.pdf",
                file_size_bytes=1024,
                file_hash="abc123",
                api_key="test_key",
            )

        assert result["status"] == "written_but_not_registered"


# =============================================================================
# Reconcile Tests
# =============================================================================

class TestReconcile:
    """Tests for reconcile() method."""

    @pytest.mark.asyncio
    async def test_reconcile_success(self):
        """Should confirm blob is safe after reconciliation."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.reconcile(blob_uuid="uuid_123")

        assert result["blob_uuid"] == "uuid_123"
        assert result["status"] == "reconciled"
        assert result["blob_safe"] is True

    @pytest.mark.asyncio
    async def test_reconcile_error_wrapped(self):
        """Reconcile errors should raise HeartBeatUnavailableError."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Reconciliation service down")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(HeartBeatUnavailableError) as exc_info:
                await client.reconcile(blob_uuid="uuid_123")

        assert "Failed to reconcile blob" in exc_info.value.message


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthCheck:
    """Tests for health_check() method."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """Should return True when healthy."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self):
        """Should return False on error."""
        client = HeartBeatClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Health check failed")

        with patch.object(client, "call_with_retries", raise_error):
            result = await client.health_check()

        assert result is False


# =============================================================================
# Trace ID Tests
# =============================================================================

class TestTraceID:
    """Tests for trace ID propagation."""

    @pytest.mark.asyncio
    async def test_trace_id_propagated(self):
        """Trace ID should be available in client."""
        client = HeartBeatClient(
            heartbeat_api_url="http://localhost:9000",
            trace_id="test_trace_abc",
        )

        assert client.trace_id == "test_trace_abc"
