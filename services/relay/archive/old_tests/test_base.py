"""
Unit Tests for BaseRelayService

Tests the abstract base class functionality:
- Initialization
- Session-scoped deduplication cache
- Two-level duplicate checking
- Rate limit enforcement
- Batch ID generation
- File hash computation

Target Coverage: 100%
"""

import pytest
import asyncio
import hashlib
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Dict, Any, Optional

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.base import BaseRelayService
from src.services.errors import (
    DuplicateFileError,
    RateLimitExceededError,
)


# =============================================================================
# Concrete Implementation for Testing
# =============================================================================

class TestRelayService(BaseRelayService):
    """Concrete implementation for testing abstract base class."""

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Test implementation of ingest_file."""
        return {
            "status": "processed",
            "filename": filename,
            "batch_id": batch_id,
        }


# =============================================================================
# Initialization Tests
# =============================================================================

class TestBaseRelayServiceInit:
    """Tests for BaseRelayService initialization."""

    def test_initialization(self):
        """Should initialize with required dependencies."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
            trace_id="test_trace_123",
        )

        assert service.service_name == "relay-test"
        assert service.core_client == core_client
        assert service.heartbeat_client == heartbeat_client
        assert service.audit_client == audit_client
        assert service.trace_id == "test_trace_123"
        assert service.session_dedup_cache == set()

    def test_generates_trace_id_if_not_provided(self):
        """Should generate trace ID if not provided."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        assert service.trace_id is not None
        assert len(service.trace_id) > 0


# =============================================================================
# File Hash Computation Tests
# =============================================================================

class TestComputeFileHash:
    """Tests for _compute_file_hash() method."""

    def test_compute_hash(self):
        """Should compute SHA256 hash."""
        file_data = b"test content"
        expected_hash = hashlib.sha256(file_data).hexdigest()

        result = BaseRelayService._compute_file_hash(file_data)

        assert result == expected_hash

    def test_hash_is_deterministic(self):
        """Same content should produce same hash."""
        file_data = b"deterministic content"

        hash1 = BaseRelayService._compute_file_hash(file_data)
        hash2 = BaseRelayService._compute_file_hash(file_data)

        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Different content should produce different hashes."""
        hash1 = BaseRelayService._compute_file_hash(b"content 1")
        hash2 = BaseRelayService._compute_file_hash(b"content 2")

        assert hash1 != hash2

    def test_empty_file_has_hash(self):
        """Empty file should still produce valid hash."""
        result = BaseRelayService._compute_file_hash(b"")

        assert result is not None
        assert len(result) == 64  # SHA256 produces 64 hex chars


# =============================================================================
# Session Cache Tests
# =============================================================================

class TestSessionCache:
    """Tests for session-scoped deduplication cache."""

    def test_cache_starts_empty(self):
        """Session cache should start empty."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        assert len(service.session_dedup_cache) == 0

    def test_clear_session_cache(self):
        """Should clear session cache."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        # Add items to cache
        service.session_dedup_cache.add("hash1")
        service.session_dedup_cache.add("hash2")
        assert len(service.session_dedup_cache) == 2

        # Clear cache
        service.clear_session_cache()
        assert len(service.session_dedup_cache) == 0


# =============================================================================
# Duplicate Checking Tests
# =============================================================================

class TestCheckDuplicate:
    """Tests for _check_duplicate() method."""

    @pytest.mark.asyncio
    async def test_session_cache_duplicate(self):
        """Should detect duplicate in session cache."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        file_data = b"duplicate content"
        file_hash = hashlib.sha256(file_data).hexdigest()

        # Pre-add hash to session cache
        service.session_dedup_cache.add(file_hash)

        with pytest.raises(DuplicateFileError) as exc_info:
            await service._check_duplicate(file_data)

        assert exc_info.value.file_hash == file_hash

    @pytest.mark.asyncio
    async def test_heartbeat_duplicate(self):
        """Should detect duplicate via HeartBeat."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        # HeartBeat reports duplicate
        heartbeat_client.check_duplicate.return_value = {
            "is_duplicate": True,
            "original_queue_id": "queue_123",
        }

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        file_data = b"duplicate content"

        with pytest.raises(DuplicateFileError) as exc_info:
            await service._check_duplicate(file_data)

        assert exc_info.value.original_queue_id == "queue_123"

    @pytest.mark.asyncio
    async def test_not_duplicate(self):
        """Should return False for new file."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        # HeartBeat reports not duplicate
        heartbeat_client.check_duplicate.return_value = {
            "is_duplicate": False,
            "original_queue_id": None,
        }

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        file_data = b"new content"
        file_hash = hashlib.sha256(file_data).hexdigest()

        result = await service._check_duplicate(file_data)

        assert result is False
        # Should add to session cache
        assert file_hash in service.session_dedup_cache


# =============================================================================
# Store Deduplication Record Tests
# =============================================================================

class TestStoreDuplicationRecord:
    """Tests for _store_deduplication_record() method."""

    @pytest.mark.asyncio
    async def test_store_record_success(self):
        """Should store dedup record via HeartBeat."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        heartbeat_client.record_duplicate.return_value = {"status": "recorded"}

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        file_data = b"test content"
        await service._store_deduplication_record(file_data, "queue_456")

        heartbeat_client.record_duplicate.assert_called_once()
        call_args = heartbeat_client.record_duplicate.call_args
        assert call_args[1]["queue_id"] == "queue_456"

    @pytest.mark.asyncio
    async def test_store_record_failure_does_not_raise(self):
        """Dedup record storage failure should not raise."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        heartbeat_client.record_duplicate.side_effect = Exception("Storage error")

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        # Should not raise
        await service._store_deduplication_record(b"test", "queue_123")


# =============================================================================
# Rate Limit Tests
# =============================================================================

class TestCheckRateLimit:
    """Tests for _check_rate_limit() method."""

    @pytest.mark.asyncio
    async def test_under_limit(self):
        """Should return False when under limit."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        heartbeat_client.check_daily_limit.return_value = {
            "limit_reached": False,
            "daily_limit": 500,
            "remaining": 400,
        }

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        result = await service._check_rate_limit("test_api_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_limit_reached(self):
        """Should raise when limit reached."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        heartbeat_client.check_daily_limit.return_value = {
            "limit_reached": True,
            "daily_limit": 500,
        }

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        with pytest.raises(RateLimitExceededError) as exc_info:
            await service._check_rate_limit("test_api_key")

        assert exc_info.value.retry_after_seconds == 86400  # 24 hours

    @pytest.mark.asyncio
    async def test_rate_limit_check_failure_allows_request(self):
        """Rate limit check failure should allow request to proceed."""
        core_client = MagicMock()
        heartbeat_client = AsyncMock()
        audit_client = MagicMock()

        heartbeat_client.check_daily_limit.side_effect = Exception("Service error")

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        # Should not raise - graceful degradation
        result = await service._check_rate_limit("test_api_key")
        assert result is False


# =============================================================================
# Batch ID Generation Tests
# =============================================================================

class TestGenerateBatchId:
    """Tests for _generate_batch_id() method."""

    def test_batch_id_format(self):
        """Batch ID should have correct format."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        batch_id = service._generate_batch_id()

        assert batch_id.startswith("batch_")

    def test_batch_ids_unique(self):
        """Each batch ID should be unique."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        batch_ids = [service._generate_batch_id() for _ in range(10)]
        assert len(set(batch_ids)) == 10  # All unique


# =============================================================================
# Abstract Method Tests
# =============================================================================

class TestAbstractIngestFile:
    """Tests for abstract ingest_file() method."""

    @pytest.mark.asyncio
    async def test_concrete_implementation_works(self):
        """Concrete implementation should work."""
        core_client = MagicMock()
        heartbeat_client = MagicMock()
        audit_client = MagicMock()

        service = TestRelayService(
            service_name="relay-test",
            core_client=core_client,
            heartbeat_client=heartbeat_client,
            audit_client=audit_client,
        )

        result = await service.ingest_file(
            file_data=b"test",
            filename="test.pdf",
            batch_id="batch_123",
        )

        assert result["status"] == "processed"
        assert result["filename"] == "test.pdf"
        assert result["batch_id"] == "batch_123"
