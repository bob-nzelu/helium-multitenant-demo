"""
Tests for clients/heartbeat.py — HeartBeat API client (real httpx)

Tests use respx to mock HTTP responses from HeartBeat endpoints.
Each test group covers: success, HeartBeat down, error responses.
"""

import json
import pytest
import respx
from httpx import Response

from src.clients.heartbeat import HeartBeatClient
from src.errors import (
    HeartBeatUnavailableError,
    JWTRejectedError,
    TransientError,
)


HEARTBEAT_URL = "http://localhost:9000"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def hb_client():
    """HeartBeatClient with short timeouts for testing."""
    return HeartBeatClient(
        heartbeat_api_url=HEARTBEAT_URL,
        timeout=5.0,
        max_attempts=1,  # No retries in unit tests
        trace_id="test-trace",
        service_api_key="test-api-key",
        service_api_secret="test-api-secret",
    )


# ── Init Tests ───────────────────────────────────────────────────────────


class TestHeartBeatClientInit:
    """Test HeartBeatClient initialization."""

    def test_defaults(self):
        client = HeartBeatClient()
        assert client.heartbeat_api_url == "http://localhost:9000"
        assert client.timeout == 30.0
        assert client.max_attempts == 5
        assert client._service_api_key == ""
        assert client._service_api_secret == ""

    def test_custom_values(self):
        client = HeartBeatClient(
            heartbeat_api_url="http://hb.prod:9000",
            timeout=10.0,
            max_attempts=3,
            trace_id="test",
            service_api_key="my-key",
            service_api_secret="my-secret",
        )
        assert client.heartbeat_api_url == "http://hb.prod:9000"
        assert client._service_api_key == "my-key"
        assert client._service_api_secret == "my-secret"

    def test_trailing_slash_stripped(self):
        client = HeartBeatClient(heartbeat_api_url="http://hb:9000/")
        assert client.heartbeat_api_url == "http://hb:9000"


# ── Write Blob Tests ─────────────────────────────────────────────────────


class TestHeartBeatWriteBlob:
    """Test write_blob method — POST /api/blobs/write."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_success(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            return_value=Response(200, json={
                "blob_uuid": "blob-001",
                "blob_path": "/files_blob/blob-001-invoice.pdf",
                "file_size_bytes": 11,
                "file_hash": "abc123",
                "status": "uploaded",
            })
        )

        result = await hb_client.write_blob(
            blob_uuid="blob-001",
            filename="invoice.pdf",
            file_data=b"pdf content",
        )

        assert result["blob_uuid"] == "blob-001"
        assert result["blob_path"] == "/files_blob/blob-001-invoice.pdf"
        assert result["file_hash"] == "abc123"
        assert result["status"] == "uploaded"
        assert ("write_blob", "blob-001", "invoice.pdf") in hb_client._calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_with_jwt(self, hb_client):
        """JWT token forwarded as Authorization: Bearer header."""
        route = respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            return_value=Response(200, json={
                "blob_uuid": "b-1", "blob_path": "/p", "file_size_bytes": 4,
                "file_hash": "h", "status": "uploaded",
            })
        )

        await hb_client.write_blob(
            blob_uuid="b-1", filename="f.pdf", file_data=b"data",
            jwt_token="my-jwt-token",
        )

        req = route.calls.last.request
        assert req.headers["authorization"] == "Bearer my-jwt-token"

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_with_metadata(self, hb_client):
        """Metadata forwarded as JSON-encoded form field."""
        route = respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            return_value=Response(200, json={
                "blob_uuid": "b-1", "blob_path": "/p", "file_size_bytes": 4,
                "file_hash": "h", "status": "uploaded",
            })
        )

        meta = {"user_trace_id": "ut-123", "float_id": "f-456"}
        await hb_client.write_blob(
            blob_uuid="b-1", filename="f.pdf", file_data=b"data",
            metadata=meta,
        )

        req = route.calls.last.request
        # Metadata should be in the multipart form data
        body = req.content.decode("utf-8", errors="replace")
        assert "user_trace_id" in body

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_jwt_rejected(self, hb_client):
        """HeartBeat returns 401 → JWTRejectedError."""
        respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            return_value=Response(401, text="Invalid JWT")
        )

        with pytest.raises(JWTRejectedError):
            await hb_client.write_blob("b-1", "f.pdf", b"data", jwt_token="bad")

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_server_error(self, hb_client):
        """HeartBeat returns 500 → TransientError (retryable)."""
        respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            return_value=Response(500, text="Internal Server Error")
        )

        with pytest.raises(TransientError):
            await hb_client.write_blob("b-1", "f.pdf", b"data")

    @respx.mock
    @pytest.mark.asyncio
    async def test_write_blob_heartbeat_down(self, hb_client):
        """HeartBeat unreachable → HeartBeatUnavailableError."""
        respx.post(f"{HEARTBEAT_URL}/api/blobs/write").mock(
            side_effect=ConnectionError("Connection refused")
        )

        with pytest.raises((HeartBeatUnavailableError, Exception)):
            await hb_client.write_blob("b-1", "f.pdf", b"data")


# ── Check Duplicate Tests ────────────────────────────────────────────────


class TestHeartBeatCheckDuplicate:
    """Test check_duplicate method — GET /api/dedup/check."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_duplicate(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/dedup/check").mock(
            return_value=Response(200, json={
                "is_duplicate": False,
                "file_hash": "abc123",
                "original_queue_id": None,
            })
        )

        result = await hb_client.check_duplicate("abc123")
        assert result["is_duplicate"] is False
        assert result["file_hash"] == "abc123"
        assert ("check_duplicate", "abc123") in hb_client._calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_is_duplicate(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/dedup/check").mock(
            return_value=Response(200, json={
                "is_duplicate": True,
                "file_hash": "abc123",
                "original_queue_id": "queue-old-001",
            })
        )

        result = await hb_client.check_duplicate("abc123")
        assert result["is_duplicate"] is True
        assert result["original_queue_id"] == "queue-old-001"

    @respx.mock
    @pytest.mark.asyncio
    async def test_check_duplicate_server_error(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/dedup/check").mock(
            return_value=Response(500, text="DB error")
        )

        with pytest.raises(TransientError):
            await hb_client.check_duplicate("hash123")


# ── Record Duplicate Tests ───────────────────────────────────────────────


class TestHeartBeatRecordDuplicate:
    """Test record_duplicate method — POST /api/dedup/record."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_record_success(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/dedup/record").mock(
            return_value=Response(201, json={
                "file_hash": "hash-abc",
                "queue_id": "queue-001",
                "status": "recorded",
            })
        )

        result = await hb_client.record_duplicate("hash-abc", "queue-001")
        assert result["file_hash"] == "hash-abc"
        assert result["queue_id"] == "queue-001"
        assert result["status"] == "recorded"


# ── Daily Limit Tests ────────────────────────────────────────────────────


class TestHeartBeatDailyLimit:
    """Test check_daily_limit method — GET /api/limits/daily."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_under_limit(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/limits/daily").mock(
            return_value=Response(200, json={
                "company_id": "company-001",
                "files_today": 10,
                "daily_limit": 500,
                "limit_reached": False,
                "remaining": 490,
            })
        )

        result = await hb_client.check_daily_limit("company-001")
        assert result["limit_reached"] is False
        assert result["remaining"] == 490

    @respx.mock
    @pytest.mark.asyncio
    async def test_limit_reached(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/limits/daily").mock(
            return_value=Response(200, json={
                "company_id": "company-001",
                "files_today": 500,
                "daily_limit": 500,
                "limit_reached": True,
                "remaining": 0,
            })
        )

        result = await hb_client.check_daily_limit("company-001", file_count=3)
        assert result["limit_reached"] is True
        assert ("check_daily_limit", "company-001", 3) in hb_client._calls


# ── Register Blob Tests ──────────────────────────────────────────────────


class TestHeartBeatRegisterBlob:
    """Test register_blob method — POST /api/blobs/register (fire-and-forget)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_success(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/blobs/register").mock(
            return_value=Response(201, json={
                "blob_uuid": "b-1",
                "status": "registered",
                "tracking_id": "track_abc",
            })
        )

        result = await hb_client.register_blob(
            blob_uuid="b-1",
            filename="test.pdf",
            file_size_bytes=1024,
            file_hash="abc",
            api_key="key-1",
        )
        assert result["status"] == "registered"
        assert ("register_blob", "b-1") in hb_client._calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_with_jwt(self, hb_client):
        route = respx.post(f"{HEARTBEAT_URL}/api/blobs/register").mock(
            return_value=Response(201, json={
                "blob_uuid": "b-1", "status": "registered", "tracking_id": "t",
            })
        )

        await hb_client.register_blob(
            "b-1", "f.pdf", 100, "h", "k", jwt_token="jwt-tok",
        )

        req = route.calls.last.request
        assert req.headers["authorization"] == "Bearer jwt-tok"

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_failure_does_not_raise(self, hb_client):
        """Register is fire-and-forget — failures must not propagate."""
        respx.post(f"{HEARTBEAT_URL}/api/blobs/register").mock(
            return_value=Response(500, text="DB error")
        )

        result = await hb_client.register_blob("b-1", "f.pdf", 100, "h", "k")
        assert result["status"] == "registration_failed"


# ── Health Check Tests ───────────────────────────────────────────────────


class TestHeartBeatHealthCheck:
    """Test health check method — GET /health."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_success(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/health").mock(
            return_value=Response(200, json={"status": "healthy"})
        )

        result = await hb_client.health_check()
        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_failure(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/health").mock(
            return_value=Response(503, text="Unhealthy")
        )

        result = await hb_client.health_check()
        assert result is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_unreachable(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/health").mock(
            side_effect=ConnectionError("refused")
        )

        result = await hb_client.health_check()
        assert result is False


# ── Audit Log Tests ──────────────────────────────────────────────────────


class TestHeartBeatAuditLog:
    """Test audit logging — POST /api/audit/log (fire-and-forget)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_log_event(self, hb_client):
        route = respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a-1"})
        )

        await hb_client.audit_log(
            service="relay-api",
            event_type="file.ingested",
            user_id="user-123",
            details={"filename": "test.pdf", "size_mb": 1.5},
        )

        assert len(hb_client.audit_events) == 1
        event = hb_client.audit_events[0]
        assert event["service"] == "relay-api"
        assert event["event_type"] == "file.ingested"
        assert event["user_id"] == "user-123"
        assert event["details"]["filename"] == "test.pdf"
        assert "timestamp" in event
        assert event["trace_id"] == "test-trace"
        assert ("audit_log", "file.ingested") in hb_client._calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_log_multiple_events(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a"})
        )

        await hb_client.audit_log("svc", "batch.started")
        await hb_client.audit_log("svc", "file.ingested")
        await hb_client.audit_log("svc", "batch.completed")
        assert len(hb_client.audit_events) == 3

    @respx.mock
    @pytest.mark.asyncio
    async def test_log_no_details(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a"})
        )

        await hb_client.audit_log("svc", "event.test")
        assert hb_client.audit_events[0]["details"] == {}

    @respx.mock
    @pytest.mark.asyncio
    async def test_log_no_user_id(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a"})
        )

        await hb_client.audit_log("svc", "event.test")
        assert hb_client.audit_events[0]["user_id"] is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_audit_never_raises_on_http_failure(self, hb_client):
        """Audit logging must never block the main flow, even on HTTP errors."""
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(500, text="DB error")
        )

        # Should not raise
        await hb_client.audit_log("svc", "event.test")
        # Event still recorded locally
        assert len(hb_client.audit_events) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_audit_never_raises_on_connection_error(self, hb_client):
        """Audit works even when HeartBeat is down."""
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            side_effect=ConnectionError("refused")
        )

        await hb_client.audit_log("svc", "event.test")
        assert len(hb_client.audit_events) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_clear_audit_events(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a"})
        )

        await hb_client.audit_log("svc", "event1")
        await hb_client.audit_log("svc", "event2")
        assert len(hb_client.audit_events) == 2

        hb_client.clear_audit_events()
        assert len(hb_client.audit_events) == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_audit_event_types_for_client_demo(self, hb_client):
        """Test event types that matter for the client demo."""
        respx.post(f"{HEARTBEAT_URL}/api/audit/log").mock(
            return_value=Response(201, json={"status": "logged", "audit_id": "a"})
        )

        events = [
            ("relay-api", "auth.failed", None, {"reason": "invalid_key"}),
            ("relay-api", "submission.rejected", "u1", {"irn": "IRN001", "firs_error": "invalid_tin"}),
            ("relay-api", "submission.queued", "u1", {"irn": "IRN002", "reason": "firs_downtime"}),
            ("relay-api", "file.ingested", "u1", {"filename": "inv.pdf", "file_hash": "abc"}),
        ]

        for service, event_type, user_id, details in events:
            await hb_client.audit_log(service, event_type, user_id, details)

        assert len(hb_client.audit_events) == 4
        types = [e["event_type"] for e in hb_client.audit_events]
        assert "auth.failed" in types
        assert "submission.rejected" in types
        assert "submission.queued" in types
        assert "file.ingested" in types


# ── Metrics Tests ────────────────────────────────────────────────────────


class TestHeartBeatMetrics:
    """Test metrics reporting — POST /api/metrics/report (fire-and-forget)."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_report_ingestion_metrics(self, hb_client):
        route = respx.post(f"{HEARTBEAT_URL}/api/metrics/report").mock(
            return_value=Response(201, json={"status": "ok"})
        )

        await hb_client.report_metrics("ingestion", {
            "files_count": 3,
            "total_size_mb": 4.5,
            "duration_s": 2.3,
        })
        assert ("report_metrics", "ingestion") in hb_client._calls

        # Verify payload shape
        req = route.calls.last.request
        body = json.loads(req.content)
        assert body["metric_type"] == "ingestion"
        assert body["reported_by"] == "relay-api"

    @respx.mock
    @pytest.mark.asyncio
    async def test_report_error_metrics(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/metrics/report").mock(
            return_value=Response(201, json={"status": "ok"})
        )

        await hb_client.report_metrics("error", {
            "error_code": "VALIDATION_FAILED",
            "error_count": 1,
            "service": "relay-api",
        })
        assert ("report_metrics", "error") in hb_client._calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_metrics_never_raises_on_failure(self, hb_client):
        """Metrics reporting must never block the main flow."""
        respx.post(f"{HEARTBEAT_URL}/api/metrics/report").mock(
            return_value=Response(500, text="DB error")
        )

        # Should not raise
        await hb_client.report_metrics("ingestion", {"files_count": 1})

    @respx.mock
    @pytest.mark.asyncio
    async def test_metrics_never_raises_on_connection_error(self, hb_client):
        respx.post(f"{HEARTBEAT_URL}/api/metrics/report").mock(
            side_effect=ConnectionError("refused")
        )

        await hb_client.report_metrics("ingestion", {"files_count": 1})


# ── Transforma Config Tests ──────────────────────────────────────────────


class TestHeartBeatTransformaConfig:
    """Test get_transforma_config — GET /api/platform/transforma/config."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_config_success(self, hb_client):
        config_data = {
            "modules": [
                {
                    "module_name": "irn_generator",
                    "source_code": "def generate_irn(): ...",
                    "version": "1.0.0",
                    "checksum": "sha256:abc",
                    "updated_at": "2026-03-04T00:00:00Z",
                },
            ],
            "service_keys": {
                "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nKEY\n-----END PUBLIC KEY-----",
                "csid": "CSID-TOKEN",
                "csid_expires_at": "2030-01-01T00:00:00Z",
                "certificate": "base64cert",
            },
        }

        route = respx.get(f"{HEARTBEAT_URL}/api/platform/transforma/config").mock(
            return_value=Response(200, json=config_data)
        )

        result = await hb_client.get_transforma_config()

        assert len(result["modules"]) == 1
        assert result["modules"][0]["module_name"] == "irn_generator"
        assert "firs_public_key_pem" in result["service_keys"]
        assert ("get_transforma_config",) in hb_client._calls

        # Verify service credentials sent
        req = route.calls.last.request
        assert req.headers["authorization"] == "Bearer test-api-key:test-api-secret"

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_config_no_credentials(self):
        """Without service credentials, no Authorization header sent."""
        client = HeartBeatClient(
            heartbeat_api_url=HEARTBEAT_URL,
            timeout=5.0,
            max_attempts=1,
        )

        route = respx.get(f"{HEARTBEAT_URL}/api/platform/transforma/config").mock(
            return_value=Response(200, json={"modules": [], "service_keys": {}})
        )

        await client.get_transforma_config()

        req = route.calls.last.request
        assert "authorization" not in req.headers

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_config_heartbeat_down(self, hb_client):
        respx.get(f"{HEARTBEAT_URL}/api/platform/transforma/config").mock(
            side_effect=ConnectionError("refused")
        )

        with pytest.raises((HeartBeatUnavailableError, Exception)):
            await hb_client.get_transforma_config()


# ── Close / Lifecycle Tests ──────────────────────────────────────────────


class TestHeartBeatClientLifecycle:
    """Test client lifecycle (close, lazy init)."""

    @pytest.mark.asyncio
    async def test_close_without_use(self):
        """Close on unused client should not error."""
        client = HeartBeatClient()
        await client.close()  # No-op, _http is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_close_after_use(self):
        """Close after making calls should clean up."""
        client = HeartBeatClient(
            heartbeat_api_url=HEARTBEAT_URL,
            max_attempts=1,
        )

        respx.get(f"{HEARTBEAT_URL}/health").mock(
            return_value=Response(200, json={"status": "healthy"})
        )

        await client.health_check()
        assert client._http is not None

        await client.close()
        assert client._http is None
