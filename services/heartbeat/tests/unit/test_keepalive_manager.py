"""
Tests for KeepAliveManager — lifecycle orchestrator for child services.

Tests cover:
    - start() with priority-ordered startup
    - stop() with reverse-priority shutdown
    - start_service() / stop_service() / restart_service()
    - get_status()
    - _monitor_loop() PID checks + health polling
    - _handle_service_failure() restart policy + crash loop detection
    - Singleton get/reset
"""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.keepalive.manager import (
    KeepAliveManager,
    get_keepalive_manager,
    reset_keepalive_manager,
    CRASH_LOOP_MAX_RESTARTS,
    CRASH_LOOP_WINDOW,
    RESTART_DELAYS,
)
from src.keepalive.process_handle import ProcessHandle


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset KeepAliveManager singleton between tests."""
    reset_keepalive_manager()
    yield
    reset_keepalive_manager()


@pytest.fixture
def manager():
    return KeepAliveManager()


@pytest.fixture
def mock_handle():
    h = MagicMock(spec=ProcessHandle)
    h.service_name = "core"
    h.pid = 1234
    h.status = "healthy"
    h.health_endpoint = "http://localhost:8000/health"
    h.restart_count = 0
    h.is_alive.return_value = True
    h.last_started_at = datetime.now(timezone.utc)
    h.start = AsyncMock(return_value=1234)
    h.stop = AsyncMock()
    h.to_dict.return_value = {
        "service_name": "core",
        "pid": 1234,
        "status": "healthy",
        "restart_count": 0,
    }
    return h


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_keepalive_manager()
        b = get_keepalive_manager()
        assert a is b

    def test_reset_clears_instance(self):
        a = get_keepalive_manager()
        reset_keepalive_manager()
        b = get_keepalive_manager()
        assert a is not b


class TestStart:
    @pytest.mark.asyncio
    async def test_start_no_services(self, manager):
        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_services.return_value = []
            await manager.start()

        assert manager._running is True
        assert manager._monitor_task is not None
        # Cancel the monitor task
        manager._running = False
        manager._monitor_task.cancel()
        try:
            await manager._monitor_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_priority_ordering(self, manager):
        services = [
            {
                "service_name": "edge",
                "instance_id": "edge-001",
                "executable_path": "/usr/bin/python",
                "working_directory": "/opt",
                "arguments": None,
                "environment": None,
                "health_endpoint": None,
                "startup_priority": 3,
                "restart_count": 0,
                "current_status": "stopped",
            },
            {
                "service_name": "core",
                "instance_id": "core-001",
                "executable_path": "/usr/bin/python",
                "working_directory": "/opt",
                "arguments": None,
                "environment": None,
                "health_endpoint": None,
                "startup_priority": 1,
                "restart_count": 0,
                "current_status": "stopped",
            },
        ]

        started_order = []

        async def track_start(handle):
            started_order.append(handle.service_name)
            return 100

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_services.return_value = services
            mock_db.return_value.mark_service_started.return_value = 1

            with patch.object(manager, "_start_single", side_effect=track_start):
                await manager.start()

        # Core (priority 1) should start before Edge (priority 3)
        assert started_order == ["core", "edge"]

        # Cleanup
        manager._running = False
        manager._monitor_task.cancel()
        try:
            await manager._monitor_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_start_handles_failure(self, manager):
        services = [
            {
                "service_name": "broken",
                "instance_id": "broken-001",
                "executable_path": "/nonexistent",
                "working_directory": "/opt",
                "arguments": None,
                "environment": None,
                "health_endpoint": None,
                "startup_priority": 1,
                "restart_count": 0,
                "current_status": "stopped",
            },
        ]

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_services.return_value = services
            mock_db.return_value.mark_service_started.return_value = 1

            with patch.object(
                manager, "_start_single", side_effect=OSError("not found")
            ):
                await manager.start()

        # Should still be running (errors are non-fatal)
        assert manager._running is True
        manager._running = False
        manager._monitor_task.cancel()
        try:
            await manager._monitor_task
        except asyncio.CancelledError:
            pass


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_empty(self, manager):
        manager._running = True
        await manager.stop()
        assert manager._running is False

    @pytest.mark.asyncio
    async def test_stop_with_handles(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}
        manager._running = True
        manager._monitor_task = asyncio.create_task(asyncio.sleep(999))

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "startup_priority": 1,
            }
            mock_db.return_value.mark_service_stopped.return_value = 1

            await manager.stop()

        mock_handle.stop.assert_called_once()
        assert len(manager._handles) == 0
        assert manager._running is False


class TestServiceOperations:
    @pytest.mark.asyncio
    async def test_start_service_already_running(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}
        result = await manager.start_service("core")
        assert result["status"] == "already_running"

    @pytest.mark.asyncio
    async def test_start_service_from_db(self, manager):
        svc_row = {
            "service_name": "relay",
            "instance_id": "relay-001",
            "executable_path": "/usr/bin/python",
            "working_directory": "/opt",
            "arguments": None,
            "environment": None,
            "health_endpoint": "http://localhost:8001/health",
            "restart_count": 0,
            "current_status": "stopped",
        }

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = svc_row
            mock_db.return_value.mark_service_started.return_value = 1

            with patch("src.keepalive.manager.get_config") as mock_config:
                mock_config.return_value.get_log_dir.return_value = "/tmp/logs"

                with patch(
                    "src.keepalive.process_handle.ProcessHandle.start",
                    new_callable=AsyncMock,
                    return_value=5678,
                ):
                    with patch(
                        "src.keepalive.process_handle.ProcessHandle.is_alive",
                        return_value=False,
                    ):
                        result = await manager.start_service("relay")

        assert result["status"] == "started"
        assert result["pid"] == 5678

    @pytest.mark.asyncio
    async def test_start_service_unknown(self, manager):
        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = None

            with patch("src.keepalive.manager.get_config") as mock_config:
                mock_config.return_value.get_log_dir.return_value = "/tmp"

                result = await manager.start_service("nonexistent")

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_stop_service(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.mark_service_stopped.return_value = 1
            result = await manager.stop_service("core")

        assert result["status"] == "stopped"
        mock_handle.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_service_unknown(self, manager):
        result = await manager.stop_service("nonexistent")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_restart_service(self, manager, mock_handle):
        mock_handle.is_alive.return_value = False
        manager._handles = {"core": mock_handle}

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.mark_service_stopped.return_value = 1
            mock_db.return_value.mark_service_started.return_value = 1
            result = await manager.restart_service("core")

        assert result["status"] == "started"


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_status_empty(self, manager):
        result = await manager.get_status()
        assert result["total"] == 0
        assert result["healthy"] == 0

    @pytest.mark.asyncio
    async def test_status_with_services(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}
        result = await manager.get_status()
        assert result["total"] == 1
        assert result["healthy"] == 1
        assert "core" in result["services"]


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_restart_with_delay(self, manager, mock_handle):
        mock_handle.restart_count = 1  # Second attempt → 10s delay
        mock_handle.is_alive.return_value = False
        manager._handles = {"core": mock_handle}

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "auto_restart": True,
                "restart_policy": "immediate_3",
            }
            mock_db.return_value.increment_restart_count.return_value = 1
            mock_db.return_value.mark_service_started.return_value = 1

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await manager._handle_service_failure("core")

            # Should have slept for 10 seconds (RESTART_DELAYS[1])
            mock_sleep.assert_called_with(RESTART_DELAYS[1])

    @pytest.mark.asyncio
    async def test_no_restart_policy_none(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "auto_restart": True,
                "restart_policy": "none",
            }

            await manager._handle_service_failure("core")

        # Should not have called start
        mock_handle.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_restart_auto_restart_false(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "auto_restart": False,
                "restart_policy": "immediate_3",
            }

            await manager._handle_service_failure("core")

        mock_handle.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_crash_loop_detection(self, manager, mock_handle):
        mock_handle.restart_count = 0
        mock_handle.is_alive.return_value = False
        manager._handles = {"core": mock_handle}

        # Simulate many recent restarts
        now = time.time()
        manager._restart_history["core"] = [now - i for i in range(CRASH_LOOP_MAX_RESTARTS)]

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "auto_restart": True,
                "restart_policy": "immediate_3",
            }
            mock_db.return_value.update_service_status.return_value = 1

            await manager._handle_service_failure("core")

        assert mock_handle.status == "crash_loop"
        assert "core" in manager._crash_loop_paused

    @pytest.mark.asyncio
    async def test_crash_loop_pause_respected(self, manager, mock_handle):
        manager._handles = {"core": mock_handle}
        manager._crash_loop_paused["core"] = time.time() + 9999  # Far future

        with patch("src.keepalive.manager.get_registry_database") as mock_db:
            mock_db.return_value.get_managed_service.return_value = {
                "auto_restart": True,
                "restart_policy": "immediate_3",
            }

            await manager._handle_service_failure("core")

        # Should not restart — still in pause
        mock_handle.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_unknown_service(self, manager):
        # Should not raise
        await manager._handle_service_failure("nonexistent")
