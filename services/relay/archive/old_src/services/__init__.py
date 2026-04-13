"""
Services Module

Shared services infrastructure:
- clients: Inter-service communication clients
- registry: Service discovery (Eureka)
- errors: Error definitions
- logging: Structured logging
- monitoring: Prometheus metrics
"""

from .clients import (
    BaseClient,
    CoreAPIClient,
    HeartBeatClient,
    AuditAPIClient,
)
from .registry import (
    EurekaClient,
    EurekaMockClient,
    ServiceRegistry,
    create_registry_client,
)
from .errors import (
    RelayError,
    format_error_response,
    format_success_response,
)

__all__ = [
    # Clients
    "BaseClient",
    "CoreAPIClient",
    "HeartBeatClient",
    "AuditAPIClient",
    # Registry
    "EurekaClient",
    "EurekaMockClient",
    "ServiceRegistry",
    "create_registry_client",
    # Errors
    "RelayError",
    "format_error_response",
    "format_success_response",
]
