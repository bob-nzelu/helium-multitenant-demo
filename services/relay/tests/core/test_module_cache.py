"""
Tests for TransformaModuleCache
"""

import asyncio
import pytest

from src.core.module_cache import TransformaModuleCache
from src.errors import ModuleNotLoadedError, ModuleCacheError
from tests.stub_heartbeat import StubHeartBeatClient


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def heartbeat_client():
    """HeartBeat client with stub Transforma config response."""
    return StubHeartBeatClient()


@pytest.fixture
def cache(heartbeat_client):
    """Un-loaded module cache (call load_all in test)."""
    return TransformaModuleCache(heartbeat_client, refresh_interval_s=3600)


# ── Load Tests ────────────────────────────────────────────────────────────


class TestModuleCacheLoad:
    """Test loading modules from HeartBeat."""

    @pytest.mark.asyncio
    async def test_load_all_creates_cache_dir(self, cache):
        await cache.load_all()
        assert cache._cache_dir is not None
        assert cache._cache_dir.exists()
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_load_all_sets_loaded(self, cache):
        assert cache.is_loaded is False
        await cache.load_all()
        assert cache.is_loaded is True
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_load_all_loads_irn_module(self, cache):
        await cache.load_all()
        module = cache.get_irn_module()
        assert hasattr(module, "generate_irn")
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_load_all_loads_qr_module(self, cache):
        await cache.load_all()
        module = cache.get_qr_module()
        assert hasattr(module, "generate_qr_data")
        assert hasattr(module, "create_qr_image_bytes")
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_load_all_loads_service_keys(self, cache):
        await cache.load_all()
        keys = cache.service_keys
        assert keys.csid == "STUB-CSID-TOKEN"
        assert "BEGIN PUBLIC KEY" in keys.firs_public_key_pem
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_module_names(self, cache):
        await cache.load_all()
        names = cache.module_names
        assert "irn_generator" in names
        assert "qr_generator" in names
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_irn_module_generates(self, cache):
        """Loaded IRN module should actually generate an IRN string."""
        await cache.load_all()
        module = cache.get_irn_module()
        irn = module.generate_irn({"invoice_number": "INV-001", "tin": "1234567890"})
        assert isinstance(irn, str)
        assert len(irn) > 0
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_qr_module_generates(self, cache):
        """Loaded QR module should generate base64 QR data."""
        await cache.load_all()
        module = cache.get_qr_module()
        qr_data = module.generate_qr_data("TEST-IRN-123")
        assert isinstance(qr_data, str)
        assert len(qr_data) > 0
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_qr_module_creates_image_bytes(self, cache):
        await cache.load_all()
        module = cache.get_qr_module()
        img_bytes = module.create_qr_image_bytes("test-data")
        assert isinstance(img_bytes, bytes)
        await cache.cleanup()


class TestModuleCacheErrors:
    """Test error handling when cache is not loaded."""

    @pytest.mark.asyncio
    async def test_get_module_before_load(self, cache):
        with pytest.raises(ModuleNotLoadedError) as exc_info:
            cache.get_irn_module()
        assert "irn_generator" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_qr_before_load(self, cache):
        with pytest.raises(ModuleNotLoadedError):
            cache.get_qr_module()

    @pytest.mark.asyncio
    async def test_service_keys_before_load(self, cache):
        with pytest.raises(ModuleNotLoadedError):
            _ = cache.service_keys

    @pytest.mark.asyncio
    async def test_get_nonexistent_module(self, cache):
        await cache.load_all()
        with pytest.raises(ModuleNotLoadedError):
            cache.get_module("nonexistent_module")
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_load_all_graceful_on_heartbeat_failure(self):
        """If HeartBeat is down, cache should degrade gracefully."""
        class FailingHeartBeat:
            async def get_transforma_config(self):
                raise ConnectionError("HeartBeat is down")

        cache = TransformaModuleCache(FailingHeartBeat())
        await cache.load_all()  # Should not raise
        assert cache.is_loaded is False
        await cache.cleanup()


class TestModuleCacheRefresh:
    """Test refresh logic."""

    @pytest.mark.asyncio
    async def test_refresh_skips_unchanged(self, cache):
        """Refresh should skip modules with matching checksum."""
        await cache.load_all()
        result = await cache.refresh()
        # Same checksums → no updates
        assert result["modules_updated"] == []
        assert result["keys_updated"] is False
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_refresh_detects_change(self):
        """Refresh should reload module when checksum changes."""
        call_count = 0

        class ChangingHeartBeat:
            async def get_transforma_config(self):
                nonlocal call_count
                call_count += 1
                return {
                    "modules": [
                        {
                            "module_name": "irn_generator",
                            "source_code": (
                                f'VERSION = {call_count}\n'
                                'def generate_irn(invoice_data: dict) -> str:\n'
                                f'    return "IRN-v{call_count}"\n'
                            ),
                            "version": f"{call_count}.0.0",
                            "checksum": f"sha256:checksum_{call_count}",
                            "updated_at": "2026-01-01T00:00:00Z",
                        },
                    ],
                    "service_keys": {
                        "firs_public_key_pem": "key",
                        "csid": f"CSID-{call_count}",
                        "csid_expires_at": "2030-01-01T00:00:00Z",
                    },
                }

        cache = TransformaModuleCache(ChangingHeartBeat())
        await cache.load_all()

        # First load: irn_generator v1
        module_v1 = cache.get_irn_module()
        assert module_v1.generate_irn({}) == "IRN-v1"

        # Refresh: checksum changed → reload
        result = await cache.refresh()
        assert "irn_generator" in result["modules_updated"]
        assert result["keys_updated"] is True

        # Module should be updated
        module_v2 = cache.get_irn_module()
        assert module_v2.generate_irn({}) == "IRN-v2"
        await cache.cleanup()


class TestModuleCacheCleanup:
    """Test cleanup and lifecycle."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_dir(self, cache):
        await cache.load_all()
        cache_dir = cache._cache_dir
        assert cache_dir.exists()
        await cache.cleanup()
        assert not cache_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_clears_state(self, cache):
        await cache.load_all()
        assert cache.is_loaded is True
        await cache.cleanup()
        assert cache.is_loaded is False
        assert len(cache._modules) == 0

    @pytest.mark.asyncio
    async def test_cleanup_cancels_refresh_task(self, cache):
        await cache.load_all()
        await cache.start_refresh_loop()
        assert cache._refresh_task is not None
        await cache.cleanup()
        assert cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_double_cleanup_safe(self, cache):
        """Calling cleanup twice should not raise."""
        await cache.load_all()
        await cache.cleanup()
        await cache.cleanup()  # Should not raise


class TestModuleCacheRefreshLoop:
    """Test background refresh loop."""

    @pytest.mark.asyncio
    async def test_start_refresh_loop(self, cache):
        await cache.load_all()
        await cache.start_refresh_loop()
        assert cache._refresh_task is not None
        assert not cache._refresh_task.done()
        await cache.cleanup()

    @pytest.mark.asyncio
    async def test_start_refresh_loop_idempotent(self, cache):
        """Starting the loop twice should not create a second task."""
        await cache.load_all()
        await cache.start_refresh_loop()
        task1 = cache._refresh_task
        await cache.start_refresh_loop()
        assert cache._refresh_task is task1
        await cache.cleanup()
