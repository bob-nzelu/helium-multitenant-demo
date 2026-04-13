"""
Tests for WS7 PDF Renderer — weasyprint fallback behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.reports.pdf_renderer import is_weasyprint_available, render_pdf


class TestPdfRenderer:
    def test_render_returns_bytes(self):
        """render_pdf should always return bytes (HTML fallback if no weasyprint)."""
        result = render_pdf("<html><body>Test</body></html>")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_html_content_preserved_in_fallback(self):
        """Without weasyprint, the HTML content should be returned as-is."""
        html = "<h1>Compliance Report</h1><p>Score: 95%</p>"
        # Force weasyprint unavailable
        with patch("src.reports.pdf_renderer._WEASYPRINT_AVAILABLE", False):
            result = render_pdf(html)
        assert result == html.encode("utf-8")
        assert b"Compliance Report" in result
        assert b"95%" in result

    def test_is_weasyprint_available_returns_bool(self):
        result = is_weasyprint_available()
        assert isinstance(result, bool)

    def test_unicode_content(self):
        """Naira symbol and special characters should be preserved."""
        html = "<p>Total: \u20a6100,000.00</p>"
        with patch("src.reports.pdf_renderer._WEASYPRINT_AVAILABLE", False):
            result = render_pdf(html)
        assert "\u20a6".encode("utf-8") in result

    def test_empty_html(self):
        with patch("src.reports.pdf_renderer._WEASYPRINT_AVAILABLE", False):
            result = render_pdf("")
        assert result == b""
