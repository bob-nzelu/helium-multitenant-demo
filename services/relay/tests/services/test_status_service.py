"""
Unit tests for StatusService — L30 / DATA_MODEL_CANONICAL §6.

Direct service tests (no HTTP): query-by-batch / -transaction_id (HB + Core
merge), query-by-invoice_number / -irn (Core-only resolve + HB backfill, since
HB is blind to invoice semantics), result derivation across the reconciled
vocab (pending/processed/not_an_invoice/duplicate/failed), Core graceful null,
and per-backend exception swallowing.
"""

from typing import Any, Dict, List, Optional

import pytest

from src.services.status_service import StatusService, _derive_result, _build_entry


# ── Mock helpers ─────────────────────────────────────────────────────────


class _MockHB:
    """Mocks the HB file_transactions read surface (transaction-level)."""

    def __init__(
        self,
        by_batch: Optional[List[Dict[str, Any]]] = None,
        by_txn: Optional[Dict[str, Any]] = None,
        raise_on: Optional[str] = None,
    ):
        self._by_batch = by_batch if by_batch is not None else []
        self._by_txn = by_txn
        self._raise_on = raise_on
        self.calls: List[tuple] = []

    async def get_transactions_by_batch(self, batch_id, tenant_id):
        self.calls.append(("by_batch", batch_id))
        if self._raise_on == "batch":
            raise RuntimeError("HB unreachable")
        return self._by_batch

    async def get_transaction_by_id(self, transaction_id, tenant_id):
        self.calls.append(("by_txn", transaction_id))
        if self._raise_on == "txn":
            raise RuntimeError("HB unreachable")
        return self._by_txn


class _MockCore:
    """Mocks the Core invoice lookup (invoice-level + sole irn/number resolver)."""

    def __init__(
        self,
        result: Optional[Dict[str, Any]] = None,
        by_batch: Optional[List[Dict[str, Any]]] = None,
        raise_exc: bool = False,
    ):
        self._result = result
        self._by_batch = by_batch if by_batch is not None else []
        self._raise = raise_exc
        self.calls: List[tuple] = []

    async def get_invoice_status(
        self, transaction_id=None, irn=None, invoice_number=None, tenant_id=""
    ):
        self.calls.append(("get_invoice_status", transaction_id, irn, invoice_number))
        if self._raise:
            raise RuntimeError("Core unreachable")
        return self._result

    async def get_invoices_by_batch(self, batch_id, tenant_id=""):
        self.calls.append(("get_invoices_by_batch", batch_id))
        if self._raise:
            raise RuntimeError("Core unreachable")
        return self._by_batch


def _txn(**kwargs) -> Dict[str, Any]:
    """An HB file_transactions row."""
    defaults = {
        "transaction_id": "TXN001",
        "batch_id": "BATCH001",
        "status": "pending",          # pending | acknowledged | not_an_invoice
        "is_duplicate": False,
        "received_at": "2026-06-17T09:30:00Z",
        "updated_at": None,
    }
    defaults.update(kwargs)
    return defaults


def _inv(**kwargs) -> Dict[str, Any]:
    """A Core invoice row."""
    defaults = {
        "external_transaction_id": "TXN001",
        "invoice_number": "INV-2026-001",
        "irn": "IRN-001",
        "workflow_status": "SIGNED",
        "transmission_status": "TRANSMITTED",
        "processed_at": "2026-06-17T10:05:00Z",
    }
    defaults.update(kwargs)
    return defaults


# ── _derive_result unit tests ─────────────────────────────────────────────


class TestDeriveResult:
    def test_hb_error_status_gives_failed(self):
        assert _derive_result(_txn(status="error"), None) == "failed"

    def test_core_error_workflow_gives_failed(self):
        assert _derive_result(_txn(status="pending"), {"workflow_status": "ERROR"}) == "failed"

    def test_is_duplicate_gives_duplicate(self):
        assert _derive_result(_txn(is_duplicate=True, status="pending"), None) == "duplicate"

    def test_failed_beats_duplicate(self):
        assert _derive_result(_txn(is_duplicate=True, status="error"), None) == "failed"

    def test_not_an_invoice_terminal(self):
        assert _derive_result(_txn(status="not_an_invoice"), None) == "not_an_invoice"

    def test_acknowledged_gives_processed(self):
        assert _derive_result(_txn(status="acknowledged"), None) == "processed"

    def test_core_irn_gives_processed_even_if_hb_pending(self):
        assert _derive_result(_txn(status="pending"), {"irn": "CORE-IRN"}) == "processed"

    def test_pending_when_seeded_only(self):
        assert _derive_result(_txn(status="pending"), None) == "pending"

    def test_core_only_with_irn_is_processed(self):
        # No HB row at all (e.g. internal upload), Core invoice has an IRN.
        assert _derive_result(None, _inv()) == "processed"

    def test_both_none_defaults_pending(self):
        assert _derive_result(None, None) == "pending"


# ── _build_entry unit tests ───────────────────────────────────────────────


class TestBuildEntry:
    def test_hb_only_entry(self):
        entry = _build_entry(_txn(transaction_id="TXN-A", status="pending"), None)
        assert entry.transaction_id == "TXN-A"
        assert entry.result == "pending"
        assert entry.irn is None
        assert entry.firs_status is None
        assert entry.invoice_number is None
        assert entry.received_at == "2026-06-17T09:30:00Z"

    def test_core_fields_merged(self):
        entry = _build_entry(_txn(status="acknowledged"), _inv())
        assert entry.irn == "IRN-001"
        assert entry.invoice_number == "INV-2026-001"
        assert entry.firs_status == "TRANSMITTED"  # from transmission_status
        assert entry.processed_at == "2026-06-17T10:05:00Z"
        assert entry.result == "processed"

    def test_firs_status_prefers_explicit_field(self):
        entry = _build_entry(_txn(status="acknowledged"), _inv(firs_status="ACCEPTED"))
        assert entry.firs_status == "ACCEPTED"

    def test_join_key_from_core_when_hb_absent(self):
        entry = _build_entry(None, _inv(external_transaction_id="TXN-Z"))
        assert entry.transaction_id == "TXN-Z"

    def test_batch_id_hint_used_when_records_lack_it(self):
        entry = _build_entry(_txn(batch_id=None), None, batch_id_hint="BHINT")
        assert entry.batch_id == "BHINT"


# ── StatusService.query — batch_id ─────────────────────────────────────────


class TestQueryByBatch:
    @pytest.mark.asyncio
    async def test_batch_merges_hb_and_core_by_txn(self):
        hb_rows = [_txn(transaction_id="T1", status="acknowledged"),
                   _txn(transaction_id="T2", status="pending")]
        core_rows = [_inv(external_transaction_id="T1", irn="IRN-T1", transmission_status="ACCEPTED")]
        svc = StatusService(_MockHB(by_batch=hb_rows), _MockCore(by_batch=core_rows))
        resp = await svc.query(batch_id="B1", tenant_id="t1")

        by_txn = {e.transaction_id: e for e in resp.results}
        assert len(resp.results) == 2
        assert by_txn["T1"].result == "processed"
        assert by_txn["T1"].firs_status == "ACCEPTED"
        assert by_txn["T1"].irn == "IRN-T1"
        assert by_txn["T2"].result == "pending"
        assert by_txn["T2"].firs_status is None

    @pytest.mark.asyncio
    async def test_batch_core_only_invoice_still_listed(self):
        # Core has an invoice for a txn HB never seeded (internal upload).
        core_rows = [_inv(external_transaction_id="T9", irn="IRN-9")]
        svc = StatusService(_MockHB(by_batch=[]), _MockCore(by_batch=core_rows))
        resp = await svc.query(batch_id="B1", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].transaction_id == "T9"
        assert resp.results[0].result == "processed"

    @pytest.mark.asyncio
    async def test_batch_calls_hb_and_core(self):
        hb = _MockHB(by_batch=[_txn()])
        core = _MockCore()
        svc = StatusService(hb, core)
        await svc.query(batch_id="MYBATCH", tenant_id="t1")
        assert any(c == ("by_batch", "MYBATCH") for c in hb.calls)
        assert any(c == ("get_invoices_by_batch", "MYBATCH") for c in core.calls)

    @pytest.mark.asyncio
    async def test_batch_empty_both_sides(self):
        svc = StatusService(_MockHB(by_batch=[]), _MockCore(by_batch=[]))
        resp = await svc.query(batch_id="EMPTY", tenant_id="t1")
        assert resp.results == []


# ── StatusService.query — transaction_id ───────────────────────────────────


class TestQueryByTransaction:
    @pytest.mark.asyncio
    async def test_txn_hb_and_core_merge(self):
        svc = StatusService(
            _MockHB(by_txn=_txn(transaction_id="TXN-X", status="acknowledged")),
            _MockCore(result=_inv(external_transaction_id="TXN-X", irn="IRN-X")),
        )
        resp = await svc.query(transaction_id="TXN-X", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].transaction_id == "TXN-X"
        assert resp.results[0].result == "processed"
        assert resp.results[0].irn == "IRN-X"

    @pytest.mark.asyncio
    async def test_txn_unknown_both_sides_empty(self):
        svc = StatusService(_MockHB(by_txn=None), _MockCore(result=None))
        resp = await svc.query(transaction_id="UNKNOWN", tenant_id="t1")
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_txn_hb_only_pending(self):
        # HB seeded pending, Core has no invoice yet (graceful null).
        svc = StatusService(_MockHB(by_txn=_txn(status="pending")), _MockCore(result=None))
        resp = await svc.query(transaction_id="TXN001", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].result == "pending"
        assert resp.results[0].firs_status is None
        assert resp.results[0].invoice_number is None


# ── StatusService.query — invoice_number / irn (Core-only + HB backfill) ────


class TestQueryCoreOnly:
    @pytest.mark.asyncio
    async def test_invoice_number_resolved_by_core(self):
        svc = StatusService(
            _MockHB(by_txn=_txn(transaction_id="TXN-INV", status="acknowledged")),
            _MockCore(result=_inv(external_transaction_id="TXN-INV", invoice_number="INV-77")),
        )
        resp = await svc.query(invoice_number="INV-77", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].invoice_number == "INV-77"
        assert resp.results[0].result == "processed"

    @pytest.mark.asyncio
    async def test_irn_resolved_by_core(self):
        svc = StatusService(
            _MockHB(by_txn=None),
            _MockCore(result=_inv(irn="IRN-ABC", external_transaction_id="TXN-IRN")),
        )
        resp = await svc.query(irn="IRN-ABC", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].irn == "IRN-ABC"

    @pytest.mark.asyncio
    async def test_irn_unknown_core_returns_empty(self):
        # HB cannot resolve an IRN — Core None means empty results.
        svc = StatusService(_MockHB(), _MockCore(result=None))
        resp = await svc.query(irn="NO-SUCH-IRN", tenant_id="t1")
        assert resp.results == []

    @pytest.mark.asyncio
    async def test_invoice_number_does_not_call_hb_directly(self):
        # HB has no irn/invoice_number surface; only the backfill-by-txn call.
        hb = _MockHB(by_txn=_txn(transaction_id="TXN-BF"))
        svc = StatusService(hb, _MockCore(result=_inv(external_transaction_id="TXN-BF")))
        await svc.query(invoice_number="INV-1", tenant_id="t1")
        # The only HB call is the backfill by the txn Core returned.
        assert hb.calls == [("by_txn", "TXN-BF")]


# ── Graceful degradation ───────────────────────────────────────────────────


class TestGraceful:
    @pytest.mark.asyncio
    async def test_core_exception_swallowed_hb_data_survives(self):
        svc = StatusService(
            _MockHB(by_txn=_txn(status="acknowledged")),
            _MockCore(raise_exc=True),
        )
        resp = await svc.query(transaction_id="TXN001", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].firs_status is None
        assert resp.results[0].result == "processed"  # from HB acknowledged

    @pytest.mark.asyncio
    async def test_hb_exception_batch_core_data_survives(self):
        # HB down on a batch query → Core invoices still answer.
        core_rows = [_inv(external_transaction_id="T1", irn="IRN-1")]
        svc = StatusService(_MockHB(raise_on="batch"), _MockCore(by_batch=core_rows))
        resp = await svc.query(batch_id="B1", tenant_id="t1")
        assert len(resp.results) == 1
        assert resp.results[0].transaction_id == "T1"

    @pytest.mark.asyncio
    async def test_hb_txn_exception_core_data_survives(self):
        svc = StatusService(
            _MockHB(raise_on="txn"),
            _MockCore(result=_inv(external_transaction_id="TXN001")),
        )
        resp = await svc.query(transaction_id="TXN001", tenant_id="t1")
        # HB raised → treated as no HB row, but Core invoice still builds an entry.
        assert len(resp.results) == 1
        assert resp.results[0].result == "processed"

    @pytest.mark.asyncio
    async def test_no_selector_returns_empty(self):
        svc = StatusService(_MockHB(), _MockCore())
        resp = await svc.query(tenant_id="t1")
        assert resp.results == []
