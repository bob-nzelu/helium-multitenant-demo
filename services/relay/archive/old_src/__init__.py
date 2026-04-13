"""
Relay Module

Universal ingestion layer for raw invoice, customer, and inventory data from ANY source.

Components:
- base.py: BaseRelayService abstract base class
- factory.py: RelayServiceFactory for creating service instances
- exceptions.py: Relay-specific exceptions
- bulk/: Relay Bulk service (Phase 1B)
- queue/: Relay Queue service (stub, Phase 2+)
- watcher/: Relay Watcher service (stub, Phase 2+)
- dbc/: Relay DBC service (stub, Phase 2+)
- api/: Relay API service (stub, Phase 2+)
- polling/: Relay Polling service (stub, Phase 2+)
- email/: Relay Email service (stub, Phase 2+)
"""

from .base import BaseRelayService
from .factory import RelayServiceFactory, get_relay_factory
from .exceptions import RelayServiceError

__all__ = [
    "BaseRelayService",
    "RelayServiceFactory",
    "get_relay_factory",
    "RelayServiceError",
]
