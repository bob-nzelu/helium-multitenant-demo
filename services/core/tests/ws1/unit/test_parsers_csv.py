"""Tests for CSV parser."""

import pytest

from src.ingestion.parsers.csv_parser import CSVParser


@pytest.fixture
def parser():
    return CSVParser()


class TestCSVParser:
    @pytest.mark.asyncio
    async def test_comma_delimited(self, parser, sample_csv_bytes):
        result = await parser.parse(sample_csv_bytes, "test.csv")
        assert result.file_type == "csv"
        assert result.metadata.row_count == 2
        assert result.raw_data[0]["invoice_number"] == "INV-001"

    @pytest.mark.asyncio
    async def test_semicolon_delimited(self, parser, sample_csv_semicolon):
        result = await parser.parse(sample_csv_semicolon, "test.csv")
        assert result.metadata.row_count == 2
        assert result.raw_data[0]["invoice_number"] == "INV-001"

    @pytest.mark.asyncio
    async def test_bom_handling(self, parser):
        content = b"\xef\xbb\xbfa,b\n1,2\n"
        result = await parser.parse(content, "bom.csv")
        assert result.metadata.row_count == 1
        assert "a" in result.raw_data[0]

    @pytest.mark.asyncio
    async def test_hdx_tag_rows_skipped(self, parser, sample_csv_with_hdx):
        result = await parser.parse(sample_csv_with_hdx, "wfp.csv")
        assert result.metadata.row_count == 1
        assert result.raw_data[0]["invoice_number"] == "INV-001"

    @pytest.mark.asyncio
    async def test_empty_csv(self, parser):
        result = await parser.parse(b"", "empty.csv")
        assert result.metadata.row_count == 0
        assert len(result.red_flags) > 0

    @pytest.mark.asyncio
    async def test_latin1_encoding(self, parser):
        content = "name,city\nJosé,São Paulo\n".encode("latin-1")
        result = await parser.parse(content, "latin.csv")
        assert result.metadata.row_count == 1
        assert len(result.red_flags) > 0  # encoding warning

    @pytest.mark.asyncio
    async def test_values_are_strings(self, parser, sample_csv_bytes):
        """CSV values are always strings."""
        result = await parser.parse(sample_csv_bytes, "test.csv")
        assert result.raw_data[0]["amount"] == "1500.00"
