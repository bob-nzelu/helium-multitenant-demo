"""Tests for JSON parser."""

import pytest

from src.errors import ValidationError
from src.ingestion.parsers.json_parser import JSONParser


@pytest.fixture
def parser():
    return JSONParser()


class TestJSONParser:
    @pytest.mark.asyncio
    async def test_array(self, parser, sample_json_bytes):
        result = await parser.parse(sample_json_bytes, "data.json")
        assert result.file_type == "json"
        assert result.metadata.row_count == 2

    @pytest.mark.asyncio
    async def test_single_object(self, parser, sample_json_single):
        result = await parser.parse(sample_json_single, "single.json")
        assert result.metadata.row_count == 1
        assert result.raw_data[0]["invoice_number"] == "INV-001"

    @pytest.mark.asyncio
    async def test_bom_handling(self, parser):
        content = b"\xef\xbb\xbf" + b'[{"x": 1}]'
        result = await parser.parse(content, "bom.json")
        assert result.metadata.row_count == 1

    @pytest.mark.asyncio
    async def test_invalid_json(self, parser):
        with pytest.raises(ValidationError):
            await parser.parse(b"{not json}", "bad.json")

    @pytest.mark.asyncio
    async def test_non_object_root(self, parser):
        with pytest.raises(ValidationError):
            await parser.parse(b'"just a string"', "str.json")

    @pytest.mark.asyncio
    async def test_empty_array(self, parser):
        result = await parser.parse(b"[]", "empty.json")
        assert result.metadata.row_count == 0
