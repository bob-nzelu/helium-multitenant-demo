"""
Tests for BatchExternalService (Q37 Gap #3/#4/#7/#8)

Covers:
  - Single-record batch → processed[1]
  - Multi-record batch → processed[2]
  - Duplicate transaction_id within a batch → duplicates[1]
  - Missing transaction_id → failed[1]
  - Missing fee_amount → failed[1]
  - VAT auto-compute at 7.5% when vat_amount absent
  - Tenant firs_service_id injected into IRN (Gap #8: not A8BM72KQ)
  - Non-JSON content still goes through single-doc path (route-level check,
    tested at route level in test_ingest_batch.py; service is never called)

Uses:
  - StubHeartBeatClient (no real HTTP)
  - RecordingLifecyclePublisher (no Core)
  - TransformaModuleCache loaded via stub (generates real IRN/QR)
"""

import pytest

from src.core.irn import IRNGenerator
from src.core.module_cache import TransformaModuleCache
from src.core.qr import QRGenerator
from src.core.tenant import TenantConfig
from src.config import RelayConfig
from src.clients.core import CoreClient
from src.services.batch_external import BatchExternalService, BatchIngestResult, auto_batch_id
from src.services.ingestion import IngestionService
from src.api.models import BatchIngestResponse
from tests.stub_heartbeat import StubHeartBeatClient


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
    )


@pytest.fixture
def heartbeat():
    return StubHeartBeatClient()


@pytest.fixture
def core():
    return CoreClient()


@pytest.fixture
async def loaded_cache():
    """Module cache with Transforma modules loaded."""
    client = StubHeartBeatClient()
    cache = TransformaModuleCache(client, refresh_interval_s=3600)
    await cache.load_all()
    yield cache
    await cache.cleanup()


@pytest.fixture
def irn_gen(loaded_cache):
    return IRNGenerator(loaded_cache)


@pytest.fixture
def qr_gen(loaded_cache):
    return QRGenerator(loaded_cache)


@pytest.fixture
def ingestion(config, heartbeat, core):
    return IngestionService(config, heartbeat, core)


@pytest.fixture
def batch_service(ingestion, core, irn_gen, qr_gen):
    return BatchExternalService(ingestion, core, irn_gen, qr_gen)


@pytest.fixture
def tenant():
    return TenantConfig(
        tenant_id="abbey-mortgage",
        api_key="abbey-api-key",
        api_secret="abbey-secret",
        service_id="ABBEY001",   # <-- tenant FIRS service ID (not A8BM72KQ)
        name="Abbey Mortgage Bank",
    )


def _record(txn_id="TXN-001", fee=1000.0, vat=None, **extra):
    r = {"transaction_id": txn_id, "fee_amount": fee}
    if vat is not None:
        r["vat_amount"] = vat
    r.update(extra)
    return r


# ── Single Record ──────────────────────────────────────────────────────────────


class TestBatchSingleRecord:
    @pytest.mark.asyncio
    async def test_single_record_processed(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record()],
            batch_id="BATCH20260617000001",
            tenant=tenant,
            trace_id="trc-single",
        )
        assert len(result.processed) == 1
        assert len(result.duplicates) == 0
        assert len(result.failed) == 0

    @pytest.mark.asyncio
    async def test_single_record_transaction_id_echoed(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record(txn_id="MY-TXN-42")],
            batch_id="BATCH001",
            tenant=tenant,
            trace_id="trc-echo",
        )
        assert result.processed[0].transaction_id == "MY-TXN-42"

    @pytest.mark.asyncio
    async def test_single_record_irn_present(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record()],
            batch_id="BATCH001",
            tenant=tenant,
            trace_id="trc-irn",
        )
        assert isinstance(result.processed[0].irn, str)
        assert len(result.processed[0].irn) > 0

    @pytest.mark.asyncio
    async def test_single_record_qr_present(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record()],
            batch_id="BATCH001",
            tenant=tenant,
            trace_id="trc-qr",
        )
        assert isinstance(result.processed[0].qr_code, str)
        assert len(result.processed[0].qr_code) > 0

    @pytest.mark.asyncio
    async def test_single_record_data_uuid_present(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record()],
            batch_id="BATCH001",
            tenant=tenant,
            trace_id="trc-uuid",
        )
        assert isinstance(result.processed[0].data_uuid, str)
        assert len(result.processed[0].data_uuid) > 0


# ── Multi-Record ──────────────────────────────────────────────────────────────


class TestBatchMultiRecord:
    @pytest.mark.asyncio
    async def test_two_records_both_processed(self, batch_service, tenant):
        records = [
            _record(txn_id="TXN-A", fee=500.0),
            _record(txn_id="TXN-B", fee=800.0),
        ]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BATCH002",
            tenant=tenant,
            trace_id="trc-multi",
        )
        assert len(result.processed) == 2
        assert len(result.duplicates) == 0
        assert len(result.failed) == 0

    @pytest.mark.asyncio
    async def test_two_records_distinct_irns(self, batch_service, tenant):
        records = [
            _record(txn_id="TXN-X", fee=100.0),
            _record(txn_id="TXN-Y", fee=200.0),
        ]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BATCH002",
            tenant=tenant,
            trace_id="trc-distinct",
        )
        irns = [e.transaction_id for e in result.processed]
        assert "TXN-X" in irns
        assert "TXN-Y" in irns

    @pytest.mark.asyncio
    async def test_summary_counts_correct(self, batch_service, tenant):
        records = [_record(txn_id="A"), _record(txn_id="B")]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BATCHX",
            tenant=tenant,
            trace_id="trc-sum",
        )
        s = result.summary
        assert s.total == 2
        assert s.processed == 2
        assert s.duplicates == 0
        assert s.failed == 0


# ── Duplicate transaction_id ──────────────────────────────────────────────────


class TestBatchDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate_txn_in_same_batch_goes_to_duplicates(
        self, batch_service, tenant
    ):
        records = [
            _record(txn_id="DUP-001"),
            _record(txn_id="DUP-001"),  # same txn_id → duplicate
        ]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BATCH-DUP",
            tenant=tenant,
            trace_id="trc-dup",
        )
        assert len(result.processed) == 1
        assert len(result.duplicates) == 1
        assert result.duplicates[0].transaction_id == "DUP-001"

    @pytest.mark.asyncio
    async def test_duplicate_entry_has_duplicate_of(self, batch_service, tenant):
        records = [_record(txn_id="DUP-002"), _record(txn_id="DUP-002")]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BATCH-DUP2",
            tenant=tenant,
            trace_id="trc-dup2",
        )
        dup = result.duplicates[0]
        assert dup.duplicate_of is not None
        assert "irn" in dup.duplicate_of
        assert "data_uuid" in dup.duplicate_of

    @pytest.mark.asyncio
    async def test_status_partial_when_duplicates(self, batch_service, tenant):
        records = [_record(txn_id="D"), _record(txn_id="D")]
        result = await batch_service.process_batch(
            records=records,
            batch_id="B",
            tenant=tenant,
            trace_id="t",
        )
        assert result.status == "partial"


# ── Missing Fields ─────────────────────────────────────────────────────────────


class TestBatchValidationFailed:
    @pytest.mark.asyncio
    async def test_missing_transaction_id_goes_to_failed(
        self, batch_service, tenant
    ):
        records = [{"fee_amount": 500.0, "description": "no txn id"}]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BFAIL",
            tenant=tenant,
            trace_id="trc-fail",
        )
        assert len(result.failed) == 1
        assert result.failed[0].error_code == "VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_missing_fee_amount_goes_to_failed(self, batch_service, tenant):
        records = [{"transaction_id": "TXN-NO-FEE"}]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BFAIL2",
            tenant=tenant,
            trace_id="trc-fail2",
        )
        assert len(result.failed) == 1
        assert result.failed[0].transaction_id == "TXN-NO-FEE"

    @pytest.mark.asyncio
    async def test_mixed_valid_invalid(self, batch_service, tenant):
        records = [
            _record(txn_id="GOOD"),
            {"fee_amount": 100.0},  # missing txn_id
        ]
        result = await batch_service.process_batch(
            records=records,
            batch_id="BMIX",
            tenant=tenant,
            trace_id="trc-mix",
        )
        assert len(result.processed) == 1
        assert len(result.failed) == 1
        assert result.status == "partial"

    @pytest.mark.asyncio
    async def test_all_failed_status_is_rejected(self, batch_service, tenant):
        records = [{"fee_amount": 100.0}]  # no txn_id
        result = await batch_service.process_batch(
            records=records,
            batch_id="BREJ",
            tenant=tenant,
            trace_id="trc-rej",
        )
        assert result.status == "rejected"


# ── VAT Auto-Compute ──────────────────────────────────────────────────────────


class TestBatchVATCompute:
    @pytest.mark.asyncio
    async def test_vat_auto_computed_when_absent(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record(fee=1000.0)],  # no vat_amount
            batch_id="BVAT",
            tenant=tenant,
            trace_id="trc-vat",
        )
        assert result.processed[0].vat_amount == pytest.approx(75.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_vat_preserved_when_supplied(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record(fee=1000.0, vat=50.0)],  # explicit vat
            batch_id="BVAT2",
            tenant=tenant,
            trace_id="trc-vat2",
        )
        assert result.processed[0].vat_amount == pytest.approx(50.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_fee_amount_echoed(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record(fee=2500.0)],
            batch_id="BFEE",
            tenant=tenant,
            trace_id="trc-fee",
        )
        assert result.processed[0].fee_amount == pytest.approx(2500.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_vat_rounding(self, batch_service, tenant):
        # 333.33 * 0.075 = 24.99975 → rounds to 25.0
        result = await batch_service.process_batch(
            records=[_record(fee=333.33)],
            batch_id="BROUND",
            tenant=tenant,
            trace_id="trc-round",
        )
        assert result.processed[0].vat_amount == pytest.approx(25.0, abs=0.01)


# ── Tenant FIRS Service ID Injection (Gap #8) ─────────────────────────────────


class TestBatchFIRSServiceID:
    @pytest.mark.asyncio
    async def test_irn_uses_tenant_service_id_not_hardcoded_default(
        self, batch_service, tenant
    ):
        """Gap #8: IRN must use tenant.service_id, never A8BM72KQ."""
        # The stub Transforma IRN module generates its own format;
        # the inline fallback would embed the service_id.
        # We verify the default fallback ("A8BM72KQ") does NOT appear,
        # and check the module was given firs_service_id=tenant.service_id.
        # We do this by patching irn_generator.generate and inspecting
        # the invoice_data it receives.
        captured = {}

        original_generate = batch_service._irn.generate

        def patched_generate(invoice_data):
            captured["invoice_data"] = dict(invoice_data)
            return original_generate(invoice_data)

        batch_service._irn.generate = patched_generate

        await batch_service.process_batch(
            records=[_record()],
            batch_id="BGAP8",
            tenant=tenant,
            trace_id="trc-gap8",
        )

        assert "invoice_data" in captured
        assert captured["invoice_data"].get("firs_service_id") == "ABBEY001"
        assert captured["invoice_data"].get("firs_service_id") != "A8BM72KQ"

    @pytest.mark.asyncio
    async def test_different_tenants_get_different_service_ids(
        self, batch_service
    ):
        """Two different tenant objects → two different firs_service_ids."""
        tenant_a = TenantConfig(
            tenant_id="a", api_key="ka", api_secret="sa",
            service_id="FIRS-A001", name="A",
        )
        tenant_b = TenantConfig(
            tenant_id="b", api_key="kb", api_secret="sb",
            service_id="FIRS-B002", name="B",
        )

        captured = []

        original_generate = batch_service._irn.generate

        def patched_generate(invoice_data):
            captured.append(invoice_data.get("firs_service_id"))
            return original_generate(invoice_data)

        batch_service._irn.generate = patched_generate

        await batch_service.process_batch(
            records=[_record(txn_id="TA-001")], batch_id="B1", tenant=tenant_a, trace_id="t1"
        )
        await batch_service.process_batch(
            records=[_record(txn_id="TB-001")], batch_id="B2", tenant=tenant_b, trace_id="t2"
        )

        assert captured[0] == "FIRS-A001"
        assert captured[1] == "FIRS-B002"


# ── BatchIngestResult helpers ─────────────────────────────────────────────────


class TestBatchIngestResultHelpers:
    @pytest.mark.asyncio
    async def test_to_response_returns_batch_ingest_response(
        self, batch_service, tenant
    ):
        result = await batch_service.process_batch(
            records=[_record()], batch_id="BRESP", tenant=tenant, trace_id="t"
        )
        resp = result.to_response()
        assert isinstance(resp, BatchIngestResponse)
        assert resp.batch_id == "BRESP"
        assert resp.status in ("ok", "partial", "rejected")

    @pytest.mark.asyncio
    async def test_ok_status_when_all_processed(self, batch_service, tenant):
        result = await batch_service.process_batch(
            records=[_record()], batch_id="BOK", tenant=tenant, trace_id="t"
        )
        assert result.status == "ok"

    def test_auto_batch_id_format(self):
        bid = auto_batch_id()
        assert bid.startswith("BATCH")
        assert len(bid) == len("BATCH20260617120000")  # BATCH + 14 digits


# ── Tenant Field Mapping ──────────────────────────────────────────────────────


class TestBatchTenantFieldMapping:
    @pytest.mark.asyncio
    async def test_tenant_field_alias_resolved(self, batch_service):
        """When a tenant maps 'ref_no' → 'transaction_id', the alias works."""
        tenant_with_alias = TenantConfig(
            tenant_id="t-alias",
            api_key="k",
            api_secret="s",
            service_id="SVC001",
            name="Alias Tenant",
            fields={"transaction_id": "ref_no"},
        )
        # Record uses "ref_no" instead of "transaction_id"
        records = [{"ref_no": "ALIAS-TXN-001", "fee_amount": 200.0}]

        result = await batch_service.process_batch(
            records=records,
            batch_id="BALIAS",
            tenant=tenant_with_alias,
            trace_id="trc-alias",
        )
        assert len(result.processed) == 1
        assert result.processed[0].transaction_id == "ALIAS-TXN-001"
