"""
Coverage tests for src/core/module_cache.py.

Targets uncovered lines: 177-178 (refresh exception), 190-191 (refresh loop body),
215-216 (cleanup rmtree OSError), 237 (cache dir not initialized).
"""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.stub_heartbeat import StubHeartBeatClient
from src.core.module_cache import TransformaModuleCache, ModuleCacheError


@pytest.fixture
def heartbeat():
    return StubHeartBeatClient(
        heartbeat_api_url="http://localhost:9000",
        timeout=5.0,
    )


@pytest.fixture
def module_cache(heartbeat):
    return TransformaModuleCache(
        heartbeat,
        refresh_interval_s=1,  # Short for testing
    )


# ── refresh exception (lines 177-178) ────────────────────────────────────


class TestRefreshException:
    """Cover refresh() when HeartBeat is unreachable."""

    @pytest.mark.asyncio
    async def test_refresh_handles_heartbeat_failure(self, module_cache):
        """When get_transforma_config raises, refresh catches and returns."""
        with patch.object(
            module_cache._heartbeat,
            "get_transforma_config",
            side_effect=RuntimeError("HeartBeat unreachable"),
        ):
            result = await module_cache.refresh()

        # Should return without crashing
        assert result["modules_updated"] == []
        assert result["keys_updated"] is False


# ── start_refresh_loop body (lines 190-191) ──────────────────────────────


class TestRefreshLoop:
    """Cover the refresh loop body."""

    @pytest.mark.asyncio
    async def test_refresh_loop_runs_and_can_be_cancelled(self, module_cache):
        """Start refresh loop, let it tick once, then cleanup."""
        # First load to set _loaded = True
        await module_cache.load_all()

        # Start the loop (1-second interval)
        await module_cache.start_refresh_loop()
        assert module_cache._refresh_task is not None

        # Let it tick (the loop sleeps then calls refresh)
        await asyncio.sleep(1.5)

        # Cleanup cancels the task
        await module_cache.cleanup()
        assert module_cache._refresh_task is None

    @pytest.mark.asyncio
    async def test_start_refresh_loop_noop_if_already_running(self, module_cache):
        """Second call to start_refresh_loop does nothing."""
        await module_cache.load_all()
        await module_cache.start_refresh_loop()
        task1 = module_cache._refresh_task

        await module_cache.start_refresh_loop()  # Should be noop
        assert module_cache._refresh_task is task1

        await module_cache.cleanup()


# ── cleanup rmtree OSError (lines 215-216) ────────────────────────────────


class TestCleanupOSError:
    """Cover cleanup when rmtree fails."""

    @pytest.mark.asyncio
    async def test_cleanup_handles_rmtree_failure(self, module_cache):
        """When shutil.rmtree raises OSError, cleanup logs warning but continues."""
        await module_cache.load_all()

        # Ensure cache dir exists
        assert module_cache._cache_dir is not None
        assert module_cache._cache_dir.exists()

        with patch("shutil.rmtree", side_effect=OSError("permission denied")):
            # Should not raise
            await module_cache.cleanup()

        # State should still be cleared
        assert module_cache._loaded is False
        assert module_cache._service_keys is None


# ── _load_module_from_info without cache dir (line 237) ───────────────────


class TestLoadModuleNoCacheDir:
    """Cover _load_module_from_info when cache_dir is None."""

    def test_raises_when_cache_dir_is_none(self, module_cache):
        """Calling _load_module_from_info without cache dir raises ModuleCacheError."""
        # Before load_all(), _cache_dir is None
        assert module_cache._cache_dir is None

        with pytest.raises(ModuleCacheError, match="not initialized"):
            module_cache._load_module_from_info({
                "module_name": "test_module",
                "source_code": "x = 1",
                "version": "1.0",
                "checksum": "abc",
            })
