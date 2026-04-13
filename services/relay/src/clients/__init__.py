"""
HTTP clients for upstream services.

CoreClient      — Invoice processing (enqueue, preview, finalize)
HeartBeatClient — Everything else (blobs, dedup, limits, audit, metrics, health)

No standalone AuditClient — audit logging flows through HeartBeat
for immutability and centralized monitoring.
"""

from .base import BaseClient
from .core import CoreClient
from .heartbeat import HeartBeatClient

__all__ = ["BaseClient", "CoreClient", "HeartBeatClient"]
