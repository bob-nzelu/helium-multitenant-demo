"""
Tests for core/dedup.py — Two-level deduplication
"""

import hashlib
import pytest

from src.core.dedup import DedupChecker, DedupResult


class TestDedupResult:
    """Test DedupResult dataclass."""

    def test_not_duplicate(self):
        r = DedupResult(is_duplicate=False, file_hash="abc")
        assert r.is_duplicate is False
        assert r.source == ""
        assert r.original_queue_id is None

    def test_session_duplicate(self):
        r = DedupResult(is_duplicate=True, file_hash="abc", source="session")
        assert r.is_duplicate is True
        assert r.source == "session"

    def test_heartbeat_duplicate(self):
        r = DedupResult(
            is_duplicate=True,
            file_hash="abc",
            source="heartbeat",
            original_queue_id="q-123",
        )
        assert r.original_queue_id == "q-123"


class TestDedupCheckerComputeHash:
    """Test hash computation."""

    def test_sha256_hex(self):
        data = b"test file content"
        expected = hashlib.sha256(data).hexdigest()
        assert DedupChecker.compute_hash(data) == expected

    def test_empty_data(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert DedupChecker.compute_hash(b"") == expected

    def test_deterministic(self):
        data = b"same content"
        assert DedupChecker.compute_hash(data) == DedupChecker.compute_hash(data)

    def test_different_data_different_hash(self):
        h1 = DedupChecker.compute_hash(b"file_a")
        h2 = DedupChecker.compute_hash(b"file_b")
        assert h1 != h2


class TestDedupCheckerSessionCache:
    """Test Level 1: session cache."""

    @pytest.mark.asyncio
    async def test_no_duplicate_first_check(self):
        checker = DedupChecker()
        result = await checker.check(b"unique file")
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_session_duplicate_after_record(self):
        checker = DedupChecker()
        data = b"same file"

        # First check
        result1 = await checker.check(data)
        assert result1.is_duplicate is False

        # Record it
        checker.record(result1.file_hash)

        # Second check — duplicate
        result2 = await checker.check(data)
        assert result2.is_duplicate is True
        assert result2.source == "session"

    @pytest.mark.asyncio
    async def test_different_files_not_duplicate(self):
        checker = DedupChecker()

        r1 = await checker.check(b"file_a")
        checker.record(r1.file_hash)

        r2 = await checker.check(b"file_b")
        assert r2.is_duplicate is False

    @pytest.mark.asyncio
    async def test_session_size(self):
        checker = DedupChecker()
        assert checker.session_size == 0

        r = await checker.check(b"data")
        checker.record(r.file_hash)
        assert checker.session_size == 1

    @pytest.mark.asyncio
    async def test_clear_session(self):
        checker = DedupChecker()
        r = await checker.check(b"data")
        checker.record(r.file_hash)
        assert checker.session_size == 1

        checker.clear()
        assert checker.session_size == 0

        # After clear, same data is not a duplicate
        r2 = await checker.check(b"data")
        assert r2.is_duplicate is False


class TestDedupCheckerHeartBeat:
    """Test Level 2: HeartBeat persistent check."""

    @pytest.mark.asyncio
    async def test_heartbeat_not_duplicate(self, heartbeat_client):
        checker = DedupChecker(heartbeat_client=heartbeat_client, trace_id="test")
        result = await checker.check(b"new file")
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_heartbeat_duplicate_found(self):
        """Mock HeartBeat returning a duplicate."""

        class MockHeartBeat:
            async def check_duplicate(self, file_hash):
                return {
                    "is_duplicate": True,
                    "file_hash": file_hash,
                    "original_queue_id": "q-original-001",
                }

        checker = DedupChecker(heartbeat_client=MockHeartBeat(), trace_id="test")
        result = await checker.check(b"already processed file")

        assert result.is_duplicate is True
        assert result.source == "heartbeat"
        assert result.original_queue_id == "q-original-001"

    @pytest.mark.asyncio
    async def test_heartbeat_unavailable_graceful_degradation(self):
        """When HeartBeat is down, allow the upload."""

        class BrokenHeartBeat:
            async def check_duplicate(self, file_hash):
                raise ConnectionError("HeartBeat is down")

        checker = DedupChecker(heartbeat_client=BrokenHeartBeat(), trace_id="test")
        result = await checker.check(b"file data")

        # Should pass through (not a duplicate)
        assert result.is_duplicate is False

    @pytest.mark.asyncio
    async def test_session_cache_checked_before_heartbeat(self):
        """Session cache should short-circuit before HeartBeat call."""

        call_count = 0

        class TrackingHeartBeat:
            async def check_duplicate(self, file_hash):
                nonlocal call_count
                call_count += 1
                return {"is_duplicate": False, "file_hash": file_hash}

        checker = DedupChecker(heartbeat_client=TrackingHeartBeat(), trace_id="test")
        data = b"file content"

        # First check — HeartBeat is called
        r1 = await checker.check(data)
        checker.record(r1.file_hash)
        assert call_count == 1

        # Second check — session cache hits, HeartBeat NOT called
        r2 = await checker.check(data)
        assert r2.is_duplicate is True
        assert r2.source == "session"
        assert call_count == 1  # Still 1, not 2

    @pytest.mark.asyncio
    async def test_no_heartbeat_client(self):
        """Works with no HeartBeat client (session cache only)."""
        checker = DedupChecker(heartbeat_client=None, trace_id="test")
        result = await checker.check(b"data")
        assert result.is_duplicate is False
