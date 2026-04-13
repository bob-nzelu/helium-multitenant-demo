"""Unit tests for circuit breaker."""

import time
from unittest.mock import patch

import pytest

from src.processing.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerClosed:
    def test_initial_state(self):
        cb = CircuitBreaker("test")
        assert cb.state == "CLOSED"
        assert cb.is_available is True

    def test_success_keeps_closed(self):
        cb = CircuitBreaker("test")
        cb.record_success()
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_failures_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == "CLOSED"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker("test", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Now 3 more failures shouldn't open (count was reset)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "CLOSED"


class TestCircuitBreakerOpen:
    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.is_available is False

    def test_blocks_requests(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available is False


class TestCircuitBreakerHalfOpen:
    def test_transitions_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"

        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"
        assert cb.is_available is True

    def test_closes_after_successes(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1, success_threshold=2)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"

        cb.record_success()
        assert cb.state == "HALF_OPEN"  # Need 2 successes
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_reopens_on_failure(self):
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.1)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"

        cb.record_failure()
        assert cb.state == "OPEN"


class TestCircuitBreakerReset:
    def test_reset(self):
        cb = CircuitBreaker("test", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"

        cb.reset()
        assert cb.state == "CLOSED"
        assert cb.is_available is True


class TestCircuitBreakerConfig:
    def test_custom_thresholds(self):
        cb = CircuitBreaker("custom", failure_threshold=10, recovery_timeout=120.0, success_threshold=5)
        assert cb.failure_threshold == 10
        assert cb.recovery_timeout == 120.0
        assert cb.success_threshold == 5

    def test_name(self):
        cb = CircuitBreaker("hsn_endpoint")
        assert cb.name == "hsn_endpoint"
