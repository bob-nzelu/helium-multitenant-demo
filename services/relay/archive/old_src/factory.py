"""
Relay Service Factory

Factory pattern for creating relay service instances.
Each relay type (Bulk, Queue, Watcher, etc.) is instantiated through the factory.

Usage:
    factory = RelayServiceFactory(core_client, heartbeat_client, audit_client)
    relay_bulk = factory.create('bulk')
    relay_watcher = factory.create('watcher')
"""

import logging
from typing import Optional, Dict, Any, Type

from .base import BaseRelayService
from .services.clients import (
    CoreAPIClient,
    HeartBeatClient,
    AuditAPIClient,
)


logger = logging.getLogger(__name__)


class RelayServiceFactory:
    """
    Factory for creating relay service instances.

    Supports all 7 relay types:
    - bulk: HTTP multipart upload (Phase 1B)
    - queue: Internal relay queue (Phase 2+)
    - watcher: File system monitoring (Phase 2+)
    - dbc: Database connectivity (Phase 2+)
    - api: Webhook/HTTP endpoints (Phase 2+)
    - polling: Time-based polling (Phase 2+)
    - email: Email attachment processing (Phase 2+)

    Pattern:
    - All services inherit from BaseRelayService
    - Factory creates instances with required dependencies injected
    - Phase 1A provides stubs for deferred types (raise NotImplementedError)
    - Phase 1B implements actual Bulk service
    """

    # Placeholder for service classes (will be populated as phases complete)
    _service_classes: Dict[str, Type[BaseRelayService]] = {}

    def __init__(
        self,
        core_client: CoreAPIClient,
        heartbeat_client: HeartBeatClient,
        audit_client: AuditAPIClient,
        trace_id: Optional[str] = None,
    ):
        """
        Initialize factory with required clients.

        Args:
            core_client: Client for Core API
            heartbeat_client: Client for HeartBeat
            audit_client: Client for audit logging
            trace_id: Optional trace ID for request tracking
        """

        self.core_client = core_client
        self.heartbeat_client = heartbeat_client
        self.audit_client = audit_client
        self.trace_id = trace_id
        self.created_services: Dict[str, BaseRelayService] = {}

        logger.debug("Initialized RelayServiceFactory")

    def create(
        self,
        service_type: str,
        trace_id: Optional[str] = None,
    ) -> BaseRelayService:
        """
        Create a relay service instance.

        Args:
            service_type: Type of service ('bulk', 'queue', 'watcher', etc.)
            trace_id: Optional trace ID (overrides factory default)

        Returns:
            Relay service instance

        Raises:
            ValueError: If service type is unknown
        """

        if service_type not in self._service_classes:
            raise ValueError(f"Unknown relay service type: {service_type}")

        service_class = self._service_classes[service_type]
        use_trace_id = trace_id or self.trace_id

        service = service_class(
            service_name=f"relay-{service_type}",
            core_client=self.core_client,
            heartbeat_client=self.heartbeat_client,
            audit_client=self.audit_client,
            trace_id=use_trace_id,
        )

        logger.info(
            f"Created relay service: {service_type} "
            f"(trace_id={use_trace_id})"
        )

        return service

    def register_service(
        self,
        service_type: str,
        service_class: Type[BaseRelayService],
    ) -> None:
        """
        Register a service class with the factory.

        Called by each service module during initialization.

        Args:
            service_type: Service type identifier ('bulk', 'queue', etc.)
            service_class: Service class (must inherit from BaseRelayService)

        Raises:
            ValueError: If service_class doesn't inherit from BaseRelayService
        """

        if not issubclass(service_class, BaseRelayService):
            raise ValueError(
                f"Service class {service_class.__name__} must inherit "
                f"from BaseRelayService"
            )

        self._service_classes[service_type] = service_class
        logger.info(
            f"Registered relay service type: {service_type} -> "
            f"{service_class.__name__}"
        )

    def get_supported_types(self) -> list:
        """
        Get list of supported service types.

        Returns:
            List of service type identifiers
        """

        return sorted(self._service_classes.keys())

    def is_supported(self, service_type: str) -> bool:
        """
        Check if service type is supported.

        Args:
            service_type: Service type identifier

        Returns:
            True if supported, False otherwise
        """

        return service_type in self._service_classes


# Global factory instance (singleton pattern)
_factory_instance: Optional[RelayServiceFactory] = None


def get_relay_factory(
    core_client: CoreAPIClient,
    heartbeat_client: HeartBeatClient,
    audit_client: AuditAPIClient,
    trace_id: Optional[str] = None,
) -> RelayServiceFactory:
    """
    Get or create the global relay service factory.

    Args:
        core_client: Client for Core API
        heartbeat_client: Client for HeartBeat
        audit_client: Client for audit logging
        trace_id: Optional trace ID

    Returns:
        RelayServiceFactory singleton instance
    """

    global _factory_instance

    if _factory_instance is None:
        _factory_instance = RelayServiceFactory(
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
            trace_id=trace_id,
        )

    return _factory_instance
