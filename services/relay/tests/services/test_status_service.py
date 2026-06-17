"""
Unit tests for StatusService — Q37 Gap #5.

Direct service tests (no HTTP): exercises query-by-batch, query-by-txn,
query-by-irn, empty results, result derivation logic, Core graceful null,
and HB exception swallowing.
"""

from typing import Any, Dict, List, Optional

import pytest

from src.services.status_service import StatusService, _derive_result, _build_entry


# ── Mock helpers ─────────────────────────────────────────────────────────


class _MockHB:
    def __init__(
        self,
        by_batch: Optional[List[Dict[str, Any]]] = None,
        by_txn: Optional[Dict[str, Any]] = None,
        by_irn: Optional[Dict[str, Any]] = None,
        raise_on: Optional[str] = None,
    ):
        self._by_batch = by_batch if by_batch is not None else []
        self._by_txn = by_txn
        self._by_irn = by_irn
        self._raise_on = raise_on
        self.calls: List[tuple] = []

    async def get_blob_status_by_batch(self, batch_id, tenant_id):
        self.calls.append(("by_batch", batch_id))
        if self._raise_on == "batch":
            raise RuntimeError("HB unreachable")
        return self._by_batch

    async def get_blob_status_by_transaction_id(self, transaction_id, tenant_id):
        self.calls.append(("by_txn", transaction_id))
        if self._raise_on == "txn":
            raise RuntimeError("HB unreachable")
        return self._by_txn

    async def get_blob_status_by_irn(self, irn, tenant_id):
        self.calls.append(("by_irn", irn))
        if self._raise_on == "irn":
            raise RuntimeError("HB unreachable")
        return self._by_irn


class _MockCore:
    def __init__(
        self,
        result: Optional[Dict[str, Any]] = None,
        raise_exc: bool = False,
    ):
        self._result = result
        self._raise = raise_exc
        self.calls: List[tuple] = []

    async def get_invoice_status(self, transaction_id, irn, tenant_id):
        self.calls.append(("get_invoice_status", transaction_id, irn))
        if self._raise:
            raise RuntimeError("Core unreachable")
        return self._result


def _rec(**kwargs) -> Dict[str, Any]:
    defaults = {
        "transaction_id": "TXN001",
        "irn": None,
        "batch_id": "BATCH001",
        "status": "pending",
        "is_duplicate": False,
        "received_at": "2026-06-17T09:30:00Z",
        "processed_at": None,
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture
def svc():
    return StatusService(_MockHB(), _MockCore())


# ── _derive_result unit tests ─────────────────────────────────────────────


class TestDeriveResult:
    def test_error_status_gives_failed(self):
        assert _derive_result(_rec(status="error"), None) == "failed"

    def test_failed_status_gives_failed(self):
        assert _derive_result(_rec(status="failed"), None) == "failed"

    def test_core_error_workflow_gives_failed(self):
        assert _derive_result(_rec(status="pending"), {"workflow_status": "ERROR"}) == "failed"

    def test_is_duplicate_gives_duplicate(self):
        assert _derive_result(_rec(is_duplicate=True, status="pending"), None) == "duplicate"

    def test_duplicate_overrides_processed_irn(self):
        # If somehow HB marks is_duplicate=True AND irn is present, failed/dup
        # still wins over processed (priority order: failed > duplicate > processed)
        assert _derive_result(_rec(is_duplicate=True, irn="IRN"), None) == "duplicate"

    def test_irn_present_gives_processed(self):
        assert _derive_result(_rec(irn="IRN-001"), None) == "processed"

    def test_hb_processed_status_gives_processed(self):
        assert _derive_result(_rec(status="processed"), None) == "processed"

    def test_hb_finalized_status_gives_processed(self):
        assert _derive_result(_rec(status="finalized"), None) == "processed"

    def test_pending_when_no_irn_no_error(self):
        assert _derive_result(_rec(status="pending", irn=None), None) == "pending"

    def test_core_irn_gives_processed(self):
        assert _derive_result(_rec(irn=None, status="pending"), {"irn": "CORE-IRN"}) == "processed"


# ── _build_entry unit tests ───────────────────────────────────────────────


class TestBuildEntry:
    def test_basic_entry_fields(self):
        hb = _rec(
            transaction_id="TXN-A",
            irn="IRN-A",
            batch_id="BATCH-A",
            status="processed",
            received_at="2026-06-17T10:00:00Z",
        )
        entry = _build_entry(hb, None)
        assert entry.transaction_id == "TXN-A"
        assert entry.irn == "IRN-A"
        assert entry.batch_id == "BATCH-A"
        assert entry.result == "processed"
        assert entry.received_at == "2026-06-17T10:00:00Z"
        assert entry.firs_status is None
        assert entry.invoice_number is None

    def test_core_fields_merged(self):
        hb = _rec(irn=None, status="pending")
        core = {
            "irn": "CORE-IRN-001",
            "firs_status": "APPROVED",
            "invoice_number": "INV-2026-001",
            "processed_at": "2026-06-17T10:05:00Z",
        }
        entry = _build_entry(hb, core)
        assert entry.irn == "CORE-IRN-001"
        assert entry.firs_status == "APPROVED"
        assert entry.invoice_number == "INV-2026-001"
        assert entry.processed_at == "2026-06-17T10:05:00Z"

    def test_hb_irn_preferred_if_core_has_none(self):
        hb = _rec(irn="HB-IRN", status="processed")
        core = {"irn": None, "firs_status": None}
        entry = _build_entry(hb, core)
        assert entry.irn == "HB-IRN"

    def test_core_irn_overrides_none_in_hb(self):
        hb = _rec(irn=None, status="pending")
        core = {"irn": "CORE-IRN"}
        entry = _build_entry(hb, core)
        assert entry.irn == "CORE-IRN"


# ── StatusService.query tests ─────────────────────────────────────────────


class TestStatusServiceQuery:

    # ── batch_id path ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_query_by_batch_id_returns_all_records(self):
        records = [
            _rec(transaction_id="TXN001", batch_id="B1"),
            _rec(transaction_id="TXN002", batch_id="B1"),
            _rec(transaction_id="TXN003", batch_id="B1"),
        ]
        svc = StatusService(_MockHB(by_batch=records), _MockCore())
        resp = await svc.query(
            batch_id="B1", transaction_id=None, irn=None, tenant_id="t1"
        )
        assert len(resp.results) == 3

    @pytest.mark.asyncio
    async def test_query_by_batch_id_calls_hb_by_batch(self):
        hb = _MockHB(by_batch=[_rec()])
        svc = StatusService(hb, _MockCore())
        await svc.query(batch_id="MYBATCH", transaction_id=None, irn=None, tenant_id="t1")
        assert any(c[0] == "by_batch" and c[1] == "MYBATCH" for c in hb.calls)

    # ── transaction_id path ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_query_by_transaction_id_returns_one_entry(self):
        record = _rec(transaction_id="TXN-X", irn="IRN-X", status="processed")
        svc = StatusService(_MockHB(by_txn=record), _MockCore())
        resp = await svc.query(
            transaction_id="TXN-X", irn=None, batch_id=None, tenant_id="t1"
        )
        assert len(resp.results) == 1
        assert resp.results[0].transaction_id == "TXN-X"

    @pytest.mark.asyncio
    async def test_query_by_transaction_id_unknown_returns_empty(self):
        svc = StatusService(_MockHB(by_txn=None), _MockCore())
        resp = await svc.query(
            transaction_id="UNKNOWN", irn=None, batch_id=None, tenant_id="t1"
        )
        assert resp.results == []

    # ── irn path ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_query_by_irn_returns_one_entry(self):
        record = _rec(irn="IRN-Y", status="processed")
        svc = StatusService(_MockHB(by_irn=record), _MockCore())
        resp = await svc.query(
            irn="IRN-Y", transaction_id=None, batch_id=None, tenant_id="t1"
        )
        assert len(resp.results) == 1
        assert resp.results[0].irn == "IRN-Y"

    @pytest.mark.asyncio
    async def test_query_by_irn_unknown_returns_empty(self):
        svc = StatusService(_MockHB(by_irn=None), _MockCore())
        resp = await svc.query(
            irn="NO-SUCH-IRN", transaction_id=None, batch_id=None, tenant_id="t1"
        )
        assert resp.results == []

    # ── Core null stub (graceful) ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_core_stub_returns_none_firs_status_is_null(self):
        record = _rec(transaction_id="TXN-C", status="processed")
        svc = StatusService(_MockHB(by_txn=record), _MockCore(result=None))
        resp = await svc.query(
            transaction_id="TXN-C", irn=None, batch_id=None, tenant_id="t1"
        )
        assert resp.results[0].firs_status is None
        assert resp.results[0].invoice_number is None

    @pytest.mark.asyncio
    async def test_core_exception_is_swallowed_gracefully(self):
        """Core throwing an exception must not surface as a 5xx."""
        record = _rec(transaction_id="TXN-D", status="processed")
        svc = StatusService(_MockHB(by_txn=record), _MockCore(raise_exc=True))
        # Must not raise
        resp = await svc.query(
            transaction_id="TXN-D", irn=None, batch_id=None, tenant_id="t1"
        )
        # Core failure → firs_status null, but result still derived from HB data
        assert resp.results[0].firs_status is None
        assert resp.results[0].result == "processed"

    # ── HB exception → graceful empty ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_hb_exception_returns_empty_results(self):
        """HB exception → empty results, never 5xx."""
        svc = StatusService(_MockHB(raise_on="batch"), _MockCore())
        resp = await svc.query(
            batch_id="BATCH", transaction_id=None, irn=None, tenant_id="t1"
        )
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_hb_txn_exception_returns_empty_results(self):
        svc = StatusService(_MockHB(raise_on="txn"), _MockCore())
        resp = await svc.query(
            transaction_id="TXN", irn=None, batch_id=None, tenant_id="t1"
        )
        assert resp.results == []

    # ── Result derivation end-to-end ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_batch_with_mixed_statuses(self):
        records = [
            _rec(transaction_id="T1", status="error"),
            _rec(transaction_id="T2", is_duplicate=True),
            _rec(transaction_id="T3", irn="IRN-3"),
            _rec(transaction_id="T4", status="pending"),
        ]
        svc = StatusService(_MockHB(by_batch=records), _MockCore())
        resp = await svc.query(
            batch_id="MIXED", transaction_id=None, irn=None, tenant_id="t1"
        )
        results_map = {e.transaction_id: e.result for e in resp.results}
        assert results_map["T1"] == "failed"
        assert results_map["T2"] == "duplicate"
        assert results_map["T3"] == "processed"
        assert results_map["T4"] == "pending"

    @pytest.mark.asyncio
    async def test_core_data_enriches_each_entry_in_batch(self):
        """Core is called once per HB record in a batch."""
        records = [
            _rec(transaction_id="T1"),
            _rec(transaction_id="T2"),
        ]
        core = _MockCore(result={"firs_status": "SUBMITTED", "invoice_number": "INV-001"})
        svc = StatusService(_MockHB(by_batch=records), core)
        resp = await svc.query(
            batch_id="BATCH", transaction_id=None, irn=None, tenant_id="t1"
        )
        assert len(core.calls) == 2
        for entry in resp.results:
            assert entry.firs_status == "SUBMITTED"
            assert entry.invoice_number == "INV-001"
