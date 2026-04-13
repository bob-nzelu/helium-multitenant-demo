"""
Circuit Breaker — per-endpoint resilience pattern.

Per DEC-WS2-002:
  - failure_threshold: 5
  - recovery_timeout: 60s
  - success_threshold: 2 (consecutive successes in HALF_OPEN to close)

States: CLOSED → OPEN → HALF_OPEN → CLOSED
"""

from __future__ import annotations

import enum
import time

import structlog

logger = structlog.get_logger()


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Per-endpoint circuit breaker.

    CLOSED: Normal operation. Failures increment counter.
    OPEN: All requests blocked. After recovery_timeout, transitions to HALF_OPEN.
    HALF_OPEN: Limited requests allowed. success_threshold consecutive successes → CLOSED.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        audit_logger=None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._audit_logger = audit_logger

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._last_state_change: tuple[str, int] | None = None  # (event, count)

    @property
    def state(self) -> str:
        """Current state, accounting for recovery timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(
                    "circuit_breaker_half_open",
                    name=self.name,
                    elapsed_seconds=round(elapsed, 1),
                )
        return self._state.value

    @property
    def is_available(self) -> bool:
        """True if requests can pass through (CLOSED or HALF_OPEN)."""
        current = self.state  # triggers timeout check
        return current != CircuitState.OPEN.value

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._last_state_change = ("closed", 0)
                logger.info("circuit_breaker_closed", name=self.name)
        elif self._state == CircuitState.CLOSED:
            # Reset failure count on success
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call. May trigger OPEN state."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN immediately re-opens
            self._state = CircuitState.OPEN
            self._success_count = 0
            logger.warning(
                "circuit_breaker_reopened",
                name=self.name,
            )
        elif (
            self._state == CircuitState.CLOSED
            and self._failure_count >= self.failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._last_state_change = ("opened", self._failure_count)
            logger.warning(
                "circuit_breaker_opened",
                name=self.name,
                failure_count=self._failure_count,
            )

    def reset(self) -> None:
        """Force reset to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        logger.info("circuit_breaker_reset", name=self.name)
