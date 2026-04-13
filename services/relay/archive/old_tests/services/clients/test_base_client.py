"""
Unit Tests for BaseClient

Tests the core HTTP client functionality:
- Retry logic with exponential backoff
- Timeout handling
- Error classification (transient vs permanent)
- Trace ID generation and propagation

Target Coverage: 100%
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.clients.base_client import BaseClient
from src.services.errors import (
    RelayError,
    TransientError,
    ConnectionTimeoutError,
    ServiceUnavailableError,
    InternalErrorError,
)


# =============================================================================
# Initialization Tests
# =============================================================================

class TestBaseClientInit:
    """Tests for BaseClient initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        client = BaseClient()

        assert client.max_attempts == 5
        assert client.initial_delay == 1.0
        assert client.timeout == 30.0
        assert client.trace_id is not None

    def test_custom_initialization(self):
        """Should accept custom configuration."""
        client = BaseClient(
            max_attempts=3,
            initial_delay=0.5,
            timeout=60.0,
            trace_id="custom_trace_123",
        )

        assert client.max_attempts == 3
        assert client.initial_delay == 0.5
        assert client.timeout == 60.0
        assert client.trace_id == "custom_trace_123"

    def test_trace_id_generation(self):
        """Should generate unique trace IDs."""
        client1 = BaseClient()
        client2 = BaseClient()

        assert client1.trace_id != client2.trace_id
        assert client1.trace_id.startswith("trace_")
        assert client2.trace_id.startswith("trace_")


# =============================================================================
# Retry Logic Tests
# =============================================================================

class TestRetryLogic:
    """Tests for retry logic with exponential backoff."""

    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self):
        """Successful call should not trigger retries."""
        client = BaseClient(max_attempts=5)
        call_count = 0

        async def successful_call():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await client.call_with_retries(successful_call)

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_transient_error_retry(self):
        """Transient errors should trigger retries."""
        client = BaseClient(max_attempts=3, initial_delay=0.01)
        call_count = 0

        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("TRANSIENT", "Transient error")
            return "success"

        result = await client.call_with_retries(failing_then_success)

        assert result == "success"
        assert call_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_transient_error_exhausted(self):
        """Should raise after all retries exhausted."""
        client = BaseClient(max_attempts=3, initial_delay=0.01)
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise TransientError("TRANSIENT", "Always fails")

        with pytest.raises(TransientError):
            await client.call_with_retries(always_fails)

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_permanent_error_no_retry(self):
        """Permanent errors should fail immediately."""
        client = BaseClient(max_attempts=5, initial_delay=0.01)
        call_count = 0

        async def permanent_failure():
            nonlocal call_count
            call_count += 1
            raise RelayError("PERMANENT", "Permanent error", status_code=400)

        with pytest.raises(RelayError) as exc_info:
            await client.call_with_retries(permanent_failure)

        assert exc_info.value.error_code == "PERMANENT"
        assert call_count == 1  # No retries for permanent errors

    @pytest.mark.asyncio
    async def test_unknown_error_wrapped(self):
        """Unknown errors should be wrapped after retries."""
        client = BaseClient(max_attempts=2, initial_delay=0.01)
        call_count = 0

        async def raises_unknown():
            nonlocal call_count
            call_count += 1
            raise ValueError("Unknown error")

        with pytest.raises(InternalErrorError) as exc_info:
            await client.call_with_retries(raises_unknown)

        assert "failed after 2 attempts" in exc_info.value.message
        assert call_count == 2


# =============================================================================
# Timeout Tests
# =============================================================================

class TestTimeoutHandling:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_retry(self):
        """Timeout should trigger retry."""
        client = BaseClient(max_attempts=3, timeout=0.1, initial_delay=0.01)
        call_count = 0

        async def slow_then_fast():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                await asyncio.sleep(1)  # Will timeout
            return "success"

        result = await client.call_with_retries(slow_then_fast)

        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_exhausted(self):
        """Should raise ServiceUnavailableError after all timeouts."""
        client = BaseClient(max_attempts=2, timeout=0.05, initial_delay=0.01)
        call_count = 0

        async def always_slow():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(1)  # Always timeout
            return "never reached"

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await client.call_with_retries(always_slow)

        assert "timeout" in exc_info.value.message.lower()
        assert call_count == 2


# =============================================================================
# Exponential Backoff Tests
# =============================================================================

class TestExponentialBackoff:
    """Tests for exponential backoff timing."""

    @pytest.mark.asyncio
    async def test_backoff_increases_exponentially(self):
        """Delays should increase exponentially."""
        delays = []

        client = BaseClient(max_attempts=4, initial_delay=0.1)

        # Monkey-patch sleep to capture delays
        original_sleep = asyncio.sleep

        async def capture_sleep(delay):
            delays.append(delay)
            await original_sleep(0.001)  # Minimal actual delay for test speed

        call_count = 0

        async def fails_three_times():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise TransientError("TRANSIENT", "Retry me")
            return "success"

        with patch("asyncio.sleep", capture_sleep):
            await client.call_with_retries(fails_three_times)

        # Should have delays: 0.1, 0.2, 0.4 (for attempts 1, 2, 3)
        assert len(delays) == 3
        assert delays[0] == pytest.approx(0.1, rel=0.1)
        assert delays[1] == pytest.approx(0.2, rel=0.1)
        assert delays[2] == pytest.approx(0.4, rel=0.1)


# =============================================================================
# Trace ID Tests
# =============================================================================

class TestTraceID:
    """Tests for trace ID management."""

    def test_generate_trace_id_format(self):
        """Generated trace IDs should have correct format."""
        trace_id = BaseClient._generate_trace_id()

        assert trace_id.startswith("trace_")
        # UUID format: trace_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        assert len(trace_id) > 10

    def test_set_trace_id(self):
        """Should allow setting custom trace ID."""
        client = BaseClient()
        original = client.trace_id

        client.set_trace_id("custom_trace_456")

        assert client.trace_id == "custom_trace_456"
        assert client.trace_id != original

    def test_get_trace_headers(self):
        """Should return headers with trace ID."""
        client = BaseClient(trace_id="test_trace_789")

        headers = client.get_trace_headers()

        assert headers["X-Trace-ID"] == "test_trace_789"
        assert headers["X-Request-ID"] == "req_test_trace_789"


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_single_attempt_config(self):
        """Should work with single attempt (no retries)."""
        client = BaseClient(max_attempts=1)
        call_count = 0

        async def fails_once():
            nonlocal call_count
            call_count += 1
            raise TransientError("TRANSIENT", "Fail")

        with pytest.raises(TransientError):
            await client.call_with_retries(fails_once)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_function_with_args(self):
        """Should pass args and kwargs to function."""
        client = BaseClient()

        async def echo_args(a, b, c=None):
            return {"a": a, "b": b, "c": c}

        result = await client.call_with_retries(echo_args, 1, 2, c=3)

        assert result == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_zero_delay_allowed(self):
        """Should allow zero initial delay."""
        client = BaseClient(max_attempts=3, initial_delay=0)
        call_count = 0

        async def fails_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("TRANSIENT", "Retry")
            return "success"

        result = await client.call_with_retries(fails_twice)

        assert result == "success"
        assert call_count == 3
