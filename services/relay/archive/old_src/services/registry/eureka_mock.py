"""
Mock Eureka Client (In-Memory Service Registry)

Used for Test/Standard tier where services run on the same machine.
Provides hardcoded service URLs for localhost (no external Eureka needed).

Configuration from RELAY_DECISIONS.md:
- Mock registry returns hardcoded localhost URLs
- Test/Standard tier: localhost:8080 (Core), localhost:9000 (HeartBeat), etc.
- No network calls, all in-memory lookups
"""

import logging
from typing import Dict, Any, Optional


logger = logging.getLogger(__name__)


class EurekaMockClient:
    """
    Mock Eureka client for Test/Standard tier.

    Returns hardcoded localhost URLs for common Helium services.
    Useful for development and single-machine deployments.
    """

    # Hardcoded service registry for Test/Standard tier
    SERVICE_REGISTRY = {
        "core-api": {
            "url": "http://localhost:8080",
            "health_check": "/health",
            "port": 8080,
        },
        "heartbeat": {
            "url": "http://localhost:9000",
            "health_check": "/health",
            "port": 9000,
        },
        "relay-bulk": {
            "url": "http://localhost:8082",
            "health_check": "/health",
            "port": 8082,
        },
        "relay-watcher": {
            "url": "http://localhost:8083",
            "health_check": "/health",
            "port": 8083,
        },
        "edge": {
            "url": "http://localhost:8084",
            "health_check": "/health",
            "port": 8084,
        },
    }

    def __init__(self):
        """Initialize mock Eureka client"""
        logger.info("Initialized EurekaMockClient (in-memory service registry)")

    def get_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        """
        Get service URL from mock registry.

        Args:
            service_name: Service name (e.g., 'core-api', 'heartbeat')

        Returns:
            Service info dict with 'url', 'port', 'health_check'
            Returns None if service not found

        Example:
            >>> client = EurekaMockClient()
            >>> client.get_service('core-api')
            {'url': 'http://localhost:8080', 'port': 8080, 'health_check': '/health'}
        """

        if service_name in self.SERVICE_REGISTRY:
            service_info = self.SERVICE_REGISTRY[service_name]
            logger.debug(f"Service lookup: {service_name} -> {service_info['url']}")
            return service_info
        else:
            logger.warning(f"Service not found in mock registry: {service_name}")
            return None

    def register_service(
        self,
        service_name: str,
        url: str,
        port: int,
        health_check: str = "/health",
    ) -> None:
        """
        Register a service in mock registry (for testing).

        Args:
            service_name: Service name
            url: Base URL
            port: Port number
            health_check: Health check path
        """

        self.SERVICE_REGISTRY[service_name] = {
            "url": url,
            "port": port,
            "health_check": health_check,
        }
        logger.info(f"Registered service in mock registry: {service_name} -> {url}")

    def deregister_service(self, service_name: str) -> None:
        """
        Deregister a service from mock registry (for testing).

        Args:
            service_name: Service name
        """

        if service_name in self.SERVICE_REGISTRY:
            del self.SERVICE_REGISTRY[service_name]
            logger.info(f"Deregistered service from mock registry: {service_name}")

    def get_all_services(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all services in mock registry.

        Returns:
            Dictionary of all services
        """

        return self.SERVICE_REGISTRY.copy()

    def is_healthy(self, service_name: str) -> bool:
        """
        Check if service is registered (no actual health check).

        Args:
            service_name: Service name

        Returns:
            True if service is in registry, False otherwise
        """

        return service_name in self.SERVICE_REGISTRY
