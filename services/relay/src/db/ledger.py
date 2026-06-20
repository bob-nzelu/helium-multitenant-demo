"""
relay.db — durable ingest ledger (write-first idempotency).

The Frontdoor's local SQLite store. One file per tenant container; the single
intentional exception to the all-PG mantra (FRONTDOOR_ARCHITECTURE.md §2). This
module is the SIMPLIFIED Phase-2-precursor build per Q28 / ARCH APPROVE-DIRECTION
(2026-06-19): the durable ``ingest_ledger`` only — NO AMQP queue, NO consumer.
Those stay Phase 2 (Frontdoor §3.6 / §10).

What it gives us, on the *current* synchronous ``/api/ingest`` path:

  • Crash-survival — a row written write-first (BEFORE the blob commit) records
    that we received a unit of work, so a crash mid-pipeline leaves a durable trace.
  • Idempotency — the PRIMARY KEY ``(tenant_id, file_sha256)`` is the idempotency
    key (Frontdoor §3.2 step 4: the per-record hash is "the durable idempotency
    key"). A second ingest of the same bytes for the same tenant short-circuits to
    the prior result instead of re-running the pipeline.

Durability knobs (Frontdoor §6.3): WAL journal, ``synchronous=NORMAL``,
``busy_timeout=5000``. WAL + NORMAL is the recommended durability/perf tradeoff
and survives process crash; only a host power-loss can drop the last few writes.

Additive + reversible: a brand-new SQLite file, no data to migrate, rollback =
delete the file (or leave ``RELAY_DB_PATH`` unset → ledger disabled, zero
behaviour change).

> SCHEMA SENSITIVITY: the ``CREATE TABLE ingest_ledger`` DDL below is a PROPOSAL
> pending Bob's ratify (it rides the same review as the Frontdoor §6 schema). It
> is the simplified single-table cut of §6, not the full ``relay_messages`` shape.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional, Tuple

logger = logging.getLogger(__name__)


# Allowed terminal/transient states for a ledger row.
LedgerResult = Literal["pending", "processed", "error", "duplicate"]

RecordOutcome = Literal["new", "duplicate"]


# ── DDL (PROPOSAL — pending Bob ratify) ────────────────────────────────────
#
# Simplified single-table cut of FRONTDOOR_ARCHITECTURE.md §6 (which proposes
# the fuller ``relay_messages``). This is the Q28 ``ingest_ledger``: just the
# durable idempotency / crash-survival ledger, no AMQP/DLQ columns (no queue in
# this build). Compound PK = the idempotency key.
INGEST_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS ingest_ledger (
    -- Idempotency key (Frontdoor §3.2 step 4: the durable key)
    tenant_id       TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,

    -- Identity / receive metadata
    data_uuid       TEXT,                       -- Relay per-request group id (NULL until known)
    trace_id        TEXT,                        -- request trace id, for cross-referencing logs
    received_at     TEXT NOT NULL,               -- ISO-8601 UTC; set write-first, before blob commit

    -- Processing outcome
    processed_at    TEXT,                        -- ISO-8601 UTC; NULL until pipeline completes
    result          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (result IN ('pending', 'processed', 'error', 'duplicate')),
    error_message   TEXT,                        -- short, human-readable; full trace in audit log

    -- Idempotent-replay payload: the serialized prior IngestResult so a
    -- duplicate hit can return the original result without re-running the
    -- pipeline. Kept small (the result envelope, not the file bytes).
    result_json     TEXT,

    PRIMARY KEY (tenant_id, file_sha256)         -- compound = the idempotency key
);

-- Operational: find unprocessed (in-flight / stuck) rows within a tenant.
CREATE INDEX IF NOT EXISTS idx_ingest_ledger_inflight
    ON ingest_ledger (tenant_id, received_at)
    WHERE processed_at IS NULL;
"""


@dataclass
class LedgerRow:
    """A single ``ingest_ledger`` row (the durable record of one ingest)."""

    tenant_id: str
    file_sha256: str
    received_at: str
    result: LedgerResult
    data_uuid: Optional[str] = None
    trace_id: Optional[str] = None
    processed_at: Optional[str] = None
    error_message: Optional[str] = None
    result_json: Optional[str] = None

    @property
    def result_payload(self) -> Optional[Dict[str, Any]]:
        """Deserialize ``result_json`` into the stored result envelope (or None)."""
        if not self.result_json:
            return None
        try:
            return json.loads(self.result_json)
        except (ValueError, TypeError):  # pragma: no cover - defensive
            logger.warning(
                "ingest_ledger: corrupt result_json for "
                "(tenant=%s, sha=%s...)",
                self.tenant_id,
                self.file_sha256[:12],
            )
            return None

    @classmethod
    def _from_sqlite(cls, row: sqlite3.Row) -> "LedgerRow":
        return cls(
            tenant_id=row["tenant_id"],
            file_sha256=row["file_sha256"],
            received_at=row["received_at"],
            result=row["result"],
            data_uuid=row["data_uuid"],
            trace_id=row["trace_id"],
            processed_at=row["processed_at"],
            error_message=row["error_message"],
            result_json=row["result_json"],
        )


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp, second precision, ``Z`` suffix — matches the
    timestamp shape used across Helium (audit events, HMAC s2s)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class IngestLedger:
    """
    Durable, crash-consistent ingest ledger backed by a per-tenant SQLite file.

    Single-row writes are autocommitted (no long transactions) so a crash leaves
    each row either fully present or absent. The idempotency key is the compound
    PRIMARY KEY ``(tenant_id, file_sha256)``.

    Usage (write-first ordering, Frontdoor §3.2):

        ledger = IngestLedger(config.relay_db_path)
        outcome, prior = ledger.record_received(tenant_id, sha, trace_id=tid)
        if outcome == "duplicate":
            return prior.result_payload          # idempotent replay; do NOT re-process
        try:
            ... run pipeline ...
            ledger.mark_processed(tenant_id, sha, data_uuid=du, result_payload=...)
        except Exception as e:
            ledger.mark_error(tenant_id, sha, str(e))
            raise

    Thread-safety: a single shared connection is guarded by an internal lock.
    SQLite autocommit + WAL handles the durability; the lock serializes Python
    access to the one connection object (``check_same_thread=False``).
    """

    def __init__(
        self,
        db_path: str,
        *,
        retention_days: int = 7,
        max_rows: int = 500_000,
        prune_every_n: int = 100,
        prune_on_start: bool = True,
    ):
        """
        Args:
            db_path: SQLite file path (``:memory:`` for tests).
            retention_days: AGE predicate — terminal rows older than this are
                pruned. Bob's ruling: SHORTER than 30 days (default ~7d). Values
                >=30 are clamped to 29 (defence-in-depth; config also clamps).
            max_rows: SIZE/row cap — when the table exceeds this, the oldest
                terminal rows are pruned down to the cap.
            prune_every_n: write-trigger gate. The opportunistic prune runs only
                on every Nth ``record_received`` insert (cheap in-memory counter),
                NOT on a timer. Set <=1 to prune on every insert.
            prune_on_start: run a single one-shot prune at construction (the
                "event" is startup). Steady-state pruning is the write path.
        """
        if not db_path:
            # Guarded construction is the caller's job (ledger disabled when
            # RELAY_DB_PATH is unset). Constructing with an empty path is a bug.
            raise ValueError("IngestLedger requires a non-empty db_path")
        self._db_path = db_path
        self._lock = threading.Lock()
        # ── Retention / prune knobs (Q28 ratify rider — Bob 2026-06-20) ──
        # Defence-in-depth clamp: retention window MUST be < 30 days regardless
        # of how this is constructed (config also clamps on the way in).
        self._retention_days = max(1, min(int(retention_days), 29))
        self._max_rows = max(1, int(max_rows))
        self._prune_every_n = max(1, int(prune_every_n))
        # In-memory write counter — the WRITE-TRIGGER gate (no timer/poller).
        # Reset to 0 each time the gate fires. Guarded by the same lock as the
        # connection (incremented inside record_received's locked section).
        self._writes_since_prune = 0
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit — each statement is its own txn
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._create_schema()
        logger.info(
            "ingest_ledger ready — %s (retention=%dd, max_rows=%d, prune_every_n=%d)",
            db_path,
            self._retention_days,
            self._max_rows,
            self._prune_every_n,
        )
        # One-shot startup prune (event = startup). Best-effort: a prune failure
        # must never block the ledger from coming up.
        if prune_on_start:
            try:
                pruned = self.prune()
                if pruned:
                    logger.info(
                        "ingest_ledger: startup prune removed %d stale row(s) — %s",
                        pruned,
                        db_path,
                    )
            except sqlite3.Error as e:  # pragma: no cover - defensive
                logger.warning("ingest_ledger: startup prune failed: %s", e)

    # ── Setup ──────────────────────────────────────────────────────────────

    def _configure_pragmas(self) -> None:
        """Durability/perf pragmas per Frontdoor §6.3.

        WAL is skipped only for the in-memory ``:memory:`` db (no on-disk WAL),
        which exists for fast unit tests; the durability claim applies to the
        real file-backed store.
        """
        cur = self._conn.cursor()
        if self._db_path != ":memory:":
            cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.execute("PRAGMA busy_timeout = 5000")
        cur.close()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(INGEST_LEDGER_DDL)

    # ── Writes ─────────────────────────────────────────────────────────────

    def record_received(
        self,
        tenant_id: str,
        file_sha256: str,
        *,
        trace_id: str = "",
        data_uuid: Optional[str] = None,
        received_at: Optional[str] = None,
    ) -> Tuple[RecordOutcome, Optional[LedgerRow]]:
        """
        Write-first: record that a unit of work was received, BEFORE the blob commit.

        Returns:
            ("new", None)               — first time we've seen this (tenant, sha);
                                          the row is now durably ``pending``.
            ("duplicate", prior_row)    — PK conflict: we already have a row for this
                                          (tenant, sha). ``prior_row`` is the existing
                                          ledger record (the idempotency hit). The
                                          caller short-circuits and replays the prior
                                          result rather than re-running the pipeline.

        Crash-consistent: a single autocommitted INSERT.
        """
        now = received_at or _utc_now_iso()
        fire_prune = False
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO ingest_ledger
                        (tenant_id, file_sha256, data_uuid, trace_id,
                         received_at, processed_at, result, error_message,
                         result_json)
                    VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL)
                    """,
                    (tenant_id, file_sha256, data_uuid, trace_id or None, now),
                )
            except sqlite3.IntegrityError:
                # PK conflict → idempotency hit. Fetch and return the prior row.
                prior = self._lookup_locked(tenant_id, file_sha256)
                logger.info(
                    "ingest_ledger: duplicate ingest (tenant=%s, sha=%s...) "
                    "→ idempotent replay",
                    tenant_id,
                    file_sha256[:12],
                )
                return "duplicate", prior
            # ── WRITE-TRIGGER gate (Q28 ratify rider — NO timer/poller) ──
            # A new row landed. Advance the in-memory counter; when it reaches
            # the gate, fire the opportunistic prune (piggybacked on THIS ingest
            # write, never on a clock). Cheap O(1) check on the common path.
            self._writes_since_prune += 1
            if self._writes_since_prune >= self._prune_every_n:
                self._writes_since_prune = 0
                fire_prune = True
        # Run the prune OUTSIDE the insert's lock section (prune() re-acquires
        # the lock itself — avoids re-entrant deadlock). Still event-triggered:
        # it only runs because this insert tripped the gate.
        if fire_prune:
            try:
                pruned = self.prune()
                if pruned:
                    logger.info(
                        "ingest_ledger: write-triggered prune removed %d "
                        "stale row(s) (gate=%d writes)",
                        pruned,
                        self._prune_every_n,
                    )
            except sqlite3.Error as e:  # pragma: no cover - defensive
                # A prune failure must never fail the ingest write that triggered it.
                logger.warning("ingest_ledger: write-triggered prune failed: %s", e)
        return "new", None

    def mark_processed(
        self,
        tenant_id: str,
        file_sha256: str,
        *,
        data_uuid: Optional[str] = None,
        result_payload: Optional[Dict[str, Any]] = None,
        processed_at: Optional[str] = None,
    ) -> None:
        """Mark a row ``processed`` after the pipeline completes successfully.

        Stores ``result_payload`` (the serialized IngestResult envelope) so a
        future duplicate ingest can replay it. Single autocommitted UPDATE.
        """
        now = processed_at or _utc_now_iso()
        payload_json = (
            json.dumps(result_payload, separators=(",", ":"))
            if result_payload is not None
            else None
        )
        with self._lock:
            self._conn.execute(
                """
                UPDATE ingest_ledger
                   SET processed_at = ?,
                       result = 'processed',
                       error_message = NULL,
                       data_uuid = COALESCE(?, data_uuid),
                       result_json = COALESCE(?, result_json)
                 WHERE tenant_id = ? AND file_sha256 = ?
                """,
                (now, data_uuid, payload_json, tenant_id, file_sha256),
            )

    def mark_error(
        self,
        tenant_id: str,
        file_sha256: str,
        error_message: str,
        *,
        processed_at: Optional[str] = None,
    ) -> None:
        """Mark a row ``error`` when the pipeline fails. Single autocommitted UPDATE.

        ``processed_at`` is stamped (the row reached a terminal outcome). The
        error message is kept short; the full trace lives in the audit log.
        """
        now = processed_at or _utc_now_iso()
        # Defensive cap — Frontdoor §6.2 keeps error_message short.
        short = (error_message or "")[:256]
        with self._lock:
            self._conn.execute(
                """
                UPDATE ingest_ledger
                   SET processed_at = ?,
                       result = 'error',
                       error_message = ?
                 WHERE tenant_id = ? AND file_sha256 = ?
                """,
                (now, short, tenant_id, file_sha256),
            )

    # ── Retention / prune (Q28 ratify rider — Bob 2026-06-20) ───────────────

    def prune(
        self,
        retention_days: Optional[int] = None,
        max_rows: Optional[int] = None,
    ) -> int:
        """
        Opportunistic retention prune of the ingest_ledger. Returns rows deleted.

        Bob's ruling (verbatim): "window SHORTER than 30 days (default ~7d) AND
        a hard size/row cap, whichever triggers first; prune oldest
        processed/duplicate first, never pending/error." Safe because HB's
        ``blob_deduplication`` is the authoritative dedup store — this ledger is
        a transient idempotency/crash-survival cache, not the system of record.

        Two predicates, evaluated together each prune ("whichever triggers
        first" = run both):

          • AGE — terminal rows with ``received_at`` older than
            ``now - retention_days``.
          • SIZE — when the table exceeds ``max_rows``, the oldest terminal rows
            beyond the cap (delete ``total_rows - max_rows`` oldest terminal
            rows). Note the cap is measured against the WHOLE table (incl.
            pending/error), but only terminal rows are ever deleted to satisfy
            it — so a table full of in-flight rows is left intact (correctness
            over the size bound).

        ONLY ``result IN ('processed','duplicate')`` is ever deleted. Rows that
        are ``pending`` (in-flight / possible mid-pipeline crash) or ``error``
        (need attention) are NEVER pruned, even if old or over-cap.

        Oldest-first: ordered by ``received_at ASC`` so the eviction is FIFO.

        Crash-safe: a single ``DELETE`` statement (autocommit = its own txn),
        respecting the WAL / busy_timeout already configured. Either the whole
        prune lands or none of it does.
        """
        days = self._retention_days if retention_days is None else int(retention_days)
        cap = self._max_rows if max_rows is None else int(max_rows)
        # Defence-in-depth clamps (same invariants as the constructor/config).
        days = max(1, min(days, 29))
        cap = max(1, cap)

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._lock:
            # SIZE predicate: how many oldest terminal rows to evict to bring the
            # WHOLE table down to the cap. Cheap COUNT(*) on the gated path.
            total = self._conn.execute(
                "SELECT COUNT(*) FROM ingest_ledger"
            ).fetchone()[0]
            over_cap = max(0, total - cap)

            # One crash-safe DELETE. The row's rowid is in the eviction set if
            # it is a terminal ('processed'/'duplicate') row AND either:
            #   (a) it is older than the retention cutoff (AGE), OR
            #   (b) it is among the `over_cap` OLDEST terminal rows (SIZE).
            # pending/error rows are excluded by the result filter in BOTH
            # subqueries, so they can never be deleted.
            cur = self._conn.execute(
                """
                DELETE FROM ingest_ledger
                 WHERE rowid IN (
                     -- (a) AGE: terminal rows older than the retention cutoff
                     SELECT rowid FROM ingest_ledger
                      WHERE result IN ('processed', 'duplicate')
                        AND received_at < ?
                     UNION
                     -- (b) SIZE: the oldest terminal rows beyond the cap.
                     -- Wrapped in a subselect so ORDER BY/LIMIT scope to THIS
                     -- arm (SQLite rejects ORDER BY/LIMIT on a bare UNION arm).
                     SELECT rowid FROM (
                         SELECT rowid FROM ingest_ledger
                          WHERE result IN ('processed', 'duplicate')
                          ORDER BY received_at ASC, rowid ASC
                          LIMIT ?
                     )
                 )
                """,
                (cutoff, over_cap),
            )
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ── Reads ──────────────────────────────────────────────────────────────

    def lookup(self, tenant_id: str, file_sha256: str) -> Optional[LedgerRow]:
        """Return the ledger row for ``(tenant_id, file_sha256)``, or None."""
        with self._lock:
            return self._lookup_locked(tenant_id, file_sha256)

    def _lookup_locked(
        self, tenant_id: str, file_sha256: str
    ) -> Optional[LedgerRow]:
        cur = self._conn.execute(
            """
            SELECT tenant_id, file_sha256, data_uuid, trace_id, received_at,
                   processed_at, result, error_message, result_json
              FROM ingest_ledger
             WHERE tenant_id = ? AND file_sha256 = ?
            """,
            (tenant_id, file_sha256),
        )
        row = cur.fetchone()
        return LedgerRow._from_sqlite(row) if row is not None else None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
