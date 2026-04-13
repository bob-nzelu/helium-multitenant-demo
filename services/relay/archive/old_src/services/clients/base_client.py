"""
Base HTTP Client with Retry Logic

Provides common HTTP client functionality with exponential backoff retry logic.
All service clients (CoreAPIClient, HeartBeatClient, AuditAPIClient) inherit from this.

Decision: Retry logic (5 attempts, exponential backoff) is built into BaseClient.
Subclasses can override if needed for specific error handling.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Type
import json

from ..errors import (
    RelayError,
    TransientError,
    ConnectionTimeoutError,
    ConnectionResetError,
    ServiceUnavailableError,
    InternalErrorError,
)


logger = logging.getLogger(__name__)


class BaseClient:
    """
    Base HTTP client with retry logic and timeout handling.

    Implements exponential backoff for transient errors (max 5 attempts).
    Distinguishes between transient (retry) and permanent (fail fast) errors.

    Configuration:
    - max_attempts: Maximum number of retry attempts (default: 5)
    - initial_delay: Initial retry delay in seconds (default: 1)
    - timeout: Request timeout in seconds (default: 30)
    """

    def __init__(
        self,
        max_attempts: int = 5,
        initial_delay: float = 1.0,
        timeout: float = 30.0,
        trace_id: Optional[str] = None,
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.timeout = timeout
        self.trace_id = trace_id or self._generate_trace_id()

    @staticmethod
    def _generate_trace_id() -> str:
        """Generate unique trace ID for request tracking"""
        import uuid

        return f"trace_{uuid.uuid4()}"

    async def call_with_retries(
        self,
        async_func,
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute async function with exponential backoff retry logic.

        Args:
            async_func: Async function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function

        Returns:
            Result from function call

        Raises:
            RelayError: On permanent errors or after all retries exhausted
        """

        for attempt in range(self.max_attempts):
            try:
                logger.debug(
                    f"Attempt {attempt + 1}/{self.max_attempts} - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                )

                return await asyncio.wait_for(
                    async_func(*args, **kwargs),
                    timeout=self.timeout,
                )

            except asyncio.TimeoutError as e:
                logger.warning(
                    f"Request timeout on attempt {attempt + 1} - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                )

                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2**attempt)
                    await asyncio.sleep(delay)
                else:
                    raise ServiceUnavailableError(
                        f"Service unavailable after {self.max_attempts} timeout attempts"
                    ) from e

            except TransientError as e:
                logger.warning(
                    f"Transient error {e.error_code} on attempt {attempt + 1}: {e.message} - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                )

                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2**attempt)
                    logger.debug(
                        f"Retrying in {delay} seconds - trace_id={self.trace_id}",
                        extra={"trace_id": self.trace_id},
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Transient error {e.error_code} - "
                        f"all {self.max_attempts} retries exhausted - "
                        f"trace_id={self.trace_id}",
                        extra={"trace_id": self.trace_id},
                    )
                    raise

            except RelayError as e:
                # Permanent errors - fail fast
                logger.error(
                    f"Permanent error {e.error_code}: {e.message} - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                )
                raise

            except Exception as e:
                # Unknown error - wrap and fail
                logger.error(
                    f"Unknown error on attempt {attempt + 1}: {str(e)} - "
                    f"trace_id={self.trace_id}",
                    extra={"trace_id": self.trace_id},
                    exc_info=True,
                )

                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2**attempt)
                    await asyncio.sleep(delay)
                else:
                    raise InternalErrorError(
                        f"Request failed after {self.max_attempts} attempts",
                        original_error=e,
                    ) from e

    def set_trace_id(self, trace_id: str) -> None:
        """Set trace ID for request tracking"""
        self.trace_id = trace_id

    def get_trace_headers(self) -> Dict[str, str]:
        """Get headers with trace ID for propagation"""
        return {
            "X-Trace-ID": self.trace_id,
            "X-Request-ID": f"req_{self.trace_id}",
        }
