"""
Unified SSE Event Publisher (SSE Spec Sections 1, 4)

Single entry point for all HeartBeat SSE events:
  1. Writes to event_ledger (persistent store for replay/catchup)
  2. Pushes to SSEEventBus (authenticated SSE stream subscribers)

Usage:
    # System event (no entity transaction):
    await publish_event("config.updated", {"changed": ["webhook"]})

    # Entity event (same-transaction with entity write):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO file_entries ...")
        sequence = ledger.record(conn, "blob.uploaded", data, company_id)
        conn.commit()
    await publish_event_with_sequence("blob.uploaded", data, sequence)
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..config import get_config
from ..database.event_ledger import get_event_ledger

logger = logging.getLogger(__name__)

# Watermark cache invalidation callbacks (registered by sse.py)
_watermark_invalidation_callbacks: List[Callable[[str], None]] = []


def register_watermark_invalidation(callback: Callable[[str], None]) -> None:
    """Register a callback that's called with company_id on every publish."""
    _watermark_invalidation_callbacks.append(callback)


async def publish_event(
    event_type: str,
    data: Dict[str, Any],
    company_id: Optional[str] = None,
    data_uuid: Optional[str] = None,
    target_user_id: Optional[str] = None,
    target_role: Optional[str] = None,
    required_permission: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """
    Publish an SSE event through the unified pipeline.

    1. Write to event_ledger (gets monotonic sequence)
    2. Push to SSEEventBus (authenticated /api/sse/stream subscribers)

    Args:
        event_type: Dotted entity.verb (e.g., "blob.uploaded")
        data: Event payload dict
        company_id: Tenant ID. Defaults to config.company_id.
        data_uuid: Optional batch/entity group filter key.
        target_user_id: Deliver only to this user (e.g., cipher_refresh).
        target_role: Deliver only to users at or above this role.
        required_permission: Deliver only to users with this permission.
        conn: Existing DB connection for same-transaction writes.
              If provided, caller is responsible for commit.
              If None, ledger uses a standalone transaction.

    Returns:
        Sequence number from event_ledger.
    """
    config = get_config()
    effective_company_id = company_id or config.company_id

    # Step 1: Write to event ledger
    ledger = get_event_ledger()
    if conn is not None:
        sequence = ledger.record(conn, event_type, data, effective_company_id, data_uuid)
    else:
        sequence = ledger.record_standalone(event_type, data, effective_company_id, data_uuid)

    # Step 2: Push to SSEEventBus (authenticated subscribers)
    await _push_to_sse_bus(event_type, data, sequence, effective_company_id,
                           target_user_id, target_role, required_permission)

    # Step 3: Invalidate watermark cache for this tenant
    for callback in _watermark_invalidation_callbacks:
        try:
            callback(effective_company_id)
        except Exception:
            pass

    # Prometheus metrics
    try:
        from ..observability.metrics import SSE_EVENTS_PUBLISHED
        SSE_EVENTS_PUBLISHED.labels(service="heartbeat", event_type=event_type).inc()
    except Exception:
        pass

    logger.debug(f"Event published: seq={sequence} type={event_type}")
    return sequence


async def publish_event_with_sequence(
    event_type: str,
    data: Dict[str, Any],
    sequence: int,
    company_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    target_role: Optional[str] = None,
    required_permission: Optional[str] = None,
) -> None:
    """
    Push an already-recorded event to SSE subscribers.

    Use when the caller already wrote to event_ledger in a shared
    transaction (via EventLedger.record(conn, ...)) and just needs
    to fan out to live subscribers.
    """
    config = get_config()
    effective_company_id = company_id or config.company_id

    await _push_to_sse_bus(event_type, data, sequence, effective_company_id,
                           target_user_id, target_role, required_permission)

    for callback in _watermark_invalidation_callbacks:
        try:
            callback(effective_company_id)
        except Exception:
            pass

    logger.debug(f"Event pushed: seq={sequence} type={event_type}")


async def _push_to_sse_bus(
    event_type: str,
    data: Dict[str, Any],
    sequence: int,
    company_id: str,
    target_user_id: Optional[str],
    target_role: Optional[str],
    required_permission: Optional[str],
) -> None:
    """Push event to SSEEventBus (authenticated subscribers)."""
    try:
        from .producer import SSEEvent, get_event_bus
        event = SSEEvent(
            event_type=event_type,
            data=data,
            sequence=sequence,
            target_user_id=target_user_id,
            target_role=target_role,
            required_permission=required_permission,
        )
        bus = get_event_bus()
        await bus.publish(event)
    except Exception as e:
        logger.warning(f"SSEEventBus push failed (non-critical): {e}")
