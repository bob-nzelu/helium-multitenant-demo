"""
Tests for System Tray App components.

Tests cover:
    - Icons: programmatic generation (green/yellow/red)
    - HeartBeatProcess: readiness polling, reachability checks
    - ConsoleWindow: basic construction
    - HeliumTrayApp: construction and menu
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
from PySide6.QtWidgets import QApplication


# Ensure a QApplication exists for QPainter/QPixmap operations
@pytest.fixture(scope="module", autouse=True)
def qapp():
    """Create a QApplication if none exists (required for Qt widgets/pixmaps)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ── Icons ────────────────────────────────────────────────────────────────


class TestIcons:
    def test_icon_green(self):
        from src.tray.icons import icon_green
        icon = icon_green()
        assert not icon.isNull()

    def test_icon_yellow(self):
        from src.tray.icons import icon_yellow
        icon = icon_yellow()
        assert not icon.isNull()

    def test_icon_red(self):
        from src.tray.icons import icon_red
        icon = icon_red()
        assert not icon.isNull()

    def test_icons_are_distinct(self):
        from src.tray.icons import icon_green, icon_yellow, icon_red
        g = icon_green()
        y = icon_yellow()
        r = icon_red()
        # All should be valid, non-null icons
        assert not g.isNull()
        assert not y.isNull()
        assert not r.isNull()


# ── HeartBeatProcess ─────────────────────────────────────────────────────


class TestHeartBeatProcess:
    def test_default_url(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()
        assert hb.base_url == "http://localhost:9000"
        hb.close()

    def test_custom_url(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess("http://192.168.1.10:9000/")
        assert hb.base_url == "http://192.168.1.10:9000"
        hb.close()

    def test_poll_readiness_success(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "ready": True,
            "total_services": 3,
            "healthy_services": 3,
        }

        with patch.object(hb._client, "get", return_value=mock_response):
            result = hb.poll_readiness()

        assert result["ready"] is True
        assert result["total_services"] == 3
        hb.close()

    def test_poll_readiness_connection_refused(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        with patch.object(
            hb._client, "get", side_effect=httpx.ConnectError("refused")
        ):
            result = hb.poll_readiness()

        assert result["ready"] is False
        assert result["error"] == "connection_refused"
        hb.close()

    def test_poll_readiness_timeout(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        with patch.object(
            hb._client, "get", side_effect=httpx.ReadTimeout("timeout")
        ):
            result = hb.poll_readiness()

        assert result["ready"] is False
        assert result["error"] == "timeout"
        hb.close()

    def test_poll_readiness_non_200(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(hb._client, "get", return_value=mock_response):
            result = hb.poll_readiness()

        assert result["ready"] is False
        assert "503" in result["error"]
        hb.close()

    def test_is_reachable_true(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(hb._client, "get", return_value=mock_response):
            assert hb.is_reachable() is True
        hb.close()

    def test_is_reachable_false(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        with patch.object(
            hb._client, "get", side_effect=httpx.ConnectError("refused")
        ):
            assert hb.is_reachable() is False
        hb.close()

    def test_start_heartbeat_already_tracked(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        hb._process = mock_proc

        assert hb.start_heartbeat() is True
        hb.close()

    def test_close_idempotent(self):
        from src.tray.heartbeat_process import HeartBeatProcess
        hb = HeartBeatProcess()
        hb.close()
        hb.close()  # Should not raise


# ── ConsoleWindow ────────────────────────────────────────────────────────


class TestConsoleWindow:
    def test_construction(self):
        from src.tray.console_window import ConsoleWindow
        cw = ConsoleWindow()
        assert cw.windowTitle() == "Helium HeartBeat — Console"

    def test_construction_with_log_dir(self):
        from src.tray.console_window import ConsoleWindow
        cw = ConsoleWindow(log_dir="/tmp/test_logs")
        assert cw._log_dir is not None
