"""
Coverage tests for src/clients/heartbeat.py (new httpx implementation).

Targets edge-case branches not covered by the main test_heartbeat.py:
  - register_blob ConnectError → fire-and-forget returns failure dict
  - health_check returns False on generic exception
  - audit_log logs warning on non-2xx HTTP response
  - report_metrics logs warning on non-2xx HTTP response
  - _raise_for_status with 400 (not 401, not 5xx) → HeartBeatUnavailableError
  - close() is idempotent
"""

import pytest
import respx
import httpx

from src.clients.heartbeat import HeartBeatClient
from src.errors import HeartBeatUnavailableError, JWTRejectedError, TransientError


@pytest.fixture
def client():
    return HeartBeatClient(
        heartbeat_api_url="http://localhost:9000",
        timeout=5.0,
        max_attempts=1,
    )


# ── _raise_for_status edge cases ─────────────────────────────────────────


class TestRaiseForStatus:
    """Cover _raise_for_status with different status codes."""

    def test_400_raises_heartbeat_unavailable(self, client):
        """Non-401 4xx raises HeartBeatUnavailableError."""
        resp = httpx.Response(400, text="Bad Request")
        with pytest.raises(HeartBeatUnavailableError):
            client._raise_for_status(resp, "test")

    def test_401_raises_jwt_rejected(self, client):
        """401 raises JWTRejectedError."""
        resp = httpx.Response(401, text="Unauthorized")
        with pytest.raises(JWTRejectedError):
            client._raise_for_status(resp, "test")

    def test_403_raises_heartbeat_unavailable(self, client):
        """403 raises HeartBeatUnavailableError (not JWT)."""
        resp = httpx.Response(403, text="Forbidden")
        with pytest.raises(HeartBeatUnavailableError):
            client._raise_for_status(resp, "test")

    def test_500_raises_transient(self, client):
        """500 raises TransientError."""
        resp = httpx.Response(500, text="Internal Server Error")
        with pytest.raises(TransientError):
            client._raise_for_status(resp, "test")

    def test_503_raises_transient(self, client):
        """503 raises TransientError."""
        resp = httpx.Response(503, text="Service Unavailable")
        with pytest.raises(TransientError):
            client._raise_for_status(resp, "test")

    def test_200_does_not_raise(self, client):
        """200 passes through silently."""
        resp = httpx.Response(200, text="OK")
        client._raise_for_status(resp, "test")  # Should not raise


# ── register_blob ConnectError (fire-and-forget) ─────────────────────────


class TestRegisterBlobConnectError:
    """Cover register_blob when HeartBeat is unreachable."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_returns_failure_dict(self, client):
        """ConnectError inside register_blob is caught, returns failure dict."""
        respx.post("http://localhost:9000/api/blobs/register").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await client.register_blob(
            blob_uuid="test-uuid",
            filename="test.pdf",
            file_size_bytes=1000,
            file_hash="abc123",
            api_key="test-key",
        )

        assert result["status"] == "registration_failed"
        assert result["blob_uuid"] == "test-uuid"


# ── health_check edge cases ──────────────────────────────────────────────


class TestHealthCheckEdge:
    """Cover health_check exception paths."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_returns_false_on_connect_error(self, client):
        """ConnectError → False."""
        respx.get("http://localhost:9000/health").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert await client.health_check() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_health_returns_false_on_timeout(self, client):
        """Timeout → False."""
        respx.get("http://localhost:9000/health").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )
        assert await client.health_check() is False


# ── audit_log non-2xx HTTP response ──────────────────────────────────────


class TestAuditLogHttpWarning:
    """Cover audit_log warning path on non-2xx response."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_audit_log_non_2xx_does_not_raise(self, client):
        """Non-2xx audit log response logs warning, doesn't raise."""
        respx.post("http://localhost:9000/api/audit/log").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        # Should not raise — fire-and-forget
        await client.audit_log(
            service="relay-test",
            event_type="test.event",
        )

        # Event should still be tracked locally
        assert len(client._audit_events) == 1


# ── report_metrics non-2xx HTTP response ─────────────────────────────────


class TestReportMetricsHttpWarning:
    """Cover report_metrics warning path on non-2xx response."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_metrics_non_2xx_does_not_raise(self, client):
        """Non-2xx metrics response logs warning, doesn't raise."""
        respx.post("http://localhost:9000/api/metrics/report").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        # Should not raise — fire-and-forget
        await client.report_metrics(
            metric_type="test",
            values={"count": 1},
        )

        # Call should still be tracked
        assert ("report_metrics", "test") in client._calls


# ── close() idempotency ──────────────────────────────────────────────────


class TestCloseIdempotent:
    """Cover close() edge cases."""

    @pytest.mark.asyncio
    async def test_close_without_use(self, client):
        """Closing before any HTTP calls is safe."""
        await client.close()  # No-op, _http is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_close_twice(self, client):
        """Closing twice is safe."""
        respx.get("http://localhost:9000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        await client.health_check()
        await client.close()
        await client.close()  # Second close is no-op
