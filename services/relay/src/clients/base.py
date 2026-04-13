"""
Base HTTP Client with Retry Logic

Cherry-picked from old_src/services/clients/base_client.py.
Provides exponential backoff retry for all upstream service clients.

Retry policy:
    - TransientError → retry with exponential backoff
    - asyncio.TimeoutError → retry with backoff
    - RelayError (permanent) → fail immediately
    - Unknown exceptions → retry, then wrap in InternalError
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from uuid6 import uuid7

from ..errors import (
    InternalError,
    RelayError,
    ServiceUnavailableError,
    TransientError,
)

logger = logging.getLogger(__name__)


class BaseClient:
    """
    Base HTTP client with retry logic and trace ID propagation.

    All service clients (CoreClient, HeartBeatClient, AuditClient)
    inherit from this.

    Configuration:
        max_attempts:   Maximum retry attempts (default: 5)
        initial_delay:  Initial backoff delay in seconds (default: 1.0)
        timeout:        Per-request timeout in seconds (default: 30.0)
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
        self.trace_id = trace_id or f"trace_{uuid7()}"

    async def call_with_retries(
        self,
        async_func: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute async function with exponential backoff retry.

        Args:
            async_func: Async callable to execute.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result from async_func.

        Raises:
            RelayError: On permanent error or all retries exhausted.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_attempts):
            try:
                logger.debug(
                    f"Attempt {attempt + 1}/{self.max_attempts}",
                    extra={"trace_id": self.trace_id},
                )

                return await asyncio.wait_for(
                    async_func(*args, **kwargs),
                    timeout=self.timeout,
                )

            except asyncio.TimeoutError as e:
                last_exception = e
                logger.warning(
                    f"Timeout on attempt {attempt + 1}",
                    extra={"trace_id": self.trace_id},
                )
                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise ServiceUnavailableError(
                        message=f"Service timed out after {self.max_attempts} attempts"
                    ) from e

            except TransientError as e:
                last_exception = e
                logger.warning(
                    f"Transient error {e.error_code} on attempt {attempt + 1}: {e.message}",
                    extra={"trace_id": self.trace_id},
                )
                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2 ** attempt)
                    logger.debug(
                        f"Retrying in {delay}s",
                        extra={"trace_id": self.trace_id},
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"All {self.max_attempts} retries exhausted for {e.error_code}",
                        extra={"trace_id": self.trace_id},
                    )
                    raise

            except RelayError:
                # Permanent error — fail fast, no retry
                raise

            except Exception as e:
                last_exception = e
                logger.error(
                    f"Unknown error on attempt {attempt + 1}: {e}",
                    extra={"trace_id": self.trace_id},
                    exc_info=True,
                )
                if attempt < self.max_attempts - 1:
                    delay = self.initial_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                else:
                    raise InternalError(
                        message=f"Failed after {self.max_attempts} attempts",
                        original_error=e,
                    ) from e

    def set_trace_id(self, trace_id: str) -> None:
        """Update trace ID for this client."""
        self.trace_id = trace_id

    def get_trace_headers(self) -> Dict[str, str]:
        """Get headers with trace ID for propagation to upstream services."""
        return {
            "X-Trace-ID": self.trace_id,
            "X-Request-ID": f"req_{self.trace_id}",
        }
