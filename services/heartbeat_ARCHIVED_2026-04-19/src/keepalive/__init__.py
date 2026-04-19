"""
Keep Alive Manager — HeartBeat's process lifecycle orchestrator.

Manages startup, monitoring, restart, and shutdown of all child services
(Core, Relay, HIS, Edge). Implements the full HEARTBEAT_LIFECYCLE_SPEC.

Components:
    ProcessHandle   — Wraps subprocess.Popen for a single managed service
    HealthPoller    — Polls child service /health endpoints
    KeepAliveManager — Orchestrates lifecycle across all services
"""

from .manager import KeepAliveManager, get_keepalive_manager, reset_keepalive_manager

__all__ = [
    "KeepAliveManager",
    "get_keepalive_manager",
    "reset_keepalive_manager",
]
