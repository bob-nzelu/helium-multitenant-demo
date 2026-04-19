"""
Tray Icons — Programmatic QPainter-generated tray icons.

Generates 3 status icons (green/yellow/red circles) without bundling
image assets. Each icon is a 64x64 QPixmap with a filled circle and
subtle border.

Status mapping:
    green  — all services healthy, platform ready
    yellow — degraded, some services unhealthy or starting
    red    — error, HeartBeat unreachable or critical failure
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap


# Icon size in pixels
ICON_SIZE = 64


def _make_circle_icon(fill_color: str, border_color: str) -> QIcon:
    """
    Create a circular tray icon with fill and border.

    Args:
        fill_color: CSS color for the circle fill.
        border_color: CSS color for the circle border.

    Returns:
        QIcon with the drawn circle.
    """
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # Border
    painter.setPen(QColor(border_color))
    painter.setBrush(QColor(fill_color))

    margin = 4
    painter.drawEllipse(margin, margin, ICON_SIZE - 2 * margin, ICON_SIZE - 2 * margin)

    painter.end()
    return QIcon(pixmap)


def icon_green() -> QIcon:
    """Green circle — all services healthy."""
    return _make_circle_icon("#4CAF50", "#388E3C")


def icon_yellow() -> QIcon:
    """Yellow circle — degraded / starting."""
    return _make_circle_icon("#FFC107", "#FFA000")


def icon_red() -> QIcon:
    """Red circle — error / unreachable."""
    return _make_circle_icon("#F44336", "#D32F2F")
