"""
Tests for IRNGenerator wrapper
"""

import pytest

from src.core.irn import IRNGenerator
from src.core.module_cache import TransformaModuleCache
from tests.stub_heartbeat import StubHeartBeatClient
from src.errors import IRNGenerationError, ModuleNotLoadedError


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
def irn_generator(loaded_cache):
    """IRNGenerator backed by loaded cache."""
    return IRNGenerator(loaded_cache)


@pytest.fixture
def unloaded_cache():
    """Module cache that has NOT been loaded."""
    client = StubHeartBeatClient()
    return TransformaModuleCache(client, refresh_interval_s=3600)


@pytest.fixture
def irn_generator_unloaded(unloaded_cache):
    """IRNGenerator backed by unloaded cache."""
    return IRNGenerator(unloaded_cache)


# ── Generate Tests ────────────────────────────────────────────────────────


class TestIRNGenerate:
    """Test IRN generation via cached module."""

    @pytest.mark.asyncio
    async def test_generate_returns_string(self, irn_generator):
        irn = irn_generator.generate({"invoice_number": "INV-001", "tin": "1234567890"})
        assert isinstance(irn, str)
        assert len(irn) > 0

    @pytest.mark.asyncio
    async def test_generate_deterministic_inputs(self, irn_generator):
        """Same input data should produce consistent IRN format."""
        data = {"invoice_number": "INV-001", "tin": "9876543210"}
        irn1 = irn_generator.generate(data)
        irn2 = irn_generator.generate(data)
        # Both should be non-empty strings (exact equality depends on timestamp)
        assert isinstance(irn1, str) and len(irn1) > 0
        assert isinstance(irn2, str) and len(irn2) > 0

    @pytest.mark.asyncio
    async def test_generate_with_minimal_data(self, irn_generator):
        """Should handle minimal invoice data."""
        irn = irn_generator.generate({"tin": "0000000000"})
        assert isinstance(irn, str)

    @pytest.mark.asyncio
    async def test_generate_with_empty_dict(self, irn_generator):
        """Stub module handles empty dict gracefully."""
        irn = irn_generator.generate({})
        assert isinstance(irn, str)


class TestIRNErrors:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_module_not_loaded_raises(self, irn_generator_unloaded):
        with pytest.raises(ModuleNotLoadedError):
            irn_generator_unloaded.generate({"tin": "1234567890"})

    @pytest.mark.asyncio
    async def test_broken_module_raises_irn_error(self):
        """If the cached module's generate_irn raises, wrap in IRNGenerationError."""
        import types

        class FakeCache:
            def get_irn_module(self):
                mod = types.ModuleType("fake_irn")
                def generate_irn(data):
                    raise ValueError("bad invoice data")
                mod.generate_irn = generate_irn
                return mod

        gen = IRNGenerator(FakeCache())
        with pytest.raises(IRNGenerationError) as exc_info:
            gen.generate({"tin": "test"})
        assert "bad invoice data" in str(exc_info.value)
