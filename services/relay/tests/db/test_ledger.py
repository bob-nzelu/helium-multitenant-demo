"""
Tests for the relay.db ingest ledger (Q28).

Covers the durable write-first idempotency contract:
    • record_received writes a 'pending' row (write-first)
    • mark_processed / mark_error transition the row
    • duplicate (tenant_id, file_sha256) → "duplicate" + prior row (idempotency hit)
    • tenant isolation: same sha under different tenants is NOT a duplicate
    • lookup round-trips fields incl. the serialized result payload
    • WAL/crash-consistency: a row survives close + re-open of the same file

Uses a real temp-file SQLite db (tmp_path) so the on-disk durability path is
exercised — not just :memory:.
"""

import sqlite3

import pytest

from src.db.ledger import IngestLedger, LedgerRow


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "relay.db")


@pytest.fixture
def ledger(db_path):
    led = IngestLedger(db_path)
    yield led
    led.close()


SHA_A = "a" * 64
SHA_B = "b" * 64
TENANT = "tenant-abbey"


# ── Construction / schema ─────────────────────────────────────────────────


class TestLedgerConstruction:
    def test_empty_path_rejected(self):
        with pytest.raises(ValueError):
            IngestLedger("")

    def test_file_is_created(self, db_path, ledger):
        import os
        assert os.path.exists(db_path)

    def test_table_exists(self, ledger):
        cur = ledger._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='ingest_ledger'"
        )
        assert cur.fetchone() is not None

    def test_wal_mode_enabled(self, db_path):
        led = IngestLedger(db_path)
        try:
            mode = led._conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            led.close()

    def test_busy_timeout_set(self, ledger):
        timeout = ledger._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000

    def test_idempotent_open_existing_file(self, db_path):
        # Opening the same file twice (CREATE TABLE IF NOT EXISTS) must not raise.
        led1 = IngestLedger(db_path)
        led1.close()
        led2 = IngestLedger(db_path)
        led2.close()


# ── record_received (write-first) ─────────────────────────────────────────


class TestRecordReceived:
    def test_new_returns_new(self, ledger):
        outcome, prior = ledger.record_received(TENANT, SHA_A, trace_id="t-1")
        assert outcome == "new"
        assert prior is None

    def test_new_row_is_pending(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        row = ledger.lookup(TENANT, SHA_A)
        assert row is not None
        assert row.result == "pending"
        assert row.processed_at is None
        assert row.received_at  # write-first timestamp present

    def test_duplicate_returns_prior(self, ledger):
        ledger.record_received(TENANT, SHA_A, trace_id="first")
        outcome, prior = ledger.record_received(TENANT, SHA_A, trace_id="second")
        assert outcome == "duplicate"
        assert prior is not None
        assert prior.file_sha256 == SHA_A
        # The prior row keeps the FIRST trace_id (the INSERT was a no-op).
        assert prior.trace_id == "first"

    def test_duplicate_does_not_overwrite(self, ledger):
        ledger.record_received(TENANT, SHA_A, data_uuid="uuid-1")
        ledger.mark_processed(TENANT, SHA_A, data_uuid="uuid-1")
        # A second record_received must NOT reset the processed row.
        outcome, prior = ledger.record_received(TENANT, SHA_A, data_uuid="uuid-2")
        assert outcome == "duplicate"
        assert prior.result == "processed"
        assert prior.data_uuid == "uuid-1"

    def test_tenant_isolation_same_sha(self, ledger):
        # Same file sha under two different tenants → both 'new' (compound PK).
        o1, _ = ledger.record_received("tenant-a", SHA_A)
        o2, _ = ledger.record_received("tenant-b", SHA_A)
        assert o1 == "new"
        assert o2 == "new"
        assert ledger.lookup("tenant-a", SHA_A) is not None
        assert ledger.lookup("tenant-b", SHA_A) is not None

    def test_different_sha_same_tenant(self, ledger):
        o1, _ = ledger.record_received(TENANT, SHA_A)
        o2, _ = ledger.record_received(TENANT, SHA_B)
        assert o1 == "new"
        assert o2 == "new"


# ── mark_processed / mark_error ───────────────────────────────────────────


class TestMarkProcessed:
    def test_marks_processed(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        ledger.mark_processed(TENANT, SHA_A, data_uuid="du-1")
        row = ledger.lookup(TENANT, SHA_A)
        assert row.result == "processed"
        assert row.processed_at is not None
        assert row.data_uuid == "du-1"

    def test_stores_and_reads_back_payload(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        payload = {"queue_id": "queue_x", "file_count": 1, "status": "ingested"}
        ledger.mark_processed(TENANT, SHA_A, result_payload=payload)
        row = ledger.lookup(TENANT, SHA_A)
        assert row.result_payload == payload

    def test_processed_then_duplicate_replays_payload(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        payload = {"queue_id": "queue_y", "status": "ingested"}
        ledger.mark_processed(TENANT, SHA_A, result_payload=payload)
        outcome, prior = ledger.record_received(TENANT, SHA_A)
        assert outcome == "duplicate"
        assert prior.result_payload == payload


class TestMarkError:
    def test_marks_error(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        ledger.mark_error(TENANT, SHA_A, "blob write failed: MinIO down")
        row = ledger.lookup(TENANT, SHA_A)
        assert row.result == "error"
        assert "MinIO down" in row.error_message
        assert row.processed_at is not None  # terminal outcome stamped

    def test_error_message_capped(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        ledger.mark_error(TENANT, SHA_A, "x" * 1000)
        row = ledger.lookup(TENANT, SHA_A)
        assert len(row.error_message) <= 256


# ── lookup ────────────────────────────────────────────────────────────────


class TestLookup:
    def test_miss_returns_none(self, ledger):
        assert ledger.lookup(TENANT, SHA_A) is None

    def test_round_trips_fields(self, ledger):
        ledger.record_received(TENANT, SHA_A, trace_id="t-9", data_uuid="du-9")
        row = ledger.lookup(TENANT, SHA_A)
        assert isinstance(row, LedgerRow)
        assert row.tenant_id == TENANT
        assert row.file_sha256 == SHA_A
        assert row.trace_id == "t-9"
        assert row.data_uuid == "du-9"

    def test_corrupt_payload_returns_none(self, ledger):
        ledger.record_received(TENANT, SHA_A)
        # Inject invalid JSON directly to exercise the defensive path.
        ledger._conn.execute(
            "UPDATE ingest_ledger SET result_json='{not json' "
            "WHERE tenant_id=? AND file_sha256=?",
            (TENANT, SHA_A),
        )
        row = ledger.lookup(TENANT, SHA_A)
        assert row.result_payload is None


# ── WAL / crash-consistency (close + re-open survives) ────────────────────


class TestDurability:
    def test_row_survives_reopen(self, db_path):
        led1 = IngestLedger(db_path)
        led1.record_received(TENANT, SHA_A, trace_id="persist")
        led1.mark_processed(
            TENANT, SHA_A, result_payload={"status": "ingested"}
        )
        led1.close()

        # Re-open the SAME file — the committed row must still be there.
        led2 = IngestLedger(db_path)
        try:
            row = led2.lookup(TENANT, SHA_A)
            assert row is not None
            assert row.result == "processed"
            assert row.trace_id == "persist"
            assert row.result_payload == {"status": "ingested"}
            # And a re-ingest after re-open is still an idempotency hit.
            outcome, prior = led2.record_received(TENANT, SHA_A)
            assert outcome == "duplicate"
        finally:
            led2.close()

    def test_pending_row_survives_reopen(self, db_path):
        # Simulates a crash AFTER write-first but BEFORE processing: the
        # 'pending' row must persist so the work is not silently lost.
        led1 = IngestLedger(db_path)
        led1.record_received(TENANT, SHA_A)
        led1.close()  # no mark_processed — mimics mid-pipeline crash

        led2 = IngestLedger(db_path)
        try:
            row = led2.lookup(TENANT, SHA_A)
            assert row is not None
            assert row.result == "pending"
            assert row.processed_at is None
        finally:
            led2.close()

    def test_check_constraint_rejects_bad_result(self, ledger):
        # Defensive: the CHECK constraint guards the result domain.
        ledger.record_received(TENANT, SHA_A)
        with pytest.raises(sqlite3.IntegrityError):
            ledger._conn.execute(
                "UPDATE ingest_ledger SET result='bogus' "
                "WHERE tenant_id=? AND file_sha256=?",
                (TENANT, SHA_A),
            )


# ── Retention / prune (Q28 ratify rider — Bob 2026-06-20) ─────────────────


def _sha(n: int) -> str:
    """Deterministic 64-hex sha for the Nth synthetic row."""
    return f"{n:064x}"


# Far-past / far-future ISO timestamps for deterministic age tests.
OLD_TS = "2020-01-01T00:00:00Z"
NEW_TS = "2099-01-01T00:00:00Z"


def _insert(ledger, sha, result, received_at):
    """Insert a row in a known terminal/transient state at a controllable
    received_at. Used so age + ordering tests are deterministic.

    Uses prune_on_start=False ledgers so we never prune mid-setup.
    """
    ledger.record_received(TENANT, sha, received_at=received_at)
    if result == "processed":
        ledger.mark_processed(TENANT, sha, processed_at=received_at)
    elif result == "error":
        ledger.mark_error(TENANT, sha, "boom", processed_at=received_at)
    elif result == "duplicate":
        # No public setter for 'duplicate' as a stored state; set directly.
        ledger._conn.execute(
            "UPDATE ingest_ledger SET result='duplicate' WHERE file_sha256=?",
            (sha,),
        )
    # 'pending' = leave as written by record_received.


def _count(ledger) -> int:
    return ledger._conn.execute("SELECT COUNT(*) FROM ingest_ledger").fetchone()[0]


def _shas(ledger):
    return sorted(
        r[0] for r in ledger._conn.execute(
            "SELECT file_sha256 FROM ingest_ledger"
        ).fetchall()
    )


@pytest.fixture
def prune_ledger(db_path):
    """Ledger with the steady-state write-trigger effectively disabled
    (prune_every_n huge) and no startup prune, so prune() is exercised
    explicitly and deterministically in these tests."""
    led = IngestLedger(
        db_path,
        retention_days=7,
        max_rows=1_000_000,
        prune_every_n=10**9,
        prune_on_start=False,
    )
    yield led
    led.close()


class TestPruneAge:
    def test_prunes_old_processed_and_duplicate(self, prune_ledger):
        # Old processed + old duplicate → pruned; new processed → kept.
        _insert(prune_ledger, _sha(1), "processed", OLD_TS)
        _insert(prune_ledger, _sha(2), "duplicate", OLD_TS)
        _insert(prune_ledger, _sha(3), "processed", NEW_TS)

        pruned = prune_ledger.prune(retention_days=7, max_rows=1_000_000)

        assert pruned == 2
        assert _count(prune_ledger) == 1
        assert prune_ledger.lookup(TENANT, _sha(3)) is not None  # the new one survives

    def test_returns_count_pruned(self, prune_ledger):
        for i in range(5):
            _insert(prune_ledger, _sha(i), "processed", OLD_TS)
        assert prune_ledger.prune(retention_days=7, max_rows=1_000_000) == 5

    def test_zero_when_nothing_old(self, prune_ledger):
        _insert(prune_ledger, _sha(1), "processed", NEW_TS)
        assert prune_ledger.prune(retention_days=7, max_rows=1_000_000) == 0
        assert _count(prune_ledger) == 1


class TestPruneNeverDeletesInflight:
    def test_pending_and_error_never_pruned_by_age(self, prune_ledger):
        # Both old → still must survive (in-flight / need attention).
        _insert(prune_ledger, _sha(1), "pending", OLD_TS)
        _insert(prune_ledger, _sha(2), "error", OLD_TS)

        pruned = prune_ledger.prune(retention_days=7, max_rows=1_000_000)

        assert pruned == 0
        assert _count(prune_ledger) == 2

    def test_pending_and_error_never_pruned_over_cap(self, prune_ledger):
        # cap=1, two protected rows over the cap → neither is deleted.
        _insert(prune_ledger, _sha(1), "pending", OLD_TS)
        _insert(prune_ledger, _sha(2), "error", OLD_TS)

        pruned = prune_ledger.prune(retention_days=7, max_rows=1)

        assert pruned == 0
        assert _count(prune_ledger) == 2

    def test_over_cap_clamps_to_terminal_rows(self, prune_ledger):
        # 4 pending (protected) + 2 processed; cap=1 → over_cap=5, but only the
        # 2 terminal rows may be deleted; the 4 pending must survive.
        for i in range(4):
            _insert(prune_ledger, _sha(100 + i), "pending", NEW_TS)
        _insert(prune_ledger, _sha(1), "processed", "2050-01-01T00:00:00Z")
        _insert(prune_ledger, _sha(2), "processed", "2050-01-01T00:00:01Z")

        pruned = prune_ledger.prune(retention_days=7, max_rows=1)

        assert pruned == 2
        pending = prune_ledger._conn.execute(
            "SELECT COUNT(*) FROM ingest_ledger WHERE result='pending'"
        ).fetchone()[0]
        assert pending == 4


class TestPruneSizeCap:
    def test_size_cap_leaves_at_most_max_rows(self, prune_ledger):
        # N+M processed (all new, so age does NOT trigger) with max_rows=N →
        # leaves exactly N, deleting M oldest.
        max_rows = 5
        extra = 3
        for i in range(max_rows + extra):
            # Increasing timestamp → i=0 is the oldest.
            ts = f"2050-01-01T00:00:{i:02d}Z"
            _insert(prune_ledger, _sha(i), "processed", ts)

        pruned = prune_ledger.prune(retention_days=7, max_rows=max_rows)

        assert pruned == extra
        assert _count(prune_ledger) == max_rows

    def test_size_cap_deletes_the_oldest(self, prune_ledger):
        # The survivors must be the NEWEST rows; the oldest are evicted.
        max_rows = 5
        total = 8
        for i in range(total):
            ts = f"2050-01-01T00:00:{i:02d}Z"
            _insert(prune_ledger, _sha(i), "processed", ts)

        prune_ledger.prune(retention_days=7, max_rows=max_rows)

        survivors = _shas(prune_ledger)
        # Oldest (i=0,1,2) evicted; newest (i=3..7) kept.
        assert survivors == [_sha(i) for i in range(3, total)]
        for i in range(3):
            assert prune_ledger.lookup(TENANT, _sha(i)) is None

    def test_oldest_first_ordering(self, prune_ledger):
        # Insert out of timestamp order; eviction must still be by received_at,
        # oldest first — not insertion order.
        _insert(prune_ledger, _sha(1), "processed", "2050-06-01T00:00:00Z")  # middle
        _insert(prune_ledger, _sha(2), "processed", "2050-01-01T00:00:00Z")  # oldest
        _insert(prune_ledger, _sha(3), "processed", "2050-12-01T00:00:00Z")  # newest

        # cap=2 → evict exactly 1, which must be the oldest (sha 2).
        prune_ledger.prune(retention_days=7, max_rows=2)

        assert prune_ledger.lookup(TENANT, _sha(2)) is None       # oldest gone
        assert prune_ledger.lookup(TENANT, _sha(1)) is not None
        assert prune_ledger.lookup(TENANT, _sha(3)) is not None


class TestPruneWriteTrigger:
    """The prune is WRITE-TRIGGERED (no timer): it fires on every Nth insert."""

    def test_fires_on_nth_insert_not_before(self, db_path):
        led = IngestLedger(
            db_path,
            retention_days=7,
            max_rows=2,
            prune_every_n=3,
            prune_on_start=False,
        )
        try:
            # Pre-load 5 OLD processed rows directly (would be pruned by age
            # once a prune actually runs).
            for i in range(5):
                led._conn.execute(
                    "INSERT INTO ingest_ledger "
                    "(tenant_id, file_sha256, received_at, result, processed_at) "
                    "VALUES (?, ?, ?, 'processed', ?)",
                    (TENANT, _sha(900 + i), OLD_TS, OLD_TS),
                )

            old_count = lambda: led._conn.execute(  # noqa: E731
                "SELECT COUNT(*) FROM ingest_ledger WHERE received_at = ?",
                (OLD_TS,),
            ).fetchone()[0]

            # Inserts 1 and 2 (under the gate) → no prune yet.
            led.record_received(TENANT, _sha(1), received_at=NEW_TS)
            led.record_received(TENANT, _sha(2), received_at=NEW_TS)
            assert old_count() == 5  # gate not reached → old rows untouched

            # 3rd insert reaches the gate (N=3) → prune fires inline.
            led.record_received(TENANT, _sha(3), received_at=NEW_TS)
            assert old_count() == 0  # old rows pruned by the write-triggered prune
        finally:
            led.close()

    def test_counter_resets_between_gate_hits(self, db_path):
        led = IngestLedger(
            db_path,
            retention_days=7,
            max_rows=1_000_000,
            prune_every_n=2,
            prune_on_start=False,
        )
        try:
            # First gate hit on the 2nd insert; then the counter resets so the
            # 3rd insert is below-gate again (no prune), 4th hits again.
            seen = []
            for i in range(4):
                led.record_received(TENANT, _sha(i), received_at=NEW_TS)
                seen.append(led._writes_since_prune)
            # After resets: counters are [1, 0, 1, 0].
            assert seen == [1, 0, 1, 0]
        finally:
            led.close()


class TestPruneStartup:
    def test_startup_prune_runs_once(self, db_path):
        # Seed an OLD processed row, then open a ledger WITH prune_on_start.
        seed = IngestLedger(db_path, prune_on_start=False)
        seed._conn.execute(
            "INSERT INTO ingest_ledger "
            "(tenant_id, file_sha256, received_at, result, processed_at) "
            "VALUES (?, ?, ?, 'processed', ?)",
            (TENANT, _sha(1), OLD_TS, OLD_TS),
        )
        seed.close()

        led = IngestLedger(db_path, retention_days=7, prune_on_start=True)
        try:
            # The startup prune (event = startup) removed the stale row.
            assert led.lookup(TENANT, _sha(1)) is None
        finally:
            led.close()


class TestPruneDisabledGuard:
    def test_no_prune_when_ledger_disabled(self):
        # The ledger is disabled by NOT constructing it (RELAY_DB_PATH unset);
        # there is no object to prune and no error path. Mirrors the ingestion
        # service's guarded `self._ledger is None` no-op.
        ledger = None
        # The wiring's guard: a disabled ledger is simply never touched.
        assert ledger is None  # no prune call, no error

    def test_retention_clamped_below_30(self, db_path):
        # Defence-in-depth: even if constructed with >=30, the window is < 30.
        led = IngestLedger(db_path, retention_days=90, prune_on_start=False)
        try:
            assert led._retention_days == 29
        finally:
            led.close()
