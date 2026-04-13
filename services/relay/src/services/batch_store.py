"""
Batch result store with PostgreSQL persistence and SSE broadcasting.

Stores batch results in PostgreSQL for durability across restarts,
and broadcasts to connected dashboard clients in real time via SSE.

Clear History (PIN-protected in dashboard) deletes all rows.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import asyncpg

logger = logging.getLogger(__name__)

MAX_BATCHES = 500

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS relay_batch_results (
    id          SERIAL PRIMARY KEY,
    batch_id    TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    batch_data  JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_relay_batch_results_batch_id
    ON relay_batch_results (batch_id);
"""

ADD_TENANT_COL = """
ALTER TABLE relay_batch_results ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
"""


class BatchStore:
    """
    PostgreSQL-backed batch result store with SSE broadcast.

    Every batch that passes through ExternalService is persisted here
    and broadcast to all connected SSE subscribers in real time.
    """

    def __init__(self, max_batches: int = MAX_BATCHES):
        self._pool: Optional[asyncpg.Pool] = None
        self._max_batches = max_batches
        self._subscribers: Set[asyncio.Queue] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def connect(self, database_url: str) -> None:
        """Connect to PostgreSQL and ensure the table exists."""
        self._pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLE)
            await conn.execute(CREATE_INDEX)
            # Migration: add tenant_id column if upgrading from pre-multi-tenant
            await conn.execute(ADD_TENANT_COL)
        logger.info("BatchStore: connected to PostgreSQL")

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── Write ─────────────────────────────────────────────────────────────

    async def add(self, batch_record: Dict, tenant_id: str = "") -> None:
        """Persist a batch result to PostgreSQL and broadcast to SSE clients."""
        if "timestamp" not in batch_record:
            batch_record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Persist
        if self._pool:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO relay_batch_results (batch_id, tenant_id, batch_data) VALUES ($1, $2, $3)",
                    batch_record.get("batch_id", ""),
                    tenant_id,
                    json.dumps(batch_record),
                )
                # Trim old rows if over limit
                count = await conn.fetchval("SELECT COUNT(*) FROM relay_batch_results")
                if count > self._max_batches:
                    await conn.execute(
                        "DELETE FROM relay_batch_results WHERE id IN "
                        "(SELECT id FROM relay_batch_results ORDER BY id ASC LIMIT $1)",
                        count - self._max_batches,
                    )

        # Broadcast to all SSE subscribers
        for q in self._subscribers:
            try:
                q.put_nowait(batch_record)
            except asyncio.QueueFull:
                logger.warning(
                    "BatchStore: subscriber queue full, skipping batch %s",
                    batch_record.get("batch_id"),
                )

        logger.debug(
            f"BatchStore: stored {batch_record.get('batch_id')}"
        )

    async def clear(self, tenant_id: Optional[str] = None) -> None:
        """
        Delete batch results. PIN-protected in dashboard.

        If tenant_id is given, only that tenant's data is deleted.
        If None, all data is deleted (admin action).
        """
        if self._pool:
            async with self._pool.acquire() as conn:
                if tenant_id:
                    await conn.execute(
                        "DELETE FROM relay_batch_results WHERE tenant_id = $1",
                        tenant_id,
                    )
                else:
                    await conn.execute("DELETE FROM relay_batch_results")
        logger.info(f"BatchStore: cleared batch results (tenant={tenant_id or 'ALL'})")

    # ── Read ──────────────────────────────────────────────────────────────

    async def all(self, tenant_id: Optional[str] = None) -> List[Dict]:
        """Return stored batches, newest first. Optionally filter by tenant."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            if tenant_id:
                rows = await conn.fetch(
                    "SELECT batch_data FROM relay_batch_results WHERE tenant_id = $1 ORDER BY id DESC LIMIT $2",
                    tenant_id, self._max_batches,
                )
            else:
                rows = await conn.fetch(
                    "SELECT batch_data FROM relay_batch_results ORDER BY id DESC LIMIT $1",
                    self._max_batches,
                )
        return [json.loads(row["batch_data"]) for row in rows]

    async def lookup(self, transaction_id: str, tenant_id: Optional[str] = None) -> Optional[Dict]:
        """
        Search stored batches for a transaction by ID.
        If tenant_id given, only searches that tenant's batches.
        """
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            if tenant_id:
                rows = await conn.fetch(
                    "SELECT batch_data FROM relay_batch_results WHERE tenant_id = $1 ORDER BY id DESC",
                    tenant_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT batch_data FROM relay_batch_results ORDER BY id DESC",
                )
        for row in rows:
            batch = json.loads(row["batch_data"])
            batch_meta = {
                "batch_id":    batch.get("batch_id"),
                "timestamp":   batch.get("timestamp"),
                "source":      batch.get("source"),
                "source_id":   batch.get("source_id"),
                "tenant_id":   batch.get("tenant_id"),
                "tenant_name": batch.get("tenant_name"),
            }
            for rec in batch.get("processed", []):
                if rec.get("transaction_id") == transaction_id:
                    return {"status": "found", "result": "processed", **rec, **batch_meta}
            for rec in batch.get("duplicates", []):
                if rec.get("transaction_id") == transaction_id:
                    return {"status": "found", "result": "duplicate", **rec, **batch_meta}
            for rec in batch.get("failed", []):
                if rec.get("transaction_id") == transaction_id:
                    return {"status": "found", "result": "failed", **rec, **batch_meta}
        return None

    # ── SSE subscription ──────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE subscriber; return its event queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Deregister a subscriber queue."""
        self._subscribers.discard(q)
