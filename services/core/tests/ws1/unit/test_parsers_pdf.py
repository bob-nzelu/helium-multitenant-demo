"""Tests for PDF parser (stub)."""

import pytest

from src.ingestion.parsers.pdf_parser import PDFParser


@pytest.fixture
def parser():
    return PDFParser()


class TestPDFParser:
    @pytest.mark.asyncio
    async def test_stub_returns_raw_text(self, parser, sample_pdf_bytes):
        result = await parser.parse(sample_pdf_bytes, "invoice.pdf")
        assert result.file_type == "pdf"
        assert result.raw_data["stub"] is True
        assert "raw_text" in result.raw_data

    @pytest.mark.asyncio
    async def test_stub_red_flag(self, parser, sample_pdf_bytes):
        result = await parser.parse(sample_pdf_bytes, "invoice.pdf")
        assert len(result.red_flags) == 1
        assert "stub" in result.red_flags[0].message.lower()

    @pytest.mark.asyncio
    async def test_metadata(self, parser, sample_pdf_bytes):
        result = await parser.parse(sample_pdf_bytes, "invoice.pdf")
        assert result.metadata.parser_type == "pdf_stub"
        assert result.metadata.file_size_bytes == len(sample_pdf_bytes)
