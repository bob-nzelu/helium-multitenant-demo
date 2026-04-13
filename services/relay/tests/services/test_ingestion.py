"""
Tests for IngestionService (7-step pipeline)

Uses StubHeartBeatClient for tests that don't need real HTTP.
"""

import hashlib
import pytest

from src.services.ingestion import IngestionService, IngestResult
from src.config import RelayConfig
from src.clients.core import CoreClient
from src.clients.redis_client import RedisClient, RateLimitResult
from src.errors import (
    DuplicateFileError,
    NoFilesProvidedError,
    TooManyFilesError,
    InvalidFileExtensionError,
    FileSizeExceededError,
    RateLimitExceededError,
    InternalError,
)
from tests.stub_heartbeat import StubHeartBeatClient


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
    )


@pytest.fixture
def heartbeat():
    return StubHeartBeatClient()


@pytest.fixture
def core():
    return CoreClient()


@pytest.fixture
def service(config, heartbeat, core):
    return IngestionService(config, heartbeat, core)


@pytest.fixture
def single_pdf():
    return [("invoice.pdf", b"%PDF-1.4 test invoice data")]


@pytest.fixture
def multi_files():
    return [
        ("invoice_001.pdf", b"%PDF-1.4 first invoice"),
        ("data_feed.xml", b"<invoice><total>100</total></invoice>"),
    ]


# ── Happy Path ────────────────────────────────────────────────────────────


class TestIngestionHappyPath:
    """Test successful ingestion pipeline."""

    @pytest.mark.asyncio
    async def test_single_file_returns_ingest_result(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-001")
        assert isinstance(result, IngestResult)
        assert result.file_count == 1
        assert result.status == "ingested"

    @pytest.mark.asyncio
    async def test_single_file_has_uuid(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-002")
        assert len(result.data_uuid) == 36  # UUID4 format

    @pytest.mark.asyncio
    async def test_single_file_has_queue_id(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-003")
        assert result.queue_id.startswith("queue_")

    @pytest.mark.asyncio
    async def test_single_file_has_blob_path(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-004")
        assert result.blob_path.startswith("/files_blob/")
        assert "invoice.pdf" in result.blob_path

    @pytest.mark.asyncio
    async def test_single_file_has_hash(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-005")
        assert len(result.file_hash) == 64  # SHA256 hex

    @pytest.mark.asyncio
    async def test_single_file_preserves_filenames(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-006")
        assert result.filenames == ["invoice.pdf"]

    @pytest.mark.asyncio
    async def test_single_file_total_size(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-007")
        assert result.total_size_bytes == len(single_pdf[0][1])

    @pytest.mark.asyncio
    async def test_single_file_has_data_uuid(self, service, single_pdf):
        """Single-file requests always get a data_uuid (per-request group)."""
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-007b")
        assert result.data_uuid is not None
        assert len(result.data_uuid) == 36

    @pytest.mark.asyncio
    async def test_multi_file_no_zip(self, service, multi_files):
        """Multi-file: files forwarded individually, no ZIP packaging."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-008")
        assert result.file_count == 2
        assert result.filenames == ["invoice_001.pdf", "data_feed.xml"]
        assert result.status == "ingested"

    @pytest.mark.asyncio
    async def test_multi_file_has_data_uuid(self, service, multi_files):
        """Multi-file requests get a data_uuid for grouping."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-008b")
        assert result.data_uuid is not None
        assert len(result.data_uuid) == 36

    @pytest.mark.asyncio
    async def test_heartbeat_calls_made(self, service, heartbeat, single_pdf):
        """Pipeline should call HeartBeat for daily limit, write, register, audit."""
        await service.ingest(single_pdf, api_key="test-key", trace_id="t-009")
        call_names = [c[0] for c in heartbeat._calls]
        assert "check_daily_limit" in call_names
        assert "write_blob" in call_names
        assert "register_blob" in call_names
        assert "audit_log" in call_names

    @pytest.mark.asyncio
    async def test_core_enqueue_called(self, service, single_pdf):
        """Pipeline should enqueue file in Core."""
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-010")
        assert result.queue_id.startswith("queue_")


# ── Per-file Processing ──────────────────────────────────────────────────


class TestPerFileProcessing:
    """Test per-file SHA256, blob writes, and dedup checks."""

    @pytest.mark.asyncio
    async def test_per_file_hashes(self, service, multi_files):
        """Each file gets its own SHA256 hash."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-pf1")
        assert len(result.file_hashes) == 2
        assert result.file_hashes[0] != result.file_hashes[1]
        # Verify correctness
        expected_0 = hashlib.sha256(multi_files[0][1]).hexdigest()
        assert result.file_hashes[0] == expected_0

    @pytest.mark.asyncio
    async def test_per_file_blob_uuids(self, service, multi_files):
        """Each file gets its own blob_uuid."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-pf2")
        assert len(result.blob_uuids) == 2
        assert result.blob_uuids[0] != result.blob_uuids[1]
        assert all(len(u) == 36 for u in result.blob_uuids)

    @pytest.mark.asyncio
    async def test_per_file_blob_paths(self, service, multi_files):
        """Each file gets its own blob_path."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-pf3")
        assert len(result.blob_paths) == 2
        assert "invoice_001.pdf" in result.blob_paths[0]
        assert "data_feed.xml" in result.blob_paths[1]

    @pytest.mark.asyncio
    async def test_per_file_dedup_checks(self, service, heartbeat, multi_files):
        """Each file gets its own dedup check."""
        await service.ingest(multi_files, api_key="test-key", trace_id="t-pf4")
        dedup_calls = [c for c in heartbeat._calls if c[0] == "check_duplicate"]
        assert len(dedup_calls) == 2

    @pytest.mark.asyncio
    async def test_per_file_blob_writes(self, service, heartbeat, multi_files):
        """Each file gets its own blob write call."""
        await service.ingest(multi_files, api_key="test-key", trace_id="t-pf5")
        write_calls = [c for c in heartbeat._calls if c[0] == "write_blob"]
        assert len(write_calls) == 2

    @pytest.mark.asyncio
    async def test_per_file_registrations(self, service, heartbeat, multi_files):
        """Each file gets its own blob registration."""
        await service.ingest(multi_files, api_key="test-key", trace_id="t-pf6")
        reg_calls = [c for c in heartbeat._calls if c[0] == "register_blob"]
        assert len(reg_calls) == 2

    @pytest.mark.asyncio
    async def test_backward_compat_file_hash_property(self, service, single_pdf):
        """file_hash property returns first hash for backward compat."""
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-pf7")
        assert result.file_hash == result.file_hashes[0]
        assert len(result.file_hash) == 64

    @pytest.mark.asyncio
    async def test_backward_compat_blob_path_property(self, service, single_pdf):
        """blob_path property returns first path for backward compat."""
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-pf8")
        assert result.blob_path == result.blob_paths[0]
        assert result.blob_path.startswith("/files_blob/")


# ── Multi-file (No ZIP) ──────────────────────────────────────────────────


class TestMultiFileForwarding:
    """Test multi-file → individual blob writes (no ZIP)."""

    @pytest.mark.asyncio
    async def test_multi_file_preserves_all_filenames(self, service, multi_files):
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-020")
        assert result.filenames == ["invoice_001.pdf", "data_feed.xml"]
        assert result.file_count == 2

    @pytest.mark.asyncio
    async def test_single_file_has_one_filename(self, service, single_pdf):
        result = await service.ingest(single_pdf, api_key="test-key", trace_id="t-021")
        assert result.filenames == ["invoice.pdf"]
        assert result.file_count == 1

    @pytest.mark.asyncio
    async def test_multi_file_blob_write_per_file(self, service, multi_files, heartbeat):
        """Each file gets its own blob write call (per-file model)."""
        await service.ingest(multi_files, api_key="test-key", trace_id="t-022")
        write_calls = [c for c in heartbeat._calls if c[0] == "write_blob"]
        assert len(write_calls) == 2

    @pytest.mark.asyncio
    async def test_multi_file_total_size(self, service, multi_files):
        """Total size is sum of all file bytes."""
        result = await service.ingest(multi_files, api_key="test-key", trace_id="t-023")
        expected = sum(len(data) for _, data in multi_files)
        assert result.total_size_bytes == expected


# ── Step 1: Validation ───────────────────────────────────────────────────


class TestIngestionValidation:
    """Test Step 1 validation failures."""

    @pytest.mark.asyncio
    async def test_no_files(self, service):
        with pytest.raises(NoFilesProvidedError):
            await service.ingest([], api_key="k", trace_id="t")

    @pytest.mark.asyncio
    async def test_too_many_files(self, service):
        files = [(f"f{i}.pdf", b"data") for i in range(10)]
        with pytest.raises(TooManyFilesError):
            await service.ingest(files, api_key="k", trace_id="t")

    @pytest.mark.asyncio
    async def test_invalid_extension(self, service):
        files = [("malware.exe", b"data")]
        with pytest.raises(InvalidFileExtensionError):
            await service.ingest(files, api_key="k", trace_id="t")

    @pytest.mark.asyncio
    async def test_file_too_large(self, service):
        big = b"x" * (11 * 1024 * 1024)  # 11 MB > 10 MB limit
        files = [("big.pdf", big)]
        with pytest.raises(FileSizeExceededError):
            await service.ingest(files, api_key="k", trace_id="t")


# ── Step 2: Daily Limit ──────────────────────────────────────────────────


class TestIngestionDailyLimit:
    """Test Step 2 daily limit enforcement."""

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, config, core):
        """When HeartBeat says limit reached, pipeline raises."""

        class LimitReachedHeartBeat(StubHeartBeatClient):
            async def check_daily_limit(self, company_id, file_count=1):
                return {
                    "company_id": company_id,
                    "files_today": 500,
                    "daily_limit": 500,
                    "limit_reached": True,
                    "remaining": 0,
                }

        svc = IngestionService(config, LimitReachedHeartBeat(), core)
        with pytest.raises(RateLimitExceededError):
            await svc.ingest(
                [("test.pdf", b"data")], api_key="k", trace_id="t"
            )

    @pytest.mark.asyncio
    async def test_heartbeat_down_allows_upload(self, config, core):
        """When HeartBeat is down, daily limit check degrades gracefully."""

        class DownHeartBeat(StubHeartBeatClient):
            async def check_daily_limit(self, company_id, file_count=1):
                raise ConnectionError("HeartBeat is down")

        svc = IngestionService(config, DownHeartBeat(), core)
        # Should NOT raise — graceful degradation
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"


# ── Step 2b: Redis Rate Limiting ─────────────────────────────────────────


class TestIngestionRedisRateLimit:
    """Test Step 2 with Redis as primary rate limiter."""

    @pytest.fixture
    def redis_degraded(self):
        """Redis client with no URL — always degrades to allow-all."""
        return RedisClient(redis_url="")

    @pytest.fixture
    def redis_allows(self):
        """Redis client that allows the request."""
        from unittest.mock import AsyncMock
        client = RedisClient(redis_url="redis://fake", default_limit=500)
        client._available = True
        client.check_rate_limit = AsyncMock(return_value=RateLimitResult(
            allowed=True, current_count=5, limit=500, remaining=495, source="redis"
        ))
        return client

    @pytest.fixture
    def redis_rejects(self):
        """Redis client that rejects (over limit)."""
        from unittest.mock import AsyncMock
        client = RedisClient(redis_url="redis://fake", default_limit=500)
        client._available = True
        client.check_rate_limit = AsyncMock(return_value=RateLimitResult(
            allowed=False, current_count=501, limit=500, remaining=0, source="redis"
        ))
        return client

    @pytest.fixture
    def redis_broken(self):
        """Redis client that throws an error (triggers fallback)."""
        from unittest.mock import AsyncMock
        client = RedisClient(redis_url="redis://fake", default_limit=500)
        client._available = True
        client.check_rate_limit = AsyncMock(side_effect=Exception("Redis crashed"))
        return client

    @pytest.mark.asyncio
    async def test_redis_degraded_falls_through_to_heartbeat(
        self, config, heartbeat, core, redis_degraded
    ):
        """When Redis is not configured, falls through to HeartBeat."""
        svc = IngestionService(config, heartbeat, core, redis_client=redis_degraded)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"
        # HeartBeat should have been called
        call_names = [c[0] for c in heartbeat._calls]
        assert "check_daily_limit" in call_names

    @pytest.mark.asyncio
    async def test_redis_allows_skips_heartbeat(
        self, config, heartbeat, core, redis_allows
    ):
        """When Redis allows the request, HeartBeat daily limit is NOT called."""
        svc = IngestionService(config, heartbeat, core, redis_client=redis_allows)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"
        # HeartBeat should NOT have check_daily_limit called
        daily_calls = [c for c in heartbeat._calls if c[0] == "check_daily_limit"]
        assert len(daily_calls) == 0

    @pytest.mark.asyncio
    async def test_redis_rejects_raises_rate_limit(
        self, config, heartbeat, core, redis_rejects
    ):
        """When Redis says over limit, RateLimitExceededError is raised."""
        svc = IngestionService(config, heartbeat, core, redis_client=redis_rejects)
        with pytest.raises(RateLimitExceededError):
            await svc.ingest(
                [("test.pdf", b"data")], api_key="k", trace_id="t"
            )

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_heartbeat(
        self, config, heartbeat, core, redis_broken
    ):
        """When Redis throws, falls back to HeartBeat instead of failing."""
        svc = IngestionService(config, heartbeat, core, redis_client=redis_broken)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"
        # HeartBeat should have been called as fallback
        call_names = [c[0] for c in heartbeat._calls]
        assert "check_daily_limit" in call_names

    @pytest.mark.asyncio
    async def test_no_redis_client_uses_heartbeat(
        self, config, heartbeat, core
    ):
        """When redis_client is None (backward compat), uses HeartBeat only."""
        svc = IngestionService(config, heartbeat, core, redis_client=None)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"
        call_names = [c[0] for c in heartbeat._calls]
        assert "check_daily_limit" in call_names


# ── Step 3: Dedup ────────────────────────────────────────────────────────


class TestIngestionDedup:
    """Test Step 3 deduplication."""

    @pytest.mark.asyncio
    async def test_duplicate_detected(self, config, core):
        """HeartBeat reports duplicate → DuplicateFileError raised."""

        class DupHeartBeat(StubHeartBeatClient):
            async def check_duplicate(self, file_hash):
                return {
                    "is_duplicate": True,
                    "file_hash": file_hash,
                    "original_queue_id": "queue_original",
                }

        svc = IngestionService(config, DupHeartBeat(), core)
        with pytest.raises(DuplicateFileError) as exc_info:
            await svc.ingest(
                [("test.pdf", b"data")], api_key="k", trace_id="t"
            )
        assert exc_info.value.original_queue_id == "queue_original"


# ── Step 4: Blob Write Failure ───────────────────────────────────────────


class TestIngestionBlobFailure:
    """Test Step 4 commit point failure."""

    @pytest.mark.asyncio
    async def test_blob_write_failure_raises_internal(self, config, core):
        """If blob write fails, raise InternalError (nothing committed)."""

        class FailWriteHeartBeat(StubHeartBeatClient):
            async def write_blob(self, blob_uuid, filename, file_data,
                                 metadata=None, jwt_token=None):
                raise ConnectionError("MinIO is down")

        svc = IngestionService(config, FailWriteHeartBeat(), core)
        with pytest.raises(InternalError) as exc_info:
            await svc.ingest(
                [("test.pdf", b"data")], api_key="k", trace_id="t"
            )
        assert "Blob write failed" in str(exc_info.value)


# ── Steps 5-7: Best-Effort ──────────────────────────────────────────────


class TestIngestionBestEffort:
    """Test steps 5-7 graceful degradation."""

    @pytest.mark.asyncio
    async def test_core_enqueue_failure_returns_orphan(self, config, heartbeat):
        """If Core is down, pipeline returns orphan queue_id."""

        class FailCore(CoreClient):
            async def enqueue(self, blob_uuid, filename, file_size_bytes,
                              batch_id, metadata=None, jwt_token=None):
                raise ConnectionError("Core is down")

        svc = IngestionService(config, heartbeat, FailCore())
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.queue_id.startswith("orphan_")
        assert result.status == "ingested"

    @pytest.mark.asyncio
    async def test_register_failure_still_succeeds(self, config, core):
        """If blob registration fails, pipeline still succeeds."""

        class FailRegisterHeartBeat(StubHeartBeatClient):
            async def register_blob(self, blob_uuid, filename, file_size_bytes,
                                    file_hash, api_key, metadata=None, jwt_token=None):
                raise ConnectionError("Registration endpoint down")

        svc = IngestionService(config, FailRegisterHeartBeat(), core)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"

    @pytest.mark.asyncio
    async def test_audit_failure_still_succeeds(self, config, core):
        """If audit log fails, pipeline still succeeds."""

        class FailAuditHeartBeat(StubHeartBeatClient):
            async def audit_log(self, service, event_type, user_id=None, details=None):
                raise ConnectionError("Audit endpoint down")

        svc = IngestionService(config, FailAuditHeartBeat(), core)
        result = await svc.ingest(
            [("test.pdf", b"data")], api_key="k", trace_id="t"
        )
        assert result.status == "ingested"


# ── IngestResult Dataclass ───────────────────────────────────────────────


class TestIngestResult:
    """Test IngestResult dataclass."""

    def test_default_status(self):
        r = IngestResult(
            data_uuid="uuid-1",
            queue_id="queue-1",
            filenames=["test.pdf"],
            file_count=1,
            total_size_bytes=100,
            file_hashes=["abc"],
            blob_uuids=["b-1"],
            blob_paths=["/path"],
        )
        assert r.status == "ingested"

    def test_custom_status(self):
        r = IngestResult(
            data_uuid="uuid-1",
            queue_id="queue-1",
            filenames=["test.pdf"],
            file_count=1,
            total_size_bytes=100,
            file_hashes=["abc"],
            blob_uuids=["b-1"],
            blob_paths=["/path"],
            status="custom",
        )
        assert r.status == "custom"

    def test_multiple_filenames(self):
        r = IngestResult(
            data_uuid="uuid-1",
            queue_id="queue-1",
            filenames=["a.pdf", "b.xml", "c.csv"],
            file_count=3,
            total_size_bytes=300,
            file_hashes=["h1", "h2", "h3"],
            blob_uuids=["b1", "b2", "b3"],
            blob_paths=["/p1", "/p2", "/p3"],
        )
        assert r.filenames == ["a.pdf", "b.xml", "c.csv"]
        assert r.file_count == 3
        assert r.data_uuid == "uuid-1"

    def test_file_hash_property(self):
        r = IngestResult(
            data_uuid="u", queue_id="q", filenames=["f"],
            file_count=1, total_size_bytes=10,
            file_hashes=["abc123"],
        )
        assert r.file_hash == "abc123"

    def test_file_hash_empty_list(self):
        r = IngestResult(
            data_uuid="u", queue_id="q", filenames=["f"],
            file_count=1, total_size_bytes=10,
        )
        assert r.file_hash == ""

    def test_blob_path_property(self):
        r = IngestResult(
            data_uuid="u", queue_id="q", filenames=["f"],
            file_count=1, total_size_bytes=10,
            blob_paths=["/files_blob/uuid-f.pdf"],
        )
        assert r.blob_path == "/files_blob/uuid-f.pdf"

    def test_blob_path_empty_list(self):
        r = IngestResult(
            data_uuid="u", queue_id="q", filenames=["f"],
            file_count=1, total_size_bytes=10,
        )
        assert r.blob_path == ""
