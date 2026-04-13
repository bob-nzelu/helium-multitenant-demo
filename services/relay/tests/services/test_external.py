"""
Tests for ExternalService (API flow)
"""

import pytest

from src.services.external import ExternalService, ExternalResult
from src.services.ingestion import IngestionService
from src.core.irn import IRNGenerator
from src.core.qr import QRGenerator
from src.core.module_cache import TransformaModuleCache
from src.config import RelayConfig
from src.clients.core import CoreClient
from tests.stub_heartbeat import StubHeartBeatClient
from src.errors import ModuleNotLoadedError


# ── Fixtures ──────────────────────────────────────────────────────────────


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
def external_service(ingestion, core, irn_gen, qr_gen):
    return ExternalService(ingestion, core, irn_gen, qr_gen)


@pytest.fixture
def single_pdf():
    return [("invoice.pdf", b"%PDF-1.4 test invoice")]


# ── Happy Path ────────────────────────────────────────────────────────────


class TestExternalHappyPath:
    """Test successful external API flow."""

    @pytest.mark.asyncio
    async def test_process_returns_external_result(self, external_service, single_pdf):
        result = await external_service.process(
            single_pdf, api_key="ext-key", trace_id="t-200"
        )
        assert isinstance(result, ExternalResult)

    @pytest.mark.asyncio
    async def test_status_is_processed(self, external_service, single_pdf):
        result = await external_service.process(
            single_pdf, api_key="ext-key", trace_id="t-201"
        )
        assert result.status == "processed"

    @pytest.mark.asyncio
    async def test_irn_generated(self, external_service, single_pdf):
        result = await external_service.process(
            single_pdf, api_key="ext-key", trace_id="t-202"
        )
        assert isinstance(result.irn, str)
        assert len(result.irn) > 0

    @pytest.mark.asyncio
    async def test_qr_code_generated(self, external_service, single_pdf):
        result = await external_service.process(
            single_pdf, api_key="ext-key", trace_id="t-203"
        )
        assert isinstance(result.qr_code, str)
        assert len(result.qr_code) > 0

    @pytest.mark.asyncio
    async def test_ingest_result_embedded(self, external_service, single_pdf):
        result = await external_service.process(
            single_pdf, api_key="ext-key", trace_id="t-204"
        )
        assert result.ingest.file_count == 1
        assert result.ingest.status == "ingested"

    @pytest.mark.asyncio
    async def test_with_invoice_data(self, external_service, single_pdf):
        """Invoice data is passed through to IRN generator."""
        result = await external_service.process(
            single_pdf,
            api_key="ext-key",
            trace_id="t-205",
            invoice_data={"invoice_number": "EXT-001", "tin": "9876543210"},
        )
        assert isinstance(result.irn, str)
        assert len(result.irn) > 0


# ── Core Unavailable ─────────────────────────────────────────────────────


class TestExternalCoreFail:
    """Test external flow when Core is unavailable."""

    @pytest.mark.asyncio
    async def test_core_down_still_returns_irn_qr(
        self, config, heartbeat, irn_gen, qr_gen
    ):
        """Even if Core is down, IRN/QR are still generated."""

        class FailCore(CoreClient):
            async def enqueue(self, blob_uuid, filename, file_size_bytes, batch_id):
                raise ConnectionError("Core is down")

            async def process_immediate(self, queue_id):
                raise ConnectionError("Core is down")

        fail_core = FailCore()
        ingestion = IngestionService(config, heartbeat, fail_core)
        svc = ExternalService(ingestion, fail_core, irn_gen, qr_gen)

        result = await svc.process(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "processed"
        assert len(result.irn) > 0
        assert len(result.qr_code) > 0


# ── Module Not Loaded ────────────────────────────────────────────────────


class TestExternalModuleNotLoaded:
    """Test external flow when Transforma modules are not cached."""

    @pytest.mark.asyncio
    async def test_module_not_loaded_raises_503(self, config, heartbeat, core):
        """When cache is cold, external flow raises ModuleNotLoadedError."""
        unloaded = TransformaModuleCache(StubHeartBeatClient())
        irn_gen = IRNGenerator(unloaded)
        qr_gen = QRGenerator(unloaded)

        ingestion = IngestionService(config, heartbeat, core)
        svc = ExternalService(ingestion, core, irn_gen, qr_gen)

        with pytest.raises(ModuleNotLoadedError):
            await svc.process(
                [("test.pdf", b"data")], api_key="k", trace_id="t"
            )
