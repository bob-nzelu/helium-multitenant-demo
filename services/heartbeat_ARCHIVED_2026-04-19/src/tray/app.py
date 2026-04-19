"""
Helium System Tray App — Optional monitoring UI for Standard/Test tier.

NOT a lifecycle owner. HeartBeat auto-starts via Registry Run key
(Standard/Test) or NSSM service (Pro/Enterprise). This app connects
to an already-running HeartBeat and shows status.

Behavior:
    1. Connects to HeartBeat at localhost:9000/api/status/readiness
    2. Shows tray icon (green=ready, yellow=degraded, red=error)
    3. Context menu: Show Console, View Status, Start HeartBeat, Quit
    4. If HeartBeat not running: shows red icon, "HeartBeat not detected"
    5. Closing this app does NOT stop HeartBeat

Usage:
    python tray_main.py
    python -m src.tray.app
"""

import logging
import sys
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .console_window import ConsoleWindow
from .heartbeat_process import HeartBeatProcess
from .icons import icon_green, icon_red, icon_yellow

logger = logging.getLogger(__name__)

# Poll interval (ms)
POLL_INTERVAL = 10_000  # 10 seconds


class HeliumTrayApp(QApplication):
    """
    System tray monitoring app for Helium HeartBeat.

    Shows a colored circle in the system tray:
        Green  — all services healthy, platform ready
        Yellow — degraded or starting
        Red    — HeartBeat unreachable or critical failure

    Context menu provides access to:
        - Console log viewer
        - Status summary tooltip
        - Emergency HeartBeat start (test tier only)
        - Quit (tray app only — HeartBeat continues)
    """

    def __init__(self, argv=None):
        super().__init__(argv or sys.argv)
        self.setQuitOnLastWindowClosed(False)

        # HeartBeat monitor
        self._heartbeat = HeartBeatProcess()

        # Icons
        self._icon_green = icon_green()
        self._icon_yellow = icon_yellow()
        self._icon_red = icon_red()

        # System tray
        self._tray = QSystemTrayIcon(self._icon_red, self)
        self._tray.setToolTip("Helium HeartBeat — Checking...")
        self._tray.activated.connect(self._on_tray_activated)

        # Context menu
        self._menu = QMenu()
        self._build_menu()
        self._tray.setContextMenu(self._menu)

        # Console window (created on demand)
        self._console: Optional[ConsoleWindow] = None

        # Status action (updated with current state)
        self._status_action: Optional[QAction] = None

        # Poll timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_INTERVAL)

        # Show tray icon
        self._tray.show()

        # Initial poll
        QTimer.singleShot(500, self._poll)

    def _build_menu(self):
        """Build the tray context menu."""
        # Status line (disabled — just informational)
        self._status_action = QAction("Status: Checking...", self)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._menu.addSeparator()

        # Show Console
        console_action = QAction("Show Console", self)
        console_action.triggered.connect(self._show_console)
        self._menu.addAction(console_action)

        self._menu.addSeparator()

        # Start HeartBeat (emergency)
        start_action = QAction("Start HeartBeat", self)
        start_action.triggered.connect(self._start_heartbeat)
        self._menu.addAction(start_action)

        self._menu.addSeparator()

        # Quit
        quit_action = QAction("Quit Tray App", self)
        quit_action.triggered.connect(self._quit)
        self._menu.addAction(quit_action)

    def _poll(self):
        """Poll HeartBeat readiness and update tray icon."""
        result = self._heartbeat.poll_readiness()

        if "error" in result:
            # HeartBeat unreachable
            self._tray.setIcon(self._icon_red)
            error = result["error"]
            if error == "connection_refused":
                tooltip = "Helium HeartBeat — Not Running"
                status = "Status: HeartBeat not detected"
            elif error == "timeout":
                tooltip = "Helium HeartBeat — Timeout"
                status = "Status: HeartBeat not responding"
            else:
                tooltip = f"Helium HeartBeat — Error"
                status = f"Status: {error}"
        elif result.get("ready"):
            # All good
            self._tray.setIcon(self._icon_green)
            total = result.get("total_services", 0)
            healthy = result.get("healthy_services", 0)
            tooltip = f"Helium HeartBeat — Ready ({healthy}/{total} services)"
            status = f"Status: Ready ({healthy}/{total} healthy)"
        else:
            # Degraded
            self._tray.setIcon(self._icon_yellow)
            total = result.get("total_services", 0)
            healthy = result.get("healthy_services", 0)
            tooltip = f"Helium HeartBeat — Degraded ({healthy}/{total} services)"
            status = f"Status: Degraded ({healthy}/{total} healthy)"

        self._tray.setToolTip(tooltip)
        if self._status_action:
            self._status_action.setText(status)

    def _on_tray_activated(self, reason):
        """Handle tray icon click."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_console()

    def _show_console(self):
        """Show or raise the console window."""
        if self._console is None:
            self._console = ConsoleWindow()
        self._console.show()
        self._console.raise_()
        self._console.activateWindow()

    def _start_heartbeat(self):
        """Emergency start — only if HeartBeat is not running."""
        if self._heartbeat.is_reachable():
            self._tray.showMessage(
                "Helium HeartBeat",
                "HeartBeat is already running.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            return

        success = self._heartbeat.start_heartbeat()
        if success:
            self._tray.showMessage(
                "Helium HeartBeat",
                "HeartBeat starting...",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            # Poll sooner to show status update
            QTimer.singleShot(3000, self._poll)
        else:
            self._tray.showMessage(
                "Helium HeartBeat",
                "Failed to start HeartBeat. Check logs.",
                QSystemTrayIcon.MessageIcon.Critical,
                5000,
            )

    def _quit(self):
        """Quit the tray app. HeartBeat continues running."""
        self._timer.stop()
        self._heartbeat.close()
        self._tray.hide()
        if self._console:
            self._console.close()
        self.quit()


def main():
    """Entry point for the Helium system tray app."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    app = HeliumTrayApp()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
