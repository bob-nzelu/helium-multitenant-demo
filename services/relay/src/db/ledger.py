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
from datetime import datetime, timezone
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

    def __init__(self, db_path: str):
        if not db_path:
            # Guarded construction is the caller's job (ledger disabled when
            # RELAY_DB_PATH is unset). Constructing with an empty path is a bug.
            raise ValueError("IngestLedger requires a non-empty db_path")
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit — each statement is its own txn
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._create_schema()
        logger.info("ingest_ledger ready — %s", db_path)

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
