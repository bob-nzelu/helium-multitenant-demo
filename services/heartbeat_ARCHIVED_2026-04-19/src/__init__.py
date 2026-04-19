"""
HeartBeat Service — Helium's Central Nervous System

Responsibilities:
1. Blob storage (filesystem write/read, metadata tracking in blob.db)
2. Deduplication (SHA256 hash check, per-source tracking)
3. Daily usage limits (per-company quotas)
4. Audit logging (immutable, append-only event trail)
5. Metrics reporting (ingestion counts, processing times, error rates)
6. Service registry (dynamic service discovery + API key management)
7. Service health monitoring (Primary keeps all services alive)
8. 7-year FIRS compliance retention management

Architecture:
    Primary — Central node. Owns all data, registry, and blob storage.
    Satellite — Lightweight proxy on other servers (future).
"""

__version__ = "2.0.0"
__service__ = "heartbeat"
