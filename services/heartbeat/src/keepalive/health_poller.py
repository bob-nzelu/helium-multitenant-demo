"""
Health Poller — Polls child service /health endpoints.

Uses httpx.AsyncClient with short timeouts to check service health.
Returns standardized status strings: "healthy", "degraded", "unhealthy".

Used by KeepAliveManager in its monitoring loop to detect service failures
before they're visible through PID monitoring alone (e.g., a deadlocked
process that's still alive but not responding to requests).
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from .process_handle import ProcessHandle

logger = logging.getLogger(__name__)

# Health check timeout (seconds)
HEALTH_CHECK_TIMEOUT = 5.0


class HealthPoller:
    """
    Polls child service /health endpoints.

    Attributes:
        _client: Shared httpx.AsyncClient for all health checks.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(HEALTH_CHECK_TIMEOUT),
                follow_redirects=False,
            )
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def check_health(
        self, service_name: str, health_url: str
    ) -> str:
        """
        Check a single service's health endpoint.

        Args:
            service_name: Service name (for logging).
            health_url: Full URL to the service's /health endpoint.

        Returns:
            "healthy" — 200 response with status=healthy
            "degraded" — 200 response with status=degraded
            "unhealthy" — non-200 response, timeout, or connection error
        """
        try:
            client = await self._get_client()
            response = await client.get(health_url)

            if response.status_code == 200:
                try:
                    data = response.json()
                    status = data.get("status", "healthy")
                    if status in ("healthy", "degraded"):
                        return status
                    return "unhealthy"
                except Exception:
                    # 200 but not valid JSON — treat as healthy
                    return "healthy"
            else:
                logger.debug(
                    f"Health check {service_name}: HTTP {response.status_code}"
                )
                return "unhealthy"

        except httpx.ConnectError:
            logger.debug(f"Health check {service_name}: connection refused")
            return "unhealthy"
        except httpx.TimeoutException:
            logger.debug(f"Health check {service_name}: timeout")
            return "unhealthy"
        except Exception as e:
            logger.debug(f"Health check {service_name}: {e}")
            return "unhealthy"

    async def poll_all(
        self, handles: Dict[str, ProcessHandle]
    ) -> Dict[str, str]:
        """
        Check all services concurrently.

        Only polls services that have a health_endpoint configured and
        are in a checkable state (starting, healthy, degraded, unhealthy).

        Args:
            handles: Map of service_name → ProcessHandle.

        Returns:
            Dict of service_name → health status string.
        """
        checkable_statuses = {"starting", "healthy", "degraded", "unhealthy"}
        tasks = {}

        for name, handle in handles.items():
            if (
                handle.health_endpoint
                and handle.status in checkable_statuses
                and handle.is_alive()
            ):
                tasks[name] = self.check_health(name, handle.health_endpoint)

        if not tasks:
            return {}

        # Run all health checks concurrently
        names = list(tasks.keys())
        results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )

        statuses = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.debug(f"Health poll {name} error: {result}")
                statuses[name] = "unhealthy"
            else:
                statuses[name] = result

        return statuses
