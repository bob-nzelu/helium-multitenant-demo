"""Tests for HLM and HLMZ parsers."""

import gzip
import json

import pytest

from src.errors import ValidationError
from src.ingestion.parsers.hlm_parser import HLMParser
from src.ingestion.parsers.hlmz_parser import HLMZParser


class TestHLMParser:
    @pytest.fixture
    def parser(self):
        return HLMParser()

    @pytest.mark.asyncio
    async def test_valid_hlm(self, parser, sample_hlm_bytes):
        result = await parser.parse(sample_hlm_bytes, "invoice.hlm")
        assert result.file_type == "hlm"
        assert result.is_hlm is True
        assert result.raw_data["hlm_version"] == "1.0"

    @pytest.mark.asyncio
    async def test_missing_hlm_version(self, parser):
        content = json.dumps({"data": "test"}).encode()
        with pytest.raises(ValidationError, match="hlm_version"):
            await parser.parse(content, "bad.hlm")

    @pytest.mark.asyncio
    async def test_invalid_json(self, parser):
        with pytest.raises(ValidationError):
            await parser.parse(b"not json", "bad.hlm")

    @pytest.mark.asyncio
    async def test_non_object(self, parser):
        with pytest.raises(ValidationError):
            await parser.parse(b"[1,2,3]", "array.hlm")


class TestHLMZParser:
    @pytest.fixture
    def parser(self):
        return HLMZParser()

    @pytest.mark.asyncio
    async def test_valid_hlmz(self, parser, sample_hlmz_bytes):
        result = await parser.parse(sample_hlmz_bytes, "invoice.hlmz")
        assert result.file_type == "hlmz"
        assert result.is_hlm is True
        assert result.metadata.parser_type == "hlmz"

    @pytest.mark.asyncio
    async def test_invalid_gzip(self, parser):
        with pytest.raises(ValidationError, match="gzip"):
            await parser.parse(b"not gzip data", "bad.hlmz")

    @pytest.mark.asyncio
    async def test_compressed_size_in_metadata(self, parser, sample_hlmz_bytes):
        result = await parser.parse(sample_hlmz_bytes, "invoice.hlmz")
        assert result.metadata.file_size_bytes == len(sample_hlmz_bytes)
