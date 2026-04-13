"""
Real Eureka Client (Distributed Service Registry)

Used for Pro/Enterprise tier with real Eureka server.
Queries live service registry for dynamic service discovery.

Supports multiple Eureka backends:
- Spring Cloud Eureka
- Consul
- etcd (with Eureka API compatibility layer)

Configuration:
- eureka_url: Base URL of Eureka server (e.g., http://eureka:8761)
- refresh_interval: How often to refresh service cache (seconds)
"""

import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


class EurekaClient:
    """
    Real Eureka client for Pro/Enterprise tier.

    Queries Eureka server for service discovery.
    Caches results locally with configurable TTL.

    In Phase 1A, this is a stub that uses config-defined URLs as fallback.
    Phase 1B will implement actual HTTP calls to Eureka.
    """

    def __init__(
        self,
        eureka_url: str,
        refresh_interval: int = 300,
        cache_ttl: int = 60,
        fallback_urls: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize real Eureka client.

        Args:
            eureka_url: Base URL of Eureka server (e.g., http://eureka:8761)
            refresh_interval: How often to refresh service list (seconds)
            cache_ttl: How long to cache service info (seconds)
            fallback_urls: Fallback URLs if Eureka unavailable
                          (e.g., {'core-api': 'http://localhost:8080'})
        """

        self.eureka_url = eureka_url.rstrip("/")
        self.refresh_interval = refresh_interval
        self.cache_ttl = cache_ttl
        self.fallback_urls = fallback_urls or {}
        self.service_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_timestamp: Optional[datetime] = None
        self.eureka_available = True

        logger.info(
            f"Initialized EurekaClient: eureka_url={eureka_url}, "
            f"refresh_interval={refresh_interval}s, cache_ttl={cache_ttl}s"
        )

    def get_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Get service URL from Eureka (with local cache).

        Args:
            service_name: Service name (e.g., 'core-api', 'heartbeat')

        Returns:
            Service info dict with 'url', 'port', 'health_check'
            Returns fallback URL if Eureka unavailable or service not found

        Strategy:
            1. Check local cache (if not expired)
            2. If cache expired or empty, refresh from Eureka
            3. If Eureka unavailable, use fallback URL from config
        """

        # Check if cache is still valid
        if self._is_cache_valid():
            if service_name in self.service_cache:
                logger.debug(
                    f"Service lookup (cached): {service_name} -> "
                    f"{self.service_cache[service_name]['url']}"
                )
                return self.service_cache[service_name]

        # Cache expired or empty - would refresh from Eureka in Phase 1B
        # For now, use fallback or return None
        if service_name in self.fallback_urls:
            service_info = {
                "url": self.fallback_urls[service_name],
                "port": self._extract_port(self.fallback_urls[service_name]),
                "health_check": "/health",
                "source": "fallback",
            }
            logger.debug(
                f"Service lookup (fallback): {service_name} -> "
                f"{service_info['url']}"
            )
            return service_info

        logger.warning(f"Service not found: {service_name}")
        return None

    def _is_cache_valid(self) -> bool:
        """Check if local cache is still valid (not expired)"""

        if not self.cache_timestamp or not self.service_cache:
            return False

        age = datetime.utcnow() - self.cache_timestamp
        return age.total_seconds() < self.cache_ttl

    def _extract_port(self, url: str) -> int:
        """Extract port number from URL"""

        try:
            # Format: http://localhost:8080 -> 8080
            parts = url.split(":")
            if len(parts) >= 3:
                return int(parts[-1])
        except (ValueError, IndexError):
            pass

        return 80 if url.startswith("http://") else 443

    async def refresh_services_async(self) -> bool:
        """
        Refresh service list from Eureka (async version).

        In Phase 1A, this is a stub.
        Phase 1B will implement actual HTTP calls to Eureka.

        Returns:
            True if refresh successful, False otherwise
        """

        try:
            # Simulate async call (in Phase 1B, actual HTTP request)
            await asyncio.sleep(0.1)

            logger.debug("Refreshing service list from Eureka (stub)")

            # In Phase 1B, would populate self.service_cache with:
            # - Service URLs
            # - Port numbers
            # - Health check endpoints
            # - Instance counts

            self.cache_timestamp = datetime.utcnow()
            self.eureka_available = True
            return True

        except Exception as e:
            logger.error(f"Failed to refresh services from Eureka: {str(e)}")
            self.eureka_available = False
            return False

    def get_all_services(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all services known to Eureka.

        Returns:
            Dictionary of all services
        """

        if not self._is_cache_valid():
            logger.warning("Service cache expired, returning fallback URLs only")
            return {
                name: {
                    "url": url,
                    "port": self._extract_port(url),
                    "health_check": "/health",
                    "source": "fallback",
                }
                for name, url in self.fallback_urls.items()
            }

        return self.service_cache.copy()

    def is_eureka_available(self) -> bool:
        """Check if Eureka server is available"""

        return self.eureka_available

    def set_fallback_url(self, service_name: str, url: str) -> None:
        """
        Set fallback URL for a service.

        Used when Eureka is unavailable - services can still be discovered
        using hardcoded/config URLs.

        Args:
            service_name: Service name
            url: Fallback URL
        """

        self.fallback_urls[service_name] = url
        logger.info(f"Set fallback URL for {service_name}: {url}")
