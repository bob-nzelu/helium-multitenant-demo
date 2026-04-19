"""
Event Ledger — Persistent SSE Event Store (SSE Spec Section 4)

Stores every SSE event with a monotonic sequence number for:
- Last-Event-ID reconnect (ring buffer fallback)
- Paginated catchup (GET /api/sse/catchup)
- Reconciliation watermarks (GET /api/sse/watermark)

Table lives in blob.db alongside entity tables so that entity-triggered
events (blob.uploaded, blob.status_changed) can be written in the same
database transaction as the entity change.

Retention: 48 hours. Pruned every 6 hours by LedgerPruner.
"""

import asyncio
import fnmatch
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventLedger:
    """
    Read/write interface to the event_ledger table in blob.db.

    Two write modes:
      - record(conn, ...) — caller provides connection for same-transaction use
      - record_standalone(...) — opens own connection (system events)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def record(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        data: Dict[str, Any],
        company_id: str,
        data_uuid: Optional[str] = None,
    ) -> int:
        """
        Insert event into ledger using an existing connection.

        Use this when the caller needs same-transaction semantics
        (e.g., entity INSERT + ledger INSERT in one commit).

        Returns the auto-incremented sequence number.
        """
        now_iso = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
        data_json = json.dumps(data)

        cursor = conn.execute(
            """
            INSERT INTO event_ledger (event_type, data_json, timestamp, data_uuid, company_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, data_json, now_iso, data_uuid, company_id),
        )
        return cursor.lastrowid

    def record_standalone(
        self,
        event_type: str,
        data: Dict[str, Any],
        company_id: str,
        data_uuid: Optional[str] = None,
    ) -> int:
        """
        Insert event into ledger with its own connection/transaction.

        Use for system events (config.updated, schema.updated, etc.)
        that have no entity transaction to join.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            sequence = self.record(conn, event_type, data, company_id, data_uuid)
            conn.commit()
            return sequence
        finally:
            conn.close()

    def query_after(
        self,
        company_id: str,
        after_sequence: int,
        limit: int = 500,
        data_uuid: Optional[str] = None,
        pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Paginated event replay for the catchup endpoint (SSE Spec Section 5).

        Returns:
            {
                "events": [...],
                "has_more": bool,
                "next_sequence": int,
                "oldest_available": int
            }
        """
        if limit > 1000:
            limit = 1000

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Get oldest available sequence for this tenant
            row = conn.execute(
                "SELECT MIN(sequence) AS oldest FROM event_ledger WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            oldest_available = row["oldest"] if row and row["oldest"] is not None else 0

            # Build query
            conditions = ["company_id = ?", "sequence > ?"]
            params: list = [company_id, after_sequence]

            if data_uuid:
                conditions.append("data_uuid = ?")
                params.append(data_uuid)

            where = " AND ".join(conditions)
            # Fetch limit+1 to detect has_more
            params.append(limit + 1)

            rows = conn.execute(
                f"""
                SELECT sequence, event_type, data_json, timestamp, data_uuid
                FROM event_ledger
                WHERE {where}
                ORDER BY sequence ASC
                LIMIT ?
                """,
                params,
            ).fetchall()

            has_more = len(rows) > limit
            rows = rows[:limit]

            # Apply fnmatch pattern filter in Python (post-query)
            events = []
            for r in rows:
                if pattern and pattern != "*":
                    if not fnmatch.fnmatch(r["event_type"], pattern):
                        continue
                events.append({
                    "sequence": r["sequence"],
                    "event_type": r["event_type"],
                    "data": json.loads(r["data_json"]),
                    "timestamp": r["timestamp"],
                    "source": "heartbeat",
                })

            next_sequence = events[-1]["sequence"] if events else after_sequence

            return {
                "events": events,
                "has_more": has_more,
                "next_sequence": next_sequence,
                "oldest_available": oldest_available,
            }
        finally:
            conn.close()

    def get_watermark(self, company_id: str) -> Dict[str, Any]:
        """
        Watermark data for reconciliation (SSE Spec Section 6).

        Returns latest_sequence, ledger_oldest for the tenant.
        Entity counts are computed by the caller (needs access to
        entity tables which vary by service).
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT
                    COALESCE(MAX(sequence), 0) AS latest_sequence,
                    COALESCE(MIN(sequence), 0) AS ledger_oldest
                FROM event_ledger
                WHERE company_id = ?
                """,
                (company_id,),
            ).fetchone()

            return {
                "latest_sequence": row["latest_sequence"],
                "ledger_oldest": row["ledger_oldest"],
            }
        finally:
            conn.close()

    def get_entity_counts(self, company_id: str) -> Dict[str, int]:
        """
        Count entity rows for watermark response.

        HeartBeat entities: file_entries (blobs), blob_batches.
        These tables don't have company_id columns, so counts are
        global (appropriate for single-tenant HeartBeat deployments).
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            blobs = conn.execute(
                "SELECT COUNT(*) AS c FROM file_entries WHERE deleted_at_unix IS NULL"
            ).fetchone()["c"]

            batches = conn.execute(
                "SELECT COUNT(*) AS c FROM blob_batches WHERE deleted_at_unix IS NULL"
            ).fetchone()["c"]

            return {
                "blobs": blobs,
                "batches": batches,
            }
        finally:
            conn.close()

    def prune(self, max_age_hours: int = 48) -> int:
        """
        Delete ledger rows older than max_age_hours (SSE Spec Section 4.3).

        Uses batched DELETE to avoid locking the table.
        Returns number of rows deleted.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

        conn = sqlite3.connect(self.db_path)
        total_deleted = 0
        try:
            while True:
                cursor = conn.execute(
                    """
                    DELETE FROM event_ledger
                    WHERE sequence IN (
                        SELECT sequence FROM event_ledger
                        WHERE timestamp < ?
                        LIMIT 1000
                    )
                    """,
                    (cutoff_iso,),
                )
                conn.commit()
                deleted = cursor.rowcount
                total_deleted += deleted
                if deleted < 1000:
                    break

            if total_deleted > 0:
                # Get oldest remaining for logging
                row = conn.execute(
                    "SELECT MIN(sequence) AS oldest FROM event_ledger"
                ).fetchone()
                oldest = row[0] if row and row[0] is not None else 0
                logger.info(
                    f"Ledger pruned: {total_deleted} rows deleted, "
                    f"oldest_remaining={oldest}"
                )

            return total_deleted
        finally:
            conn.close()


class LedgerPruner:
    """
    Background task that prunes the event ledger every 6 hours.
    """

    def __init__(self, ledger: "EventLedger", interval_hours: int = 6):
        self._ledger = ledger
        self._interval = interval_hours * 3600
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Ledger pruner started (interval={self._interval // 3600}h, "
            f"retention=48h)"
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Ledger pruner stopped")

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                self._ledger.prune()
                self._update_ledger_size_metric()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ledger pruner error: {e}")
                await asyncio.sleep(60)

    def _update_ledger_size_metric(self):
        """Set the helium_sse_ledger_size gauge after pruning."""
        try:
            from ..observability.metrics import SSE_LEDGER_SIZE
            conn = sqlite3.connect(self._ledger.db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM event_ledger"
                ).fetchone()
                SSE_LEDGER_SIZE.labels(service="heartbeat").set(row[0] if row else 0)
            finally:
                conn.close()
        except Exception:
            pass


# ── Singleton ──────────────────────────────────────────────────────────

_ledger_instance: Optional[EventLedger] = None
_pruner_instance: Optional[LedgerPruner] = None


def get_event_ledger(db_path: Optional[str] = None) -> EventLedger:
    """Get singleton EventLedger (creates on first call)."""
    global _ledger_instance
    if _ledger_instance is None:
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "databases",
                "blob.db",
            )
        _ledger_instance = EventLedger(db_path)
    return _ledger_instance


def get_ledger_pruner() -> LedgerPruner:
    """Get singleton LedgerPruner (creates on first call)."""
    global _pruner_instance
    if _pruner_instance is None:
        _pruner_instance = LedgerPruner(get_event_ledger())
    return _pruner_instance


def reset_event_ledger() -> None:
    """Reset singletons (for testing/shutdown)."""
    global _ledger_instance, _pruner_instance
    _ledger_instance = None
    _pruner_instance = None
