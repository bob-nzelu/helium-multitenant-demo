"""
Services - Clients Module

Provides inter-service communication clients:
- BaseClient: Base HTTP client with retry logic
- CoreAPIClient: Communication with Core API
- HeartBeatClient: Communication with HeartBeat (blob, dedup, limits)
- AuditAPIClient: Audit event logging
"""

from .base_client import BaseClient
from .core_api_client import CoreAPIClient
from .heartbeat_client import HeartBeatClient
from .audit_client import AuditAPIClient

__all__ = [
    "BaseClient",
    "CoreAPIClient",
    "HeartBeatClient",
    "AuditAPIClient",
]
