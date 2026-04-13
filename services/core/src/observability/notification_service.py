"""
WS6: Notification Service — Create, deliver, and query notifications.

Notifications are stored in the notifications schema and pushed to
Float via SSE. Fire-and-forget: failures are logged but never propagated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool
from uuid6 import uuid7

from src.sse.models import SSEEvent

logger = structlog.get_logger()


NOTIFICATION_INSERT = """
    INSERT INTO notifications.notifications (
        notification_id, company_id, recipient_id, notification_type,
        category, title, body, priority, data, expires_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

NOTIFICATION_LIST = """
    SELECT
        n.notification_id, n.company_id, n.recipient_id, n.notification_type,
        n.category, n.title, n.body, n.priority, n.data,
        n.created_at, n.expires_at,
        nr.read_at IS NOT NULL AS is_read,
        nr.read_at
    FROM notifications.notifications n
    LEFT JOIN notifications.notification_reads nr
        ON n.notification_id = nr.notification_id AND nr.read_by = %s
    WHERE n.company_id = %s
      AND (n.recipient_id IS NULL OR n.recipient_id = %s)
"""

MARK_READ_INSERT = """
    INSERT INTO notifications.notification_reads (read_id, notification_id, read_by)
    VALUES (%s, %s, %s)
    ON CONFLICT (notification_id, read_by) DO NOTHING
"""

# We need a unique constraint for the ON CONFLICT above — create it if missing.
# Actually, the PK is read_id. We need a UNIQUE on (notification_id, read_by).
# This is handled in the SQL schema via a unique index.
MARK_READ_CHECK = """
    SELECT 1 FROM notifications.notification_reads
    WHERE notification_id = %s AND read_by = %s
"""

MARK_READ_SIMPLE = """
    INSERT INTO notifications.notification_reads (read_id, notification_id, read_by)
    VALUES (%s, %s, %s)
"""

UNREAD_COUNT = """
    SELECT COUNT(*)
    FROM notifications.notifications n
    WHERE n.company_id = %s
      AND (n.recipient_id IS NULL OR n.recipient_id = %s)
      AND NOT EXISTS (
          SELECT 1 FROM notifications.notification_reads nr
          WHERE nr.notification_id = n.notification_id AND nr.read_by = %s
      )
"""

CLEANUP_EXPIRED = """
    DELETE FROM notifications.notifications
    WHERE expires_at IS NOT NULL AND expires_at < NOW()
"""


class NotificationService:
    """Create and deliver notifications via DB + SSE."""

    def __init__(self, pool: AsyncConnectionPool, sse_manager: Any) -> None:
        self._pool = pool
        self._sse_manager = sse_manager

    async def send(
        self,
        company_id: str,
        notification_type: str,
        category: str,
        title: str,
        body: str,
        recipient_id: str | None = None,
        priority: str = "normal",
        data: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> str | None:
        """
        Create a notification: INSERT into DB + push via SSE.

        Returns notification_id on success, None on failure.
        Fire-and-forget: exceptions are caught internally.
        """
        notification_id = str(uuid7())
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    NOTIFICATION_INSERT,
                    (
                        notification_id,
                        company_id,
                        recipient_id,
                        notification_type,
                        category,
                        title,
                        body,
                        priority,
                        json.dumps(data) if data else None,
                        expires_at,
                    ),
                )

            # Push via SSE
            try:
                await self._sse_manager.publish(SSEEvent(
                    event_type="notification.created",
                    data={
                        "notification_id": notification_id,
                        "company_id": company_id,
                        "notification_type": notification_type,
                        "category": category,
                        "title": title,
                        "priority": priority,
                    },
                ))
            except Exception as sse_err:
                logger.warning("notification_sse_push_failed", error=str(sse_err))

            return notification_id
        except Exception as e:
            logger.error(
                "notification_send_failed",
                error=str(e),
                category=category,
                company_id=company_id,
            )
            return None

    async def mark_read(
        self,
        notification_id: str,
        user_id: str,
    ) -> bool:
        """
        Mark a notification as read for a user.

        Idempotent: re-reading an already-read notification is a no-op.
        Returns True if marked (or already read), False on error.
        """
        try:
            async with self._pool.connection() as conn:
                # Check if already read
                cur = await conn.execute(MARK_READ_CHECK, (notification_id, user_id))
                if await cur.fetchone():
                    return True  # Already read

                read_id = str(uuid7())
                await conn.execute(
                    MARK_READ_SIMPLE,
                    (read_id, notification_id, user_id),
                )
            return True
        except Exception as e:
            logger.error(
                "notification_mark_read_failed",
                error=str(e),
                notification_id=notification_id,
            )
            return False

    async def list_for_user(
        self,
        company_id: str,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Paginated notification list for a user.

        Returns (notifications, total_count).
        """
        try:
            query = NOTIFICATION_LIST
            params: list[Any] = [user_id, company_id, user_id]

            if unread_only:
                query += """
                    AND NOT EXISTS (
                        SELECT 1 FROM notifications.notification_reads nr2
                        WHERE nr2.notification_id = n.notification_id AND nr2.read_by = %s
                    )
                """
                params.append(user_id)

            # Count total
            count_query = f"SELECT COUNT(*) FROM ({query}) sub"
            async with self._pool.connection() as conn:
                cur = await conn.execute(count_query, params)
                row = await cur.fetchone()
                total = row[0] if row else 0

                # Fetch page
                page_query = query + " ORDER BY n.created_at DESC LIMIT %s OFFSET %s"
                page_params = params + [limit, offset]
                cur = await conn.execute(page_query, page_params)
                rows = await cur.fetchall()

            notifications = []
            for r in rows:
                notifications.append({
                    "notification_id": r[0],
                    "company_id": r[1],
                    "recipient_id": r[2],
                    "notification_type": r[3],
                    "category": r[4],
                    "title": r[5],
                    "body": r[6],
                    "priority": r[7],
                    "data": json.loads(r[8]) if r[8] else None,
                    "created_at": r[9].isoformat() if r[9] else None,
                    "expires_at": r[10].isoformat() if r[10] else None,
                    "is_read": bool(r[11]),
                    "read_at": r[12].isoformat() if r[12] else None,
                })

            return notifications, total
        except Exception as e:
            logger.error("notification_list_failed", error=str(e))
            return [], 0

    async def unread_count(
        self,
        company_id: str,
        user_id: str,
    ) -> int:
        """Get unread notification count for badge display."""
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    UNREAD_COUNT, (company_id, user_id, user_id)
                )
                row = await cur.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.error("notification_unread_count_failed", error=str(e))
            return 0

    async def cleanup_expired(self) -> int:
        """Delete expired notifications. Returns count deleted."""
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(CLEANUP_EXPIRED)
                return cur.rowcount or 0
        except Exception as e:
            logger.error("notification_cleanup_failed", error=str(e))
            return 0
