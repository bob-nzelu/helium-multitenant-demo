"""
Console Window — Log viewer for HeartBeat and managed services.

Simple QPlainTextEdit window that shows recent log output.
Toggled from the tray context menu. Not essential — purely a
convenience for developers and admins.
"""

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QVBoxLayout

logger = logging.getLogger(__name__)

# Max lines to show in console
MAX_LINES = 500
# Refresh interval (ms)
REFRESH_INTERVAL = 3000


class ConsoleWindow(QWidget):
    """
    A simple log tail window.

    Reads the HeartBeat log file and displays the last N lines.
    Auto-refreshes every 3 seconds when visible.
    """

    def __init__(self, log_dir: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Helium HeartBeat — Console")
        self.setMinimumSize(700, 400)

        self._log_dir = Path(log_dir) if log_dir else None
        self._last_size = 0

        # Text display
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 9))
        self._text.setMaximumBlockCount(MAX_LINES)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text)

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.setInterval(REFRESH_INTERVAL)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def _get_log_file(self) -> Optional[Path]:
        """Find the most recent HeartBeat log file."""
        if self._log_dir is None:
            # Default to HeartBeat's logs directory
            default = Path(__file__).resolve().parent.parent.parent / "logs"
            if default.exists():
                self._log_dir = default

        if self._log_dir is None or not self._log_dir.exists():
            return None

        log_files = sorted(
            self._log_dir.glob("heartbeat*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return log_files[0] if log_files else None

    def _refresh(self):
        """Read new log content and append to display."""
        log_file = self._get_log_file()
        if log_file is None:
            if self._text.toPlainText() == "":
                self._text.setPlainText("No log file found. HeartBeat may not be running.")
            return

        try:
            current_size = log_file.stat().st_size
            if current_size == self._last_size:
                return  # No new content

            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                if self._last_size > 0 and current_size > self._last_size:
                    # Read only new bytes
                    f.seek(self._last_size)
                    new_text = f.read()
                else:
                    # First read or file rotated — read last portion
                    content = f.read()
                    lines = content.splitlines()
                    new_text = "\n".join(lines[-MAX_LINES:])
                    self._text.clear()

                self._last_size = current_size

                if new_text.strip():
                    self._text.appendPlainText(new_text.rstrip())
                    # Auto-scroll to bottom
                    scrollbar = self._text.verticalScrollBar()
                    scrollbar.setValue(scrollbar.maximum())

        except Exception as e:
            logger.debug(f"Console refresh error: {e}")
