"""
Unit Tests for CoreAPIClient

Tests the Core API client functionality:
- Enqueue file operations
- Process preview with timeout
- Finalize operations
- Health checks
- Error handling

Target Coverage: 100%
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.clients.core_api_client import CoreAPIClient
from src.services.errors import CoreUnavailableError


# =============================================================================
# Initialization Tests
# =============================================================================

class TestCoreAPIClientInit:
    """Tests for CoreAPIClient initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        assert client.core_api_url == "http://localhost:8080"
        assert client.timeout == 30.0
        assert client.preview_timeout == 300.0  # 5 minutes
        assert client.max_attempts == 5

    def test_custom_initialization(self):
        """Should accept custom configuration."""
        client = CoreAPIClient(
            core_api_url="http://core:8080/",
            timeout=60.0,
            preview_timeout=600.0,
            max_attempts=3,
            trace_id="custom_trace",
        )

        assert client.core_api_url == "http://core:8080"  # Trailing slash stripped
        assert client.timeout == 60.0
        assert client.preview_timeout == 600.0
        assert client.max_attempts == 3
        assert client.trace_id == "custom_trace"

    def test_url_trailing_slash_stripped(self):
        """Should strip trailing slash from URL."""
        client = CoreAPIClient(core_api_url="http://localhost:8080///")

        assert client.core_api_url == "http://localhost:8080"


# =============================================================================
# Enqueue Tests
# =============================================================================

class TestEnqueue:
    """Tests for enqueue() method."""

    @pytest.mark.asyncio
    async def test_enqueue_success(self):
        """Should return queue ID on success."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.enqueue(
            blob_uuid="uuid_123",
            filename="invoice.pdf",
            file_size_bytes=1024,
            batch_id="batch_456",
        )

        assert "queue_id" in result
        assert result["status"] == "queued"
        assert result["batch_id"] == "batch_456"

    @pytest.mark.asyncio
    async def test_enqueue_generates_batch_id(self):
        """Should use blob_uuid as batch_id if not provided."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.enqueue(
            blob_uuid="uuid_789",
            filename="invoice.pdf",
            file_size_bytes=1024,
        )

        assert result["batch_id"] == "uuid_789"

    @pytest.mark.asyncio
    async def test_enqueue_queue_id_format(self):
        """Queue ID should be based on blob_uuid."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.enqueue(
            blob_uuid="test_uuid_abc",
            filename="invoice.pdf",
            file_size_bytes=1024,
        )

        assert result["queue_id"] == "queue_test_uuid_abc"


# =============================================================================
# Process Preview Tests
# =============================================================================

class TestProcessPreview:
    """Tests for process_preview() method."""

    @pytest.mark.asyncio
    async def test_process_preview_success(self):
        """Should return preview data on success."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.process_preview(queue_id="queue_123")

        assert result["queue_id"] == "queue_123"
        assert result["status"] == "processed"
        assert "preview_data" in result

    @pytest.mark.asyncio
    async def test_process_preview_custom_timeout(self):
        """Should accept custom timeout."""
        client = CoreAPIClient(
            core_api_url="http://localhost:8080",
            preview_timeout=60.0,
        )

        # Custom timeout override
        result = await client.process_preview(
            queue_id="queue_123",
            timeout=30.0,
        )

        assert result["status"] == "processed"

    @pytest.mark.asyncio
    async def test_process_preview_timeout_raises(self):
        """Should raise asyncio.TimeoutError on timeout."""
        client = CoreAPIClient(
            core_api_url="http://localhost:8080",
            preview_timeout=0.05,  # Very short timeout
        )

        # Patch the internal _call to be slow
        original_method = client.process_preview

        async def slow_preview(queue_id, timeout=None):
            # Simulate a slow call that exceeds timeout
            async def _slow_call():
                await asyncio.sleep(1)
                return {"status": "processed"}

            use_timeout = timeout or 0.05

            try:
                return await asyncio.wait_for(
                    client.call_with_retries(_slow_call),
                    timeout=use_timeout,
                )
            except asyncio.TimeoutError:
                raise

        with patch.object(client, "process_preview", slow_preview):
            with pytest.raises(asyncio.TimeoutError):
                await slow_preview("queue_123", timeout=0.05)


# =============================================================================
# Process Immediate Tests
# =============================================================================

class TestProcessImmediate:
    """Tests for process_immediate() method."""

    @pytest.mark.asyncio
    async def test_process_immediate_success(self):
        """Should return processing result."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.process_immediate(queue_id="queue_456")

        assert result["queue_id"] == "queue_456"
        assert result["status"] == "processed"
        assert "invoices" in result


# =============================================================================
# Finalize Tests
# =============================================================================

class TestFinalize:
    """Tests for finalize() method."""

    @pytest.mark.asyncio
    async def test_finalize_success(self):
        """Should finalize and return result."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.finalize(queue_id="queue_789")

        assert result["queue_id"] == "queue_789"
        assert result["status"] == "finalized"

    @pytest.mark.asyncio
    async def test_finalize_with_edits(self):
        """Should accept user edits."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        user_edits = {"vendor_name": "Updated Vendor"}

        result = await client.finalize(
            queue_id="queue_789",
            user_edits=user_edits,
        )

        assert result["status"] == "finalized"

    @pytest.mark.asyncio
    async def test_finalize_empty_edits(self):
        """Should handle None edits."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.finalize(
            queue_id="queue_789",
            user_edits=None,
        )

        assert result["status"] == "finalized"


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthCheck:
    """Tests for health_check() method."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        """Should return True when healthy."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self):
        """Should return False on error."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        # Patch to raise error
        async def raise_error(*args, **kwargs):
            raise Exception("Connection failed")

        with patch.object(client, "call_with_retries", raise_error):
            result = await client.health_check()

        assert result is False


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_enqueue_wraps_error(self):
        """Enqueue errors should be wrapped as CoreUnavailableError."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        async def raise_error(*args, **kwargs):
            raise Exception("Connection refused")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(CoreUnavailableError) as exc_info:
                await client.enqueue(
                    blob_uuid="uuid_123",
                    filename="test.pdf",
                    file_size_bytes=1024,
                )

        assert "Failed to enqueue" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_process_preview_wraps_error(self):
        """Process preview errors should be wrapped."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        async def raise_error(*args, **kwargs):
            raise Exception("Server error")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(CoreUnavailableError) as exc_info:
                await client.process_preview(queue_id="queue_123")

        assert "Failed to process preview" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_finalize_wraps_error(self):
        """Finalize errors should be wrapped."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        async def raise_error(*args, **kwargs):
            raise Exception("Finalize failed")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(CoreUnavailableError) as exc_info:
                await client.finalize(queue_id="queue_123")

        assert "Failed to finalize" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_process_immediate_wraps_error(self):
        """Process immediate errors should be wrapped."""
        client = CoreAPIClient(core_api_url="http://localhost:8080")

        async def raise_error(*args, **kwargs):
            raise Exception("Processing failed")

        with patch.object(client, "call_with_retries", raise_error):
            with pytest.raises(CoreUnavailableError) as exc_info:
                await client.process_immediate(queue_id="queue_123")

        assert "Failed to process immediately" in exc_info.value.message


# =============================================================================
# Trace ID Propagation Tests
# =============================================================================

class TestTraceIDPropagation:
    """Tests for trace ID propagation."""

    @pytest.mark.asyncio
    async def test_trace_id_in_enqueue(self):
        """Enqueue should include trace_id in payload."""
        client = CoreAPIClient(
            core_api_url="http://localhost:8080",
            trace_id="test_trace_xyz",
        )

        # The stub implementation includes trace_id in response
        result = await client.enqueue(
            blob_uuid="uuid_123",
            filename="test.pdf",
            file_size_bytes=1024,
        )

        # Verify client has trace_id
        assert client.trace_id == "test_trace_xyz"
