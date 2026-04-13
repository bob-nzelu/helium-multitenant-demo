"""
Tests for clients/base.py — BaseClient with retry logic
"""

import asyncio
import pytest

from src.clients.base import BaseClient
from src.errors import (
    ConnectionTimeoutError,
    InternalError,
    InvalidAPIKeyError,
    RelayError,
    ServiceUnavailableError,
    TransientError,
)


class TestBaseClientInit:
    """Test BaseClient initialization."""

    def test_defaults(self):
        client = BaseClient()
        assert client.max_attempts == 5
        assert client.initial_delay == 1.0
        assert client.timeout == 30.0
        assert client.trace_id.startswith("trace_")

    def test_custom_values(self):
        client = BaseClient(
            max_attempts=3,
            initial_delay=0.5,
            timeout=10.0,
            trace_id="custom-trace",
        )
        assert client.max_attempts == 3
        assert client.initial_delay == 0.5
        assert client.timeout == 10.0
        assert client.trace_id == "custom-trace"

    def test_trace_id_generation(self):
        c1 = BaseClient()
        c2 = BaseClient()
        assert c1.trace_id != c2.trace_id

    def test_set_trace_id(self):
        client = BaseClient()
        client.set_trace_id("new-trace")
        assert client.trace_id == "new-trace"

    def test_get_trace_headers(self):
        client = BaseClient(trace_id="test-trace-123")
        headers = client.get_trace_headers()
        assert headers["X-Trace-ID"] == "test-trace-123"
        assert headers["X-Request-ID"] == "req_test-trace-123"


class TestCallWithRetries:
    """Test retry logic."""

    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        client = BaseClient(max_attempts=3, trace_id="test")

        async def success():
            return "ok"

        result = await client.call_with_retries(success)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_success_after_transient_errors(self):
        client = BaseClient(max_attempts=5, initial_delay=0.01, trace_id="test")
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TransientError(message="flaky")
            return "recovered"

        result = await client.call_with_retries(flaky)
        assert result == "recovered"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_permanent_error_fails_fast(self):
        client = BaseClient(max_attempts=5, initial_delay=0.01, trace_id="test")
        attempts = 0

        async def permanent_fail():
            nonlocal attempts
            attempts += 1
            raise InvalidAPIKeyError()

        with pytest.raises(InvalidAPIKeyError):
            await client.call_with_retries(permanent_fail)
        # Should fail on first attempt (no retries for permanent errors)
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_transient(self):
        client = BaseClient(max_attempts=3, initial_delay=0.01, trace_id="test")

        async def always_transient():
            raise ConnectionTimeoutError()

        with pytest.raises(ConnectionTimeoutError):
            await client.call_with_retries(always_transient)

    @pytest.mark.asyncio
    async def test_timeout_retries_then_raises(self):
        client = BaseClient(max_attempts=2, initial_delay=0.01, timeout=0.01, trace_id="test")

        async def slow():
            await asyncio.sleep(10)
            return "too late"

        with pytest.raises(ServiceUnavailableError, match="timed out"):
            await client.call_with_retries(slow)

    @pytest.mark.asyncio
    async def test_unknown_exception_retries_then_wraps(self):
        client = BaseClient(max_attempts=2, initial_delay=0.01, trace_id="test")

        async def weird_error():
            raise RuntimeError("unexpected")

        with pytest.raises(InternalError) as exc_info:
            await client.call_with_retries(weird_error)
        assert exc_info.value.original_error is not None

    @pytest.mark.asyncio
    async def test_relay_error_not_retried(self):
        """Non-transient RelayError should fail immediately."""
        client = BaseClient(max_attempts=5, initial_delay=0.01, trace_id="test")
        attempts = 0

        async def relay_err():
            nonlocal attempts
            attempts += 1
            raise RelayError("PERMANENT", "not retryable", status_code=400)

        with pytest.raises(RelayError):
            await client.call_with_retries(relay_err)
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_delay(self):
        """Verify delays increase exponentially."""
        client = BaseClient(max_attempts=4, initial_delay=0.05, trace_id="test")
        timestamps = []

        async def record_and_fail():
            import time
            timestamps.append(time.monotonic())
            raise TransientError(message="fail")

        with pytest.raises(TransientError):
            await client.call_with_retries(record_and_fail)

        # Should have 4 attempts
        assert len(timestamps) == 4

        # Total elapsed time should be at least sum of delays:
        # 0.05 + 0.10 + 0.20 = 0.35s (with jitter, could be less)
        total_elapsed = timestamps[-1] - timestamps[0]
        assert total_elapsed >= 0.10, f"Total elapsed too short: {total_elapsed}"

        # Each subsequent delay should be > previous delay (exponential growth)
        delays = [timestamps[i] - timestamps[i - 1] for i in range(1, len(timestamps))]
        for i in range(1, len(delays)):
            assert delays[i] > delays[i - 1] * 0.5, (
                f"Delay {i+1} ({delays[i]:.3f}s) not growing vs "
                f"delay {i} ({delays[i-1]:.3f}s)"
            )

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        client = BaseClient(max_attempts=1, trace_id="test")

        async def echo(a, b, c=None):
            return (a, b, c)

        result = await client.call_with_retries(echo, 1, 2, c=3)
        assert result == (1, 2, 3)
