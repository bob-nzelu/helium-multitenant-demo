"""
WS7: PDF Renderer — thin weasyprint wrapper with graceful fallback.

Production (Docker): weasyprint + GTK/Cairo installed → real PDF output.
Dev/Test (no Docker): weasyprint unavailable → returns raw HTML bytes.

Tests should verify HTML content, not PDF rendering.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

_WEASYPRINT_AVAILABLE: bool | None = None


def is_weasyprint_available() -> bool:
    """Check if weasyprint can be imported (cached after first call)."""
    global _WEASYPRINT_AVAILABLE
    if _WEASYPRINT_AVAILABLE is None:
        try:
            import weasyprint  # noqa: F401
            _WEASYPRINT_AVAILABLE = True
        except ImportError:
            _WEASYPRINT_AVAILABLE = False
    return _WEASYPRINT_AVAILABLE


def render_pdf(html_content: str) -> bytes:
    """
    Convert HTML string to PDF bytes.

    Falls back to UTF-8 encoded HTML if weasyprint is not installed.
    The fallback is intentional for dev/test environments without
    GTK/Cairo system dependencies.
    """
    if is_weasyprint_available():
        from weasyprint import HTML
        return HTML(string=html_content).write_pdf()

    logger.warning("weasyprint_not_available_returning_html")
    return html_content.encode("utf-8")
