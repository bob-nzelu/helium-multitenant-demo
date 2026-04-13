"""
Tests for QRGenerator wrapper
"""

import pytest

from src.core.qr import QRGenerator
from src.core.module_cache import TransformaModuleCache
from tests.stub_heartbeat import StubHeartBeatClient
from src.errors import ModuleNotLoadedError, QRGenerationError


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def loaded_cache():
    """Module cache with Transforma modules loaded."""
    client = StubHeartBeatClient()
    cache = TransformaModuleCache(client, refresh_interval_s=3600)
    await cache.load_all()
    yield cache
    await cache.cleanup()


@pytest.fixture
def qr_generator(loaded_cache):
    """QRGenerator backed by loaded cache."""
    return QRGenerator(loaded_cache)


@pytest.fixture
def unloaded_cache():
    """Module cache that has NOT been loaded."""
    client = StubHeartBeatClient()
    return TransformaModuleCache(client, refresh_interval_s=3600)


@pytest.fixture
def qr_generator_unloaded(unloaded_cache):
    """QRGenerator backed by unloaded cache."""
    return QRGenerator(unloaded_cache)


# ── Generate QR Data ─────────────────────────────────────────────────────


class TestQRGenerate:
    """Test QR data generation via cached module."""

    @pytest.mark.asyncio
    async def test_generate_returns_string(self, qr_generator):
        qr_data = qr_generator.generate("TEST-IRN-001")
        assert isinstance(qr_data, str)
        assert len(qr_data) > 0

    @pytest.mark.asyncio
    async def test_generate_base64_format(self, qr_generator):
        """QR data should be base64-encoded."""
        import base64
        qr_data = qr_generator.generate("TEST-IRN-002")
        # Should be valid base64 (no exception on decode)
        decoded = base64.b64decode(qr_data)
        assert len(decoded) > 0

    @pytest.mark.asyncio
    async def test_generate_different_irns(self, qr_generator):
        """Different IRNs should produce different QR data."""
        qr1 = qr_generator.generate("IRN-AAA")
        qr2 = qr_generator.generate("IRN-BBB")
        assert qr1 != qr2


class TestQRGenerateImage:
    """Test QR image generation."""

    @pytest.mark.asyncio
    async def test_generate_image_returns_bytes(self, qr_generator):
        img = qr_generator.generate_image("TEST-IRN-003")
        assert isinstance(img, bytes)
        assert len(img) > 0

    @pytest.mark.asyncio
    async def test_generate_image_is_bytes_with_content(self, qr_generator):
        """Image bytes should have non-trivial content."""
        img = qr_generator.generate_image("TEST-IRN-004")
        # Stub returns placeholder; real module returns PNG.
        # Just verify it's non-trivial bytes.
        assert len(img) > 4


class TestQRErrors:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_module_not_loaded_generate(self, qr_generator_unloaded):
        with pytest.raises(ModuleNotLoadedError):
            qr_generator_unloaded.generate("IRN-TEST")

    @pytest.mark.asyncio
    async def test_module_not_loaded_generate_image(self, qr_generator_unloaded):
        with pytest.raises(ModuleNotLoadedError):
            qr_generator_unloaded.generate_image("IRN-TEST")

    @pytest.mark.asyncio
    async def test_broken_module_raises_qr_error(self):
        """If the cached module raises, wrap in QRGenerationError."""
        import types

        class FakeCache:
            def get_qr_module(self):
                mod = types.ModuleType("fake_qr")
                def generate_qr_data(irn):
                    raise RuntimeError("QR lib missing")
                mod.generate_qr_data = generate_qr_data
                return mod

        gen = QRGenerator(FakeCache())
        with pytest.raises(QRGenerationError) as exc_info:
            gen.generate("IRN-TEST")
        assert "QR lib missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_broken_image_module_raises_qr_error(self):
        """If create_qr_image_bytes raises, wrap in QRGenerationError."""
        import types

        class FakeCache:
            def get_qr_module(self):
                mod = types.ModuleType("fake_qr")
                def create_qr_image_bytes(irn):
                    raise OSError("Pillow not installed")
                mod.create_qr_image_bytes = create_qr_image_bytes
                return mod

        gen = QRGenerator(FakeCache())
        with pytest.raises(QRGenerationError) as exc_info:
            gen.generate_image("IRN-TEST")
        assert "Pillow not installed" in str(exc_info.value)
