"""
Queue Repository - Operations for core_queue table.

Manages the Core processing queue:
- Relay writes entries (enqueue)
- Core reads and processes entries (dequeue, mark_processing, mark_completed)
- Priority-based ordering (1=highest, 10=lowest)

Written by Relay, read/processed by Core.

Reference: DATABASE_SCHEMAS.md (core_queue), DATABASE_DECISIONS.md (Decision 4)
"""

import logging
from typing import Any, Optional

from uuid6 import uuid7

from ..resource_client import ResourceClient

logger = logging.getLogger(__name__)


class QueueRepository:
    """Repository for core_queue operations.

    Implements a database-backed queue with priority ordering.
    Queue entries flow: PENDING -> PROCESSING -> COMPLETED|FAILED.

    Args:
        client: Connected ResourceClient for core database.
    """

    def __init__(self, client: ResourceClient):
        self.client = client

    async def enqueue(self, queue_data: dict[str, Any]) -> str:
        """Add a new entry to the processing queue.

        Args:
            queue_data: Dictionary with queue entry fields. Required:
                - blob_uuid: File UUID in blob storage
                - company_id: Company identifier
            Optional:
                - queue_id: Unique queue ID (auto-generated if not provided)
                - data_uuid: Per-request group UUID (from Relay)
                - original_filename: Original file name
                - immediate_processing: Auto-process flag (default True)
                - batch_id: Batch identifier
                - uploaded_by: User email
                - priority: 1-10 (default 5)

        Returns:
            The queue_id of the created entry.
        """
        queue_id = queue_data.get("queue_id") or f"Q-{uuid7().hex[:16].upper()}"

        query = """
            INSERT INTO core_queue (
                queue_id, data_uuid, blob_uuid, original_filename,
                immediate_processing, batch_id,
                company_id, uploaded_by,
                status, priority
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """
        params = (
            queue_id,
            queue_data.get("data_uuid"),
            queue_data["blob_uuid"],
            queue_data.get("original_filename"),
            queue_data.get("immediate_processing", True),
            queue_data.get("batch_id"),
            queue_data["company_id"],
            queue_data.get("uploaded_by"),
            "PENDING",
            queue_data.get("priority", 5),
        )

        await self.client.execute(query, params)
        logger.info("Enqueued %s (blob=%s)", queue_id, queue_data["blob_uuid"])
        return queue_id

    async def dequeue(self, priority_order: bool = True) -> Optional[dict[str, Any]]:
        """Get the next pending queue entry and mark it as PROCESSING.

        Atomically selects the highest-priority PENDING entry and
        updates its status to PROCESSING.

        Args:
            priority_order: If True, order by priority ASC then created_at ASC
                           (lower number = higher priority). Default True.

        Returns:
            Queue entry dict or None if queue is empty.
        """
        order_clause = "priority ASC, created_at ASC" if priority_order else "created_at ASC"

        # Select next pending entry
        select_query = f"""
            SELECT * FROM core_queue
            WHERE status = 'PENDING'
            ORDER BY {order_clause}
            LIMIT 1
        """
        entry = await self.client.fetch_one(select_query)

        if not entry:
            return None

        # Mark as PROCESSING
        update_query = """
            UPDATE core_queue
            SET status = 'PROCESSING',
                processing_started_at = CURRENT_TIMESTAMP
            WHERE queue_id = $1 AND status = 'PENDING'
        """
        rows_affected = await self.client.execute(update_query, (entry["queue_id"],))

        if rows_affected > 0:
            logger.info("Dequeued %s (priority=%d)", entry["queue_id"], entry["priority"])
            # Refresh to get updated fields
            return await self.get_entry(entry["queue_id"])

        # Race condition: another worker grabbed it
        return None

    async def get_entry(self, queue_id: str) -> Optional[dict[str, Any]]:
        """Get a queue entry by queue_id.

        Args:
            queue_id: Unique queue entry identifier.

        Returns:
            Queue entry dict or None.
        """
        query = "SELECT * FROM core_queue WHERE queue_id = $1"
        return await self.client.fetch_one(query, (queue_id,))

    async def mark_processing(self, queue_id: str) -> bool:
        """Mark a queue entry as PROCESSING.

        Args:
            queue_id: Unique queue entry identifier.

        Returns:
            True if entry was found and updated.
        """
        query = """
            UPDATE core_queue
            SET status = 'PROCESSING',
                processing_started_at = CURRENT_TIMESTAMP
            WHERE queue_id = $1 AND status = 'PENDING'
        """
        rows_affected = await self.client.execute(query, (queue_id,))
        if rows_affected > 0:
            logger.info("Marked %s as PROCESSING", queue_id)
            return True
        return False

    async def mark_completed(self, queue_id: str) -> bool:
        """Mark a queue entry as COMPLETED.

        Args:
            queue_id: Unique queue entry identifier.

        Returns:
            True if entry was found and updated.
        """
        query = """
            UPDATE core_queue
            SET status = 'COMPLETED',
                processing_completed_at = CURRENT_TIMESTAMP
            WHERE queue_id = $1 AND status = 'PROCESSING'
        """
        rows_affected = await self.client.execute(query, (queue_id,))
        if rows_affected > 0:
            logger.info("Marked %s as COMPLETED", queue_id)
            return True
        return False

    async def mark_failed(self, queue_id: str, error_message: str) -> bool:
        """Mark a queue entry as FAILED with error details.

        Args:
            queue_id: Unique queue entry identifier.
            error_message: Description of the failure.

        Returns:
            True if entry was found and updated.
        """
        query = """
            UPDATE core_queue
            SET status = 'FAILED',
                processing_completed_at = CURRENT_TIMESTAMP,
                error_message = $1
            WHERE queue_id = $2 AND status = 'PROCESSING'
        """
        rows_affected = await self.client.execute(query, (error_message, queue_id))
        if rows_affected > 0:
            logger.info("Marked %s as FAILED: %s", queue_id, error_message)
            return True
        return False

    async def mark_preview_ready(self, queue_id: str, hlx_blob_uuid: str) -> bool:
        """Mark a queue entry as PREVIEW_READY and store the HLX blob reference.

        Called by WS3 after a .hlx preview has been generated and uploaded
        to HeartBeat blob store.

        Args:
            queue_id: Unique queue entry identifier.
            hlx_blob_uuid: UUID of the generated .hlx blob in HeartBeat.

        Returns:
            True if entry was found and updated.
        """
        query = """
            UPDATE core_queue
            SET status = 'PREVIEW_READY',
                processed_at = CURRENT_TIMESTAMP,
                error_message = $1
            WHERE queue_id = $2 AND status IN ('PROCESSING', 'PROCESSED')
        """
        # Store hlx_blob_uuid in error_message column temporarily until a
        # dedicated column is added; callers can also use get_entry() to read it.
        rows_affected = await self.client.execute(query, (hlx_blob_uuid, queue_id))
        if rows_affected > 0:
            logger.info("Marked %s as PREVIEW_READY (hlx_blob=%s)", queue_id, hlx_blob_uuid)
            return True
        return False

    async def mark_cancelled(self, queue_id: str) -> bool:
        """Mark a queue entry as CANCELLED.

        Valid from any non-terminal status (PENDING or PROCESSING).

        Args:
            queue_id: Unique queue entry identifier.

        Returns:
            True if entry was found and updated.
        """
        query = """
            UPDATE core_queue
            SET status = 'CANCELLED',
                processed_at = CURRENT_TIMESTAMP
            WHERE queue_id = $1 AND status IN ('PENDING', 'PROCESSING')
        """
        rows_affected = await self.client.execute(query, (queue_id,))
        if rows_affected > 0:
            logger.info("Marked %s as CANCELLED", queue_id)
            return True
        return False

    async def get_pending_count(self) -> int:
        """Get count of pending queue entries.

        Returns:
            Number of entries with PENDING status.
        """
        query = "SELECT COUNT(*) as count FROM core_queue WHERE status = 'PENDING'"
        row = await self.client.fetch_one(query)
        return row["count"] if row else 0

    async def get_processing_count(self) -> int:
        """Get count of currently processing entries.

        Returns:
            Number of entries with PROCESSING status.
        """
        query = "SELECT COUNT(*) as count FROM core_queue WHERE status = 'PROCESSING'"
        row = await self.client.fetch_one(query)
        return row["count"] if row else 0

    async def list_entries(
        self,
        status: Optional[str] = None,
        company_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List queue entries with optional filters.

        Args:
            status: Filter by status (PENDING, PROCESSING, COMPLETED, FAILED).
            company_id: Filter by company.
            batch_id: Filter by batch.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of queue entry dicts.
        """
        where_clauses = []
        params = []
        param_idx = 1

        if status:
            where_clauses.append(f"status = ${param_idx}")
            params.append(status)
            param_idx += 1
        if company_id:
            where_clauses.append(f"company_id = ${param_idx}")
            params.append(company_id)
            param_idx += 1
        if batch_id:
            where_clauses.append(f"batch_id = ${param_idx}")
            params.append(batch_id)
            param_idx += 1

        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        params.extend([limit, offset])
        query = f"""
            SELECT * FROM core_queue
            {where_str}
            ORDER BY priority ASC, created_at ASC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """
        return await self.client.fetch_all(query, tuple(params))

    async def get_batch_status(self, batch_id: str) -> dict[str, int]:
        """Get status summary for a batch.

        Args:
            batch_id: Batch identifier.

        Returns:
            Dict mapping status to count, e.g.:
            {"PENDING": 3, "PROCESSING": 1, "COMPLETED": 10, "FAILED": 0}
        """
        query = """
            SELECT status, COUNT(*) as count
            FROM core_queue
            WHERE batch_id = $1
            GROUP BY status
        """
        rows = await self.client.fetch_all(query, (batch_id,))

        result = {"PENDING": 0, "PROCESSING": 0, "COMPLETED": 0, "FAILED": 0}
        for row in rows:
            result[row["status"]] = row["count"]
        return result
