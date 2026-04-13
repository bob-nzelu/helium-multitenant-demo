"""
WS6: Audit Logger — Fire-and-forget audit trail for the entire Core pipeline.

Records every significant action across WS1-WS5 to core.audit_log.
Audit failures are logged but NEVER propagated — observability must not
block business operations.

This is the cross-cutting audit logger (pool-based).
WS5's finalize/audit_logger.py (conn-based, transaction-scoped) is separate.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool
from uuid6 import uuid7

logger = structlog.get_logger()


AUDIT_INSERT = """
    INSERT INTO core.audit_log (
        audit_id, event_type, entity_type, entity_id, action,
        actor_id, actor_type, company_id, x_trace_id,
        before_state, after_state, changed_fields, metadata
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s
    )
"""

AUDIT_INSERT_BATCH = """
    INSERT INTO core.audit_log (
        audit_id, event_type, entity_type, entity_id, action,
        actor_id, actor_type, company_id, x_trace_id,
        before_state, after_state, changed_fields, metadata
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s, %s
    )
"""


def _json_or_none(value: dict | None) -> str | None:
    """Serialize dict to JSON string, or None."""
    if value is None:
        return None
    return json.dumps(value)


class AuditLogger:
    """
    Cross-cutting audit logger for the Core pipeline.

    Pool-based: acquires its own connection for each write.
    Fire-and-forget: all exceptions are caught internally.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def log(
        self,
        event_type: str,
        entity_type: str,
        action: str,
        company_id: str = "",
        entity_id: str | None = None,
        actor_id: str | None = None,
        actor_type: str = "system",
        x_trace_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        changed_fields: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Write a single audit log entry.

        Returns the audit_id on success, None on failure.
        """
        audit_id = str(uuid7())
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    AUDIT_INSERT,
                    (
                        audit_id,
                        event_type,
                        entity_type,
                        entity_id,
                        action,
                        actor_id,
                        actor_type,
                        company_id,
                        x_trace_id,
                        _json_or_none(before_state),
                        _json_or_none(after_state),
                        changed_fields,
                        _json_or_none(metadata),
                    ),
                )
            return audit_id
        except Exception as e:
            logger.error(
                "audit_write_failed",
                error=str(e),
                event_type=event_type,
                entity_type=entity_type,
                action=action,
            )
            return None

    async def log_batch(
        self,
        events: list[dict[str, Any]],
    ) -> list[str]:
        """
        Batch insert multiple audit events.

        Each event dict has the same keys as log() parameters.
        Returns list of audit_ids for successfully inserted events.
        """
        if not events:
            return []

        audit_ids: list[str] = []
        rows: list[tuple] = []

        for event in events:
            audit_id = str(uuid7())
            audit_ids.append(audit_id)
            rows.append((
                audit_id,
                event.get("event_type", ""),
                event.get("entity_type", ""),
                event.get("entity_id"),
                event.get("action", ""),
                event.get("actor_id"),
                event.get("actor_type", "system"),
                event.get("company_id", ""),
                event.get("x_trace_id"),
                _json_or_none(event.get("before_state")),
                _json_or_none(event.get("after_state")),
                event.get("changed_fields"),
                _json_or_none(event.get("metadata")),
            ))

        try:
            async with self._pool.connection() as conn:
                await conn.executemany(AUDIT_INSERT_BATCH, rows)
            return audit_ids
        except Exception as e:
            logger.error(
                "audit_batch_write_failed",
                error=str(e),
                event_count=len(events),
            )
            return []
