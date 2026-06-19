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
