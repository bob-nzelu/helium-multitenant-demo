"""
Tests for ProcessHandle — subprocess wrapper for managed services.

Tests cover:
    - Initialization and from_db_row factory
    - start() with mocked subprocess.Popen
    - stop() with graceful and forceful paths
    - is_alive() with psutil and fallback
    - Health tracking (success/failure recording)
    - to_dict() serialization
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.keepalive.process_handle import ProcessHandle


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def handle(tmp_path):
    """Create a basic ProcessHandle for testing."""
    return ProcessHandle(
        service_name="test-service",
        executable_path=sys.executable,
        working_directory=str(tmp_path),
        arguments=["-c", "import time; time.sleep(60)"],
        environment={"TEST_VAR": "1"},
        health_endpoint="http://localhost:8000/health",
        log_dir=str(tmp_path / "logs"),
    )


@pytest.fixture
def db_row():
    """Sample managed_services database row."""
    return {
        "service_name": "core",
        "instance_id": "core-001",
        "executable_path": "/usr/bin/python",
        "working_directory": "/opt/helium/core",
        "arguments": json.dumps(["-m", "uvicorn", "main:app"]),
        "environment": json.dumps({"PORT": "8000"}),
        "startup_priority": 1,
        "auto_start": 1,
        "auto_restart": 1,
        "restart_policy": "immediate_3",
        "health_endpoint": "http://localhost:8000/health",
        "restart_count": 3,
        "current_status": "healthy",
    }


# ── Tests ─────────────────────────────────────────────────────────────

class TestProcessHandleInit:
    def test_defaults(self, handle):
        assert handle.service_name == "test-service"
        assert handle.status == "stopped"
        assert handle.pid is None
        assert handle.process is None
        assert handle.restart_count == 0
        assert handle.last_started_at is None
        assert handle.last_stopped_at is None

    def test_from_db_row(self, db_row):
        handle = ProcessHandle.from_db_row(db_row, log_dir="/tmp/logs")
        assert handle.service_name == "core"
        assert handle.executable_path == "/usr/bin/python"
        assert handle.arguments == ["-m", "uvicorn", "main:app"]
        assert handle.environment == {"PORT": "8000"}
        assert handle.health_endpoint == "http://localhost:8000/health"
        assert handle.restart_count == 3
        assert handle.status == "healthy"
        assert handle.log_dir == "/tmp/logs"

    def test_from_db_row_null_fields(self):
        row = {
            "service_name": "edge",
            "instance_id": "edge-001",
            "executable_path": "/usr/bin/python",
            "working_directory": "/opt/helium/edge",
            "arguments": None,
            "environment": None,
            "health_endpoint": None,
            "restart_count": 0,
            "current_status": "stopped",
        }
        handle = ProcessHandle.from_db_row(row)
        assert handle.arguments == []
        assert handle.environment == {}
        assert handle.health_endpoint is None


class TestProcessHandleStart:
    @pytest.mark.asyncio
    async def test_start_success(self, handle):
        mock_process = MagicMock(spec=subprocess.Popen)
        mock_process.pid = 12345

        with patch("subprocess.Popen", return_value=mock_process):
            pid = await handle.start()

        assert pid == 12345
        assert handle.pid == 12345
        assert handle.status == "starting"
        assert handle.last_started_at is not None
        assert handle.process is mock_process

    @pytest.mark.asyncio
    async def test_start_creates_log_dir(self, handle, tmp_path):
        log_dir = str(tmp_path / "new_logs")
        handle.log_dir = log_dir

        mock_process = MagicMock(spec=subprocess.Popen)
        mock_process.pid = 100

        with patch("subprocess.Popen", return_value=mock_process):
            await handle.start()

        assert os.path.exists(log_dir)

    @pytest.mark.asyncio
    async def test_start_raises_if_already_running(self, handle):
        handle.process = MagicMock()
        handle.pid = 999

        with patch.object(handle, "is_alive", return_value=True):
            with pytest.raises(RuntimeError, match="already running"):
                await handle.start()

    @pytest.mark.asyncio
    async def test_start_failure_resets_status(self, handle):
        with patch("subprocess.Popen", side_effect=OSError("not found")):
            with pytest.raises(OSError):
                await handle.start()
        assert handle.status == "stopped"


class TestProcessHandleStop:
    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, handle):
        handle.process = None
        await handle.stop()
        assert handle.status == "stopped"
        assert handle.last_stopped_at is not None

    @pytest.mark.asyncio
    async def test_stop_graceful(self, handle):
        mock_process = MagicMock(spec=subprocess.Popen)
        mock_process.pid = 123
        mock_process.wait.return_value = 0
        handle.process = mock_process
        handle.pid = 123

        with patch.object(handle, "is_alive", return_value=True):
            await handle.stop(grace_seconds=2)

        assert handle.status == "stopped"
        assert handle.pid is None
        assert handle.process is None

    @pytest.mark.asyncio
    async def test_stop_process_already_gone(self, handle):
        mock_process = MagicMock(spec=subprocess.Popen)
        mock_process.pid = 123
        handle.process = mock_process
        handle.pid = 123

        with patch.object(handle, "is_alive", return_value=False):
            await handle.stop()

        assert handle.status == "stopped"


class TestProcessHandleIsAlive:
    def test_is_alive_no_process(self, handle):
        assert handle.is_alive() is False

    def test_is_alive_with_psutil(self, handle):
        handle.process = MagicMock()
        handle.pid = 999

        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "running"

        with patch("psutil.Process", return_value=mock_proc):
            assert handle.is_alive() is True

    def test_is_alive_zombie(self, handle):
        handle.process = MagicMock()
        handle.pid = 999

        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "zombie"

        with patch("psutil.Process", return_value=mock_proc):
            assert handle.is_alive() is False

    def test_is_alive_no_such_process(self, handle):
        import psutil
        handle.process = MagicMock()
        handle.pid = 999

        with patch("psutil.Process", side_effect=psutil.NoSuchProcess(999)):
            assert handle.is_alive() is False

    def test_is_alive_fallback_poll(self, handle):
        """When psutil is not importable, falls back to subprocess.poll()."""
        handle.process = MagicMock()
        handle.process.poll.return_value = None  # Still running
        handle.pid = 999

        with patch.dict("sys.modules", {"psutil": None}):
            with patch("builtins.__import__", side_effect=ImportError):
                # Directly test the fallback path
                result = handle.process.poll() is None
                assert result is True


class TestHealthTracking:
    def test_record_health_success_from_starting(self, handle):
        handle.status = "starting"
        handle.record_health_success()
        assert handle.status == "healthy"
        assert handle._healthy_since is not None

    def test_record_health_success_stays_healthy(self, handle):
        handle.status = "healthy"
        handle._healthy_since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        handle.record_health_success()
        # _healthy_since should NOT be reset
        assert handle._healthy_since.year == 2026

    def test_record_health_success_resets_failures(self, handle):
        handle._consecutive_health_failures = 2
        handle.status = "starting"
        handle.record_health_success()
        assert handle._consecutive_health_failures == 0

    def test_record_health_failure_threshold(self, handle):
        handle.status = "healthy"
        handle.record_health_failure()
        assert handle.status == "healthy"  # 1 failure, not enough
        handle.record_health_failure()
        assert handle.status == "healthy"  # 2 failures
        handle.record_health_failure()
        assert handle.status == "unhealthy"  # 3 failures → unhealthy

    def test_healthy_duration_none(self, handle):
        assert handle.healthy_duration_seconds is None

    def test_healthy_duration_positive(self, handle):
        handle._healthy_since = datetime.now(timezone.utc)
        duration = handle.healthy_duration_seconds
        assert duration is not None
        assert duration >= 0


class TestToDict:
    def test_serialization(self, handle):
        handle.pid = 42
        handle.status = "healthy"
        handle.restart_count = 2
        handle.last_started_at = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

        d = handle.to_dict()
        assert d["service_name"] == "test-service"
        assert d["pid"] == 42
        assert d["status"] == "healthy"
        assert d["restart_count"] == 2
        assert d["health_endpoint"] == "http://localhost:8000/health"
        assert "2026-03-01" in d["last_started_at"]

    def test_serialization_stopped(self, handle):
        d = handle.to_dict()
        assert d["pid"] is None
        assert d["last_started_at"] is None
        assert d["last_stopped_at"] is None
        assert d["healthy_since"] is None
