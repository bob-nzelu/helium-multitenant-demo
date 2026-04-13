"""Tests for dedup checker (unit tests — hash computation only)."""

from src.ingestion.dedup import DedupChecker


class TestComputeHash:
    def test_consistent_hash(self):
        h1 = DedupChecker.compute_hash(b"hello world")
        h2 = DedupChecker.compute_hash(b"hello world")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = DedupChecker.compute_hash(b"hello")
        h2 = DedupChecker.compute_hash(b"world")
        assert h1 != h2

    def test_empty_content(self):
        h = DedupChecker.compute_hash(b"")
        assert len(h) == 64  # SHA256 hex is always 64 chars

    def test_sha256_format(self):
        h = DedupChecker.compute_hash(b"test")
        assert all(c in "0123456789abcdef" for c in h)
