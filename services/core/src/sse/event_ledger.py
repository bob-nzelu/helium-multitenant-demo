"""
Event Ledger — Persistent SSE event store.

Per SSE_SPEC v1.1 Section 4:
- Every published event is written to event_ledger in the same transaction
  as the entity change (or immediately after for non-transactional publishes).
- Supports catchup queries (Section 5) and watermark (Section 6).
- Pruning removes rows older than retention window (Section 4.3).

Per Section 6.4: Watermark response cached per company_id, 30s TTL,
invalidated on publish.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

logger = structlog.get_logger()

# Watermark cache TTL (Section 6.4)
WATERMARK_CACHE_TTL = 30.0


class EventLedger:
    """
    Persistent event store for SSE replay and reconciliation.

    Handles:
    - Writing events to the ledger (write)
    - Paginated replay queries (query) — Section 5
    - Watermark snapshots with caching (watermark) — Section 6
    - Pruning old rows (prune) — Section 4.3
    """

    def __init__(self, retention_hours: int = 48):
        self._retention_hours = retention_hours
        # Watermark cache: company_id → (timestamp, response_dict)
        self._watermark_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def write(
        self,
        pool: AsyncConnectionPool,
        event_type: str,
        data: dict[str, Any],
        timestamp: str,
        company_id: str,
        data_uuid: str | None = None,
    ) -> int:
        """
        Write an event to the ledger. Returns the assigned sequence.

        Per Section 4.2: This should be called in the same transaction
        as the entity change when possible.
        """
        data_json = json.dumps(data)

        async with pool.connection() as conn:
            row = await conn.execute(
                """
                INSERT INTO core.event_ledger (event_type, data_json, timestamp, data_uuid, company_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING sequence
                """,
                (event_type, data_json, timestamp, data_uuid, company_id),
            )
            result = await row.fetchone()
            await conn.commit()

        sequence = result[0]

        # Invalidate watermark cache for this tenant (Section 6.4)
        self._watermark_cache.pop(company_id, None)

        logger.debug(
            "ledger_event_written",
            sequence=sequence,
            event_type=event_type,
        )

        return sequence

    async def write_in_transaction(
        self,
        conn,
        event_type: str,
        data: dict[str, Any],
        timestamp: str,
        company_id: str,
        data_uuid: str | None = None,
    ) -> int:
        """
        Write an event to the ledger within an existing transaction.

        Per Section 4.2: "in the same database transaction as the entity
        change that triggered it."

        Args:
            conn: An active psycopg AsyncConnection (already in a transaction).

        Returns:
            The assigned sequence number.
        """
        data_json = json.dumps(data)

        row = await conn.execute(
            """
            INSERT INTO core.event_ledger (event_type, data_json, timestamp, data_uuid, company_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING sequence
            """,
            (event_type, data_json, timestamp, data_uuid, company_id),
        )
        result = await row.fetchone()
        sequence = result[0]

        # Invalidate watermark cache
        self._watermark_cache.pop(company_id, None)

        return sequence

    async def query(
        self,
        pool: AsyncConnectionPool,
        company_id: str,
        after_sequence: int,
        limit: int = 500,
        data_uuid: str | None = None,
        pattern: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """
        Paginated catchup query (Section 5).

        Returns:
            (events, has_more, oldest_available)
        """
        limit = min(limit, 1000)

        async with pool.connection() as conn:
            # Get oldest available sequence
            row = await conn.execute(
                "SELECT COALESCE(MIN(sequence), 0) FROM core.event_ledger WHERE company_id = %s",
                (company_id,),
            )
            oldest_available = (await row.fetchone())[0]

            # Build query with optional filters
            conditions = ["company_id = %s", "sequence > %s"]
            params: list[Any] = [company_id, after_sequence]

            if data_uuid:
                conditions.append("data_uuid = %s")
                params.append(data_uuid)

            if pattern:
                # Convert fnmatch pattern to SQL LIKE:
                # '*' → '%', '?' → '_', escape existing '%' and '_'
                sql_pattern = pattern.replace("%", r"\%").replace("_", r"\_")
                sql_pattern = sql_pattern.replace("*", "%").replace("?", "_")
                conditions.append("event_type LIKE %s")
                params.append(sql_pattern)

            where = " AND ".join(conditions)
            # Fetch limit + 1 to detect has_more
            params.append(limit + 1)

            rows = await conn.execute(
                f"""
                SELECT sequence, event_type, data_json, timestamp
                FROM core.event_ledger
                WHERE {where}
                ORDER BY sequence ASC
                LIMIT %s
                """,
                params,
            )
            results = await rows.fetchall()

        has_more = len(results) > limit
        results = results[:limit]

        events = []
        for seq, event_type, data_json, ts in results:
            events.append({
                "sequence": seq,
                "event_type": event_type,
                "data": json.loads(data_json),
                "timestamp": ts,
                "source": "core",
            })

        return events, has_more, oldest_available

    async def watermark(
        self,
        pool: AsyncConnectionPool,
        company_id: str,
    ) -> dict[str, Any]:
        """
        Reconciliation watermark (Section 6).

        Per Section 6.4: Cached per company_id with 30s TTL.
        Cache invalidated on publish (write/write_in_transaction).
        """
        now = time.time()

        # Check cache — return a copy to avoid shared-reference mutation
        if company_id in self._watermark_cache:
            cached_at, cached_response = self._watermark_cache[company_id]
            if now - cached_at < WATERMARK_CACHE_TTL:
                result = dict(cached_response)
                result["cached"] = True
                return result

        async with pool.connection() as conn:
            # Ledger bounds — single query, schema-qualified (no search_path switching)
            row = await conn.execute(
                """
                SELECT COALESCE(MIN(sequence), 0),
                       COALESCE(MAX(sequence), 0)
                FROM core.event_ledger
                WHERE company_id = %s
                """,
                (company_id,),
            )
            ledger_oldest, latest_sequence = await row.fetchone()

            # Entity counts — single query across all three schemas
            row = await conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM invoices.invoices
                     WHERE company_id = %s AND deleted_at IS NULL),
                    (SELECT COUNT(*) FROM customers.customers
                     WHERE company_id = %s AND deleted_at IS NULL),
                    (SELECT COUNT(*) FROM inventory.inventory
                     WHERE company_id = %s AND deleted_at IS NULL)
                """,
                (company_id, company_id, company_id),
            )
            inv_count, cust_count, prod_count = await row.fetchone()

            entity_counts = {
                "invoices": inv_count,
                "customers": cust_count,
                "products": prod_count,
            }

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        response = {
            "latest_sequence": latest_sequence,
            "entity_counts": entity_counts,
            "ledger_oldest": ledger_oldest,
            "timestamp": ts,
            "cached": False,
        }

        # Cache the response (Section 6.4)
        self._watermark_cache[company_id] = (now, response)

        return response

    async def prune(self, pool: AsyncConnectionPool) -> int:
        """
        Delete events older than retention window (Section 4.3).

        Per spec: rows retained for minimum 48 hours, pruning runs every 6 hours.
        Uses batched DELETE to avoid locking.

        Returns:
            Number of rows deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        total_deleted = 0
        batch_size = 10000

        async with pool.connection() as conn:
            while True:
                row = await conn.execute(
                    """
                    DELETE FROM core.event_ledger
                    WHERE sequence IN (
                        SELECT sequence FROM core.event_ledger
                        WHERE timestamp < %s
                        LIMIT %s
                    )
                    """,
                    (cutoff_str, batch_size),
                )
                deleted = row.rowcount
                await conn.commit()

                total_deleted += deleted

                if deleted < batch_size:
                    break

        if total_deleted > 0:
            # Update ledger size metric
            async with pool.connection() as conn:
                row = await conn.execute("SELECT COUNT(*) FROM core.event_ledger")
                count = (await row.fetchone())[0]

            from src.sse.manager import sse_ledger_size
            sse_ledger_size.labels(service="core").set(count)

            logger.info(
                "ledger_pruned",
                rows_deleted=total_deleted,
                cutoff=cutoff_str,
            )

        return total_deleted
