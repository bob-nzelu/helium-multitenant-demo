"""
SSE Data Models

SSEEvent: An event to publish to connected clients.
SSEClient: A connected SSE client with its own async queue.

Per SSE_SPEC v1.1:
- Events carry sequence, event_type, data, timestamp, source, company_id.
- Clients are scoped to a company_id (tenant isolation).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SSEEvent:
    """An event to publish through the SSE transport."""

    event_type: str
    data: dict[str, Any]
    data_uuid: str | None = None
    company_id: str | None = None  # Tenant scope (Section 11.1)
    id: int | None = None  # Sequence, assigned by SSEConnectionManager
    timestamp: str | None = None  # ISO 8601 UTC, set at publish time (Section 1.2)
    source: str = "core"  # Producing service identifier (Section 1.2)


@dataclass
class SSEClient:
    """A connected SSE client."""

    client_id: str
    company_id: str | None = None  # From JWT claims (Section 2.2)
    queue: asyncio.Queue[SSEEvent | None] = field(default_factory=asyncio.Queue)
    data_uuid_filter: str | None = None
    pattern_filter: str | None = None  # fnmatch pattern (Section 2.3)
    connected_at: float = field(default_factory=time.monotonic)
    jwt_exp: int = 0  # JWT expiry timestamp for per-write checks (Section 11.3)
