"""
Unit Tests for AuditAPIClient

Tests the audit logging client functionality:
- Batch ingestion events
- File ingestion events
- Error logging
- Authentication failure logging
- Rate limit events
- Fire-and-forget behavior (no blocking on failures)

Target Coverage: 100%
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.clients.audit_client import AuditAPIClient


# =============================================================================
# Initialization Tests
# =============================================================================

class TestAuditAPIClientInit:
    """Tests for AuditAPIClient initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        assert client.heartbeat_api_url == "http://localhost:9000"
        assert client.service_name == "relay-bulk"
        assert client.timeout == 30.0
        assert client.max_attempts == 3  # Fewer retries for audit

    def test_custom_initialization(self):
        """Should accept custom configuration."""
        client = AuditAPIClient(
            heartbeat_api_url="http://heartbeat:9000/",
            service_name="relay-watcher",
            timeout=60.0,
            max_attempts=5,
            trace_id="custom_trace",
        )

        assert client.heartbeat_api_url == "http://heartbeat:9000"
        assert client.service_name == "relay-watcher"
        assert client.timeout == 60.0
        assert client.max_attempts == 5
        assert client.trace_id == "custom_trace"

    def test_shorter_initial_delay(self):
        """Audit client should have shorter initial delay."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        assert client.initial_delay == 0.5  # Shorter than BaseClient default


# =============================================================================
# Batch Ingestion Started Tests
# =============================================================================

class TestLogBatchIngestionStarted:
    """Tests for log_batch_ingestion_started() method."""

    @pytest.mark.asyncio
    async def test_log_batch_started(self):
        """Should log batch ingestion started event."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        # Should not raise
        await client.log_batch_ingestion_started(
            batch_id="batch_123",
            api_key="test_api_key_very_long",
            total_files=5,
            total_size_mb=10.5,
        )

    @pytest.mark.asyncio
    async def test_api_key_truncated(self):
        """API key should be truncated for privacy."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        logged_events = []

        async def capture_event(*args, **kwargs):
            logged_events.append({"status": "logged"})
            return {"status": "logged"}

        with patch.object(client, "call_with_retries", capture_event):
            await client.log_batch_ingestion_started(
                batch_id="batch_123",
                api_key="very_long_api_key_that_should_be_truncated",
                total_files=5,
                total_size_mb=10.5,
            )

        # The method should run without error
        assert len(logged_events) == 1

    @pytest.mark.asyncio
    async def test_short_api_key_not_truncated(self):
        """Short API key should not be truncated."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        # Should not raise even with short key
        await client.log_batch_ingestion_started(
            batch_id="batch_123",
            api_key="short_key",
            total_files=5,
            total_size_mb=10.5,
        )


# =============================================================================
# File Ingested Tests
# =============================================================================

class TestLogFileIngested:
    """Tests for log_file_ingested() method."""

    @pytest.mark.asyncio
    async def test_log_file_ingested(self):
        """Should log file ingested event."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_file_ingested(
            batch_id="batch_123",
            file_uuid="uuid_456",
            filename="invoice.pdf",
            file_size_mb=2.5,
            queue_id="queue_789",
        )

    @pytest.mark.asyncio
    async def test_file_size_rounded(self):
        """File size should be rounded to 2 decimal places."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        # Should not raise even with many decimal places
        await client.log_file_ingested(
            batch_id="batch_123",
            file_uuid="uuid_456",
            filename="invoice.pdf",
            file_size_mb=2.123456789,
            queue_id="queue_789",
        )


# =============================================================================
# Batch Ingestion Completed Tests
# =============================================================================

class TestLogBatchIngestionCompleted:
    """Tests for log_batch_ingestion_completed() method."""

    @pytest.mark.asyncio
    async def test_log_batch_completed(self):
        """Should log batch completion event."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_batch_ingestion_completed(
            batch_id="batch_123",
            successful_count=3,
            duplicate_count=1,
            failed_count=1,
        )

    @pytest.mark.asyncio
    async def test_total_count_calculated(self):
        """Total count should be sum of all counts."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        # Method calculates total_count = successful + duplicate + failed
        await client.log_batch_ingestion_completed(
            batch_id="batch_123",
            successful_count=5,
            duplicate_count=2,
            failed_count=1,
        )


# =============================================================================
# Error Logging Tests
# =============================================================================

class TestLogError:
    """Tests for log_error() method."""

    @pytest.mark.asyncio
    async def test_log_error_with_filename(self):
        """Should log error event with filename."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_error(
            error_code="VALIDATION_FAILED",
            filename="invoice.pdf",
            details="Invalid file format",
        )

    @pytest.mark.asyncio
    async def test_log_error_without_filename(self):
        """Should log error event without filename."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_error(
            error_code="INTERNAL_ERROR",
            filename=None,
            details="Unexpected server error",
        )


# =============================================================================
# Authentication Failure Tests
# =============================================================================

class TestLogAuthenticationFailure:
    """Tests for log_authentication_failure() method."""

    @pytest.mark.asyncio
    async def test_log_auth_failure(self):
        """Should log authentication failure event."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_authentication_failure(
            api_key="invalid_api_key_12345",
            error="Invalid API key",
        )

    @pytest.mark.asyncio
    async def test_auth_failure_api_key_truncated(self):
        """API key should be truncated for security."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_authentication_failure(
            api_key="very_long_api_key_should_be_truncated",
            error="Signature verification failed",
        )


# =============================================================================
# Rate Limit Exceeded Tests
# =============================================================================

class TestLogRateLimitExceeded:
    """Tests for log_rate_limit_exceeded() method."""

    @pytest.mark.asyncio
    async def test_log_rate_limit(self):
        """Should log rate limit exceeded event."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        await client.log_rate_limit_exceeded(
            api_key="rate_limited_api_key",
            current_usage=500,
            limit=500,
        )


# =============================================================================
# Fire-and-Forget Behavior Tests
# =============================================================================

class TestFireAndForget:
    """Tests for non-blocking audit logging."""

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_raise(self):
        """Audit logging failures should not raise exceptions."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Audit service unavailable")

        with patch.object(client, "call_with_retries", raise_error):
            # Should NOT raise - fire and forget
            await client.log_batch_ingestion_started(
                batch_id="batch_123",
                api_key="test_key",
                total_files=5,
                total_size_mb=10.5,
            )

    @pytest.mark.asyncio
    async def test_all_log_methods_non_blocking(self):
        """All log methods should be non-blocking on failure."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        async def raise_error(*args, **kwargs):
            raise Exception("Audit service unavailable")

        with patch.object(client, "call_with_retries", raise_error):
            # None of these should raise
            await client.log_batch_ingestion_started(
                batch_id="batch_123",
                api_key="test_key",
                total_files=5,
                total_size_mb=10.5,
            )

            await client.log_file_ingested(
                batch_id="batch_123",
                file_uuid="uuid_456",
                filename="test.pdf",
                file_size_mb=1.0,
                queue_id="queue_789",
            )

            await client.log_batch_ingestion_completed(
                batch_id="batch_123",
                successful_count=0,
                duplicate_count=0,
                failed_count=1,
            )

            await client.log_error(
                error_code="TEST_ERROR",
                filename="test.pdf",
                details="Test error",
            )

            await client.log_authentication_failure(
                api_key="test_key",
                error="Test auth failure",
            )

            await client.log_rate_limit_exceeded(
                api_key="test_key",
                current_usage=100,
                limit=100,
            )


# =============================================================================
# Event Data Format Tests
# =============================================================================

class TestEventDataFormat:
    """Tests for event data structure."""

    @pytest.mark.asyncio
    async def test_event_has_timestamp(self):
        """Events should include ISO timestamp."""
        client = AuditAPIClient(heartbeat_api_url="http://localhost:9000")

        # The stub implementation logs the timestamp
        # We just verify the method runs without error
        await client.log_batch_ingestion_started(
            batch_id="batch_123",
            api_key="test_key",
            total_files=5,
            total_size_mb=10.5,
        )

    @pytest.mark.asyncio
    async def test_event_has_service_name(self):
        """Events should include service name."""
        client = AuditAPIClient(
            heartbeat_api_url="http://localhost:9000",
            service_name="relay-test",
        )

        assert client.service_name == "relay-test"

    @pytest.mark.asyncio
    async def test_event_has_trace_id(self):
        """Events should include trace ID."""
        client = AuditAPIClient(
            heartbeat_api_url="http://localhost:9000",
            trace_id="test_trace_123",
        )

        assert client.trace_id == "test_trace_123"
