"""
Tests for core/validation.py — File validation
"""

import pytest

from src.config import RelayConfig
from src.core.validation import (
    validate_file_count,
    validate_file_extensions,
    validate_file_sizes,
    validate_files,
)
from src.errors import (
    FileSizeExceededError,
    InvalidFileExtensionError,
    NoFilesProvidedError,
    TooManyFilesError,
    TotalSizeExceededError,
)


class TestValidateFileCount:
    """Test file count validation."""

    def test_valid_count(self):
        validate_file_count(1, 3)
        validate_file_count(2, 3)
        validate_file_count(3, 3)

    def test_zero_files(self):
        with pytest.raises(NoFilesProvidedError):
            validate_file_count(0, 3)

    def test_too_many_files(self):
        with pytest.raises(TooManyFilesError) as exc_info:
            validate_file_count(5, 3)
        assert "5" in str(exc_info.value.message)
        assert "3" in str(exc_info.value.message)

    def test_exactly_at_limit(self):
        # Should pass — 3 out of 3
        validate_file_count(3, 3)

    def test_single_file_limit(self):
        validate_file_count(1, 1)
        with pytest.raises(TooManyFilesError):
            validate_file_count(2, 1)


class TestValidateFileExtensions:
    """Test file extension validation."""

    def test_valid_extensions(self):
        files = [
            ("invoice.pdf", b"data"),
            ("feed.xml", b"data"),
            ("data.json", b"data"),
        ]
        validate_file_extensions(files, (".pdf", ".xml", ".json"))

    def test_invalid_extension(self):
        files = [("script.exe", b"data")]
        with pytest.raises(InvalidFileExtensionError) as exc_info:
            validate_file_extensions(files, (".pdf", ".xml"))
        assert "script.exe" in str(exc_info.value.message)

    def test_case_insensitive(self):
        files = [("INVOICE.PDF", b"data"), ("Feed.XML", b"data")]
        validate_file_extensions(files, (".pdf", ".xml"))

    def test_mixed_valid_invalid(self):
        """First invalid file triggers error (fail-fast)."""
        files = [
            ("good.pdf", b"data"),
            ("bad.exe", b"data"),
            ("also_good.xml", b"data"),
        ]
        with pytest.raises(InvalidFileExtensionError) as exc_info:
            validate_file_extensions(files, (".pdf", ".xml"))
        assert "bad.exe" in str(exc_info.value.message)

    def test_no_extension(self):
        files = [("README", b"data")]
        with pytest.raises(InvalidFileExtensionError):
            validate_file_extensions(files, (".pdf",))

    def test_xlsx_allowed(self):
        files = [("report.xlsx", b"data")]
        validate_file_extensions(files, (".pdf", ".xlsx"))

    def test_csv_allowed(self):
        files = [("data.csv", b"data")]
        validate_file_extensions(files, (".csv",))

    def test_all_default_extensions(self, config):
        files = [
            ("a.pdf", b"d"),
            ("b.xml", b"d"),
            ("c.json", b"d"),
        ]
        validate_file_extensions(files, config.allowed_extensions)


class TestValidateFileSizes:
    """Test file size validation."""

    def test_valid_sizes(self):
        files = [
            ("small.pdf", b"x" * 1024),       # 1 KB
            ("medium.pdf", b"x" * (1024 * 1024)),  # 1 MB
        ]
        validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)

    def test_individual_file_too_large(self):
        # 11 MB file with 10 MB limit
        files = [("big.pdf", b"x" * (11 * 1024 * 1024))]
        with pytest.raises(FileSizeExceededError) as exc_info:
            validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)
        assert "big.pdf" in str(exc_info.value.message)

    def test_total_size_exceeded(self):
        # 3 files of 11 MB each = 33 MB, exceeds 30 MB limit
        files = [
            ("a.pdf", b"x" * (9 * 1024 * 1024)),
            ("b.pdf", b"x" * (9 * 1024 * 1024)),
            ("c.pdf", b"x" * (9 * 1024 * 1024)),
        ]
        # Individual files are under 10 MB, but total is 27 MB
        validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)

        # Now exceed total
        files_big = [
            ("a.pdf", b"x" * (10 * 1024 * 1024)),
            ("b.pdf", b"x" * (10 * 1024 * 1024)),
            ("c.pdf", b"x" * (10 * 1024 * 1024)),
            ("d.pdf", b"x" * (1 * 1024 * 1024)),
        ]
        with pytest.raises(TotalSizeExceededError):
            validate_file_sizes(files_big, max_file_size_mb=10.0, max_total_size_mb=30.0)

    def test_exactly_at_limit(self):
        # 10 MB file with 10 MB limit — should pass (not strictly greater)
        files = [("exact.pdf", b"x" * (10 * 1024 * 1024))]
        # 10.0 MB / 10.0 MB → not > 10.0, passes
        validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)

    def test_empty_file_valid(self):
        files = [("empty.pdf", b"")]
        validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)

    def test_individual_check_before_total(self):
        """Individual file check happens first (fail-fast)."""
        # One file exceeds individual limit, total would also exceed
        files = [("huge.pdf", b"x" * (50 * 1024 * 1024))]
        with pytest.raises(FileSizeExceededError):
            validate_file_sizes(files, max_file_size_mb=10.0, max_total_size_mb=30.0)


class TestValidateFiles:
    """Test combined validation function."""

    def test_all_pass(self, config, sample_files):
        validate_files(sample_files, config)

    def test_no_files(self, config):
        with pytest.raises(NoFilesProvidedError):
            validate_files([], config)

    def test_too_many(self, config):
        files = [(f"file_{i}.pdf", b"data") for i in range(5)]
        with pytest.raises(TooManyFilesError):
            validate_files(files, config)

    def test_bad_extension(self, config):
        files = [("script.py", b"data")]
        with pytest.raises(InvalidFileExtensionError):
            validate_files(files, config)

    def test_oversized(self, config, large_file):
        files = [("big.pdf", large_file)]
        with pytest.raises(FileSizeExceededError):
            validate_files(files, config)

    def test_validation_order(self, config):
        """Count is checked first, then extensions, then sizes."""
        # 5 .exe files of 50MB each — count fails first
        files = [(f"f{i}.exe", b"x" * (50 * 1024 * 1024)) for i in range(5)]
        with pytest.raises(TooManyFilesError):
            validate_files(files, config)
