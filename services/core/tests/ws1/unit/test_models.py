"""Tests for WS1 ingestion models."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.ingestion.models import (
    DedupResult,
    EnqueueRequest,
    EnqueueResponse,
    FileType,
    ParseMetadata,
    ParseResult,
    RedFlag,
)


class TestFileType:
    def test_all_values(self):
        assert FileType.EXCEL.value == "excel"
        assert FileType.HLM.value == "hlm"
        assert FileType.HLMZ.value == "hlmz"

    def test_string_enum(self):
        assert str(FileType.PDF) == "FileType.PDF"


class TestParseResult:
    def test_create_minimal(self):
        meta = ParseMetadata(parser_type="test", original_filename="x.csv")
        result = ParseResult(file_type="csv", raw_data=[], metadata=meta)
        assert result.is_hlm is False
        assert result.file_hash == ""
        assert result.red_flags == []

    def test_with_red_flags(self):
        meta = ParseMetadata(parser_type="test", original_filename="x.csv")
        flags = [RedFlag(field_name="a", message="bad")]
        result = ParseResult(file_type="csv", raw_data=[], metadata=meta, red_flags=flags)
        assert len(result.red_flags) == 1

    def test_hlm_flag(self):
        meta = ParseMetadata(parser_type="hlm", original_filename="x.hlm")
        result = ParseResult(file_type="hlm", raw_data={}, metadata=meta, is_hlm=True)
        assert result.is_hlm is True


class TestDedupResult:
    def test_not_duplicate(self):
        r = DedupResult(is_duplicate=False, file_hash="abc123")
        assert r.existing_queue_id is None

    def test_duplicate(self):
        r = DedupResult(is_duplicate=True, file_hash="abc", existing_queue_id="q-1")
        assert r.is_duplicate


class TestEnqueueRequest:
    def test_valid(self):
        req = EnqueueRequest(
            blob_uuid="test-uuid",
            data_uuid="data-uuid",
            original_filename="invoice.xlsx",
            company_id="tenant-1",
        )
        assert req.priority == 3

    def test_priority_bounds(self):
        with pytest.raises(PydanticValidationError):
            EnqueueRequest(
                blob_uuid="u", data_uuid="d", original_filename="f",
                company_id="c", priority=0,
            )
        with pytest.raises(PydanticValidationError):
            EnqueueRequest(
                blob_uuid="u", data_uuid="d", original_filename="f",
                company_id="c", priority=6,
            )

    def test_missing_required(self):
        with pytest.raises(PydanticValidationError):
            EnqueueRequest(blob_uuid="u", data_uuid="d")  # type: ignore
