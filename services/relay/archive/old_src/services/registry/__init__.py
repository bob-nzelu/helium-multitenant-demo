"""
Services - Registry Module

Provides service discovery using Eureka (or mock for testing).
- EurekaClient: Real Eureka for Pro/Enterprise
- EurekaMockClient: Mock in-memory registry for Test/Standard
- ServiceRegistry: Singleton wrapper for global access
"""

from .eureka_client import EurekaClient
from .eureka_mock import EurekaMockClient
from .factory import create_registry_client, ServiceRegistry

__all__ = [
    "EurekaClient",
    "EurekaMockClient",
    "create_registry_client",
    "ServiceRegistry",
]
