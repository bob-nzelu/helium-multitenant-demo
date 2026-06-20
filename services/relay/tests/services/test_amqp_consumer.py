"""
Tests for AMQPConsumer (Q37 Gap #6).

aio_pika is mocked throughout — no real RabbitMQ broker required.

Coverage:
  - start() when amqp_url empty  → stays disabled, no error
  - start() when amqp_url set    → connects, declares exchange/queue, starts consuming
  - start() with aio_pika absent → stays disabled, no error
  - _handle_message() valid JSON array → calls batch_service.process_batch
  - _handle_message() non-list JSON   → skipped silently
  - _handle_message() missing tenant_id header → skipped silently
  - _handle_message() unknown tenant_id        → skipped silently
  - _handle_message() with reply_to set → result published to reply queue
  - _handle_message() with reply_to absent → result not published, no error
  - health_status property → "disabled" / "connected" / "disconnected"
  - GET /health includes "amqp" key in services dict
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.config import RelayConfig
from src.services.amqp_consumer import AMQPConsumer
from src.core.tenant import TenantConfig


# ── Helpers / stubs ───────────────────────────────────────────────────────────


def make_config(url: str = "") -> RelayConfig:
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        amqp_url=url,
        amqp_exchange="helium.ingest",
        amqp_queue="relay.ingest",
        amqp_routing_key="ingest",
    )


def make_tenant(tenant_id: str = "tenant-abc") -> TenantConfig:
    return TenantConfig(
        tenant_id=tenant_id,
        api_key="key-abc",
        api_secret="secret-abc",
        service_id="SVC001",
        name="Test Tenant",
    )


def make_batch_result(status: str = "ok"):
    """Return a fake BatchIngestResult whose to_response() returns a JSON-able object."""
    resp = MagicMock()
    resp.status = status
    resp.model_dump_json.return_value = json.dumps({"status": status, "batch_id": "B1"})
    result = MagicMock()
    result.to_response.return_value = resp
    return result


def make_message(
    body_data,
    tenant_id: str = "tenant-abc",
    reply_to: str = "",
    message_id: str = "msg-001",
    correlation_id: str = "corr-001",
    delivery_tag: int = 1,
) -> MagicMock:
    """Build a mock aio_pika IncomingMessage."""
    msg = MagicMock()
    msg.body = json.dumps(body_data).encode()
    msg.headers = {"tenant_id": tenant_id} if tenant_id else {}
    msg.reply_to = reply_to or None
    msg.message_id = message_id
    msg.correlation_id = correlation_id
    msg.delivery_tag = delivery_tag

    # Support 'async with message.process():'
    @asynccontextmanager
    async def _process():
        yield

    msg.process = _process
    return msg


# ── start() when amqp_url empty ───────────────────────────────────────────────


class TestStartDisabled:
    @pytest.mark.asyncio
    async def test_start_empty_url_stays_disabled(self):
        config = make_config(url="")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})
        await consumer.start()

        assert consumer.is_connected is False
        assert consumer._disabled is True
        assert consumer.health_status == "disabled"

    @pytest.mark.asyncio
    async def test_start_empty_url_stop_is_safe(self):
        config = make_config(url="")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})
        await consumer.start()
        await consumer.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_start_no_aio_pika_stays_disabled(self):
        """Simulate aio_pika not installed by patching the module-level flag."""
        config = make_config(url="amqp://localhost/")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})

        with patch("src.services.amqp_consumer._AIO_PIKA_AVAILABLE", False):
            await consumer.start()

        assert consumer.is_connected is False
        assert consumer._disabled is True
        assert consumer.health_status == "disabled"


# ── start() when amqp_url configured ─────────────────────────────────────────


class TestStartConnected:
    @pytest.mark.asyncio
    async def test_start_calls_connect_robust(self):
        config = make_config(url="amqp://guest:guest@localhost:5672/")
        batch_svc = AsyncMock()
        tenant = make_tenant()

        mock_queue = AsyncMock()
        mock_exchange = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.declare_exchange = AsyncMock(return_value=mock_exchange)
        mock_channel.declare_queue = AsyncMock(return_value=mock_queue)
        mock_connection = AsyncMock()
        mock_connection.channel = AsyncMock(return_value=mock_channel)

        with patch("src.services.amqp_consumer._AIO_PIKA_AVAILABLE", True), \
             patch("src.services.amqp_consumer.aio_pika") as mock_aio_pika:
            mock_aio_pika.connect_robust = AsyncMock(return_value=mock_connection)
            mock_aio_pika.ExchangeType = MagicMock()
            mock_aio_pika.ExchangeType.DIRECT = "direct"

            consumer = AMQPConsumer(config, batch_service=batch_svc, tenant_registry={})
            await consumer.start()

        mock_aio_pika.connect_robust.assert_called_once_with(config.amqp_url)
        mock_channel.declare_exchange.assert_called_once()
        mock_channel.declare_queue.assert_called_once()
        mock_queue.bind.assert_called_once()
        mock_queue.consume.assert_called_once()
        assert consumer.is_connected is True
        assert consumer.health_status == "connected"

    @pytest.mark.asyncio
    async def test_start_connection_error_non_fatal(self):
        config = make_config(url="amqp://bad-host:5672/")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})

        with patch("src.services.amqp_consumer._AIO_PIKA_AVAILABLE", True), \
             patch("src.services.amqp_consumer.aio_pika") as mock_aio_pika:
            mock_aio_pika.connect_robust = AsyncMock(
                side_effect=ConnectionError("refused")
            )

            await consumer.start()  # must not raise

        assert consumer.is_connected is False
        assert consumer.health_status == "disconnected"

    @pytest.mark.asyncio
    async def test_stop_closes_connection(self):
        config = make_config(url="amqp://localhost/")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})
        mock_conn = AsyncMock()
        consumer._connection = mock_conn
        consumer.is_connected = True

        await consumer.stop()

        mock_conn.close.assert_called_once()
        assert consumer.is_connected is False

    @pytest.mark.asyncio
    async def test_stop_when_never_started(self):
        config = make_config(url="")
        consumer = AMQPConsumer(config, batch_service=None, tenant_registry={})
        await consumer.stop()  # must not raise


# ── _handle_message(): valid JSON array ───────────────────────────────────────


class TestHandleMessage:
    def _make_consumer(self, batch_result=None, tenant_id="tenant-abc"):
        config = make_config(url="amqp://localhost/")
        batch_svc = AsyncMock()
        if batch_result is None:
            batch_result = make_batch_result()
        batch_svc.process_batch = AsyncMock(return_value=batch_result)
        tenant = make_tenant(tenant_id)
        consumer = AMQPConsumer(
            config,
            batch_service=batch_svc,
            tenant_registry={tenant_id: tenant},
        )
        # Simulate a connected channel for reply publishing
        mock_channel = AsyncMock()
        mock_channel.default_exchange = AsyncMock()
        mock_channel.default_exchange.publish = AsyncMock()
        consumer._channel = mock_channel
        consumer.is_connected = True
        return consumer, batch_svc, mock_channel

    @pytest.mark.asyncio
    async def test_valid_records_calls_process_batch(self):
        consumer, batch_svc, _ = self._make_consumer()
        records = [
            {"transaction_id": "T1", "fee_amount": 100.0},
            {"transaction_id": "T2", "fee_amount": 200.0},
        ]
        msg = make_message(records)

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_called_once()
        call_kwargs = batch_svc.process_batch.call_args
        assert call_kwargs.kwargs["records"] == records
        assert call_kwargs.kwargs["tenant"].tenant_id == "tenant-abc"

    @pytest.mark.asyncio
    async def test_non_list_json_skipped(self):
        consumer, batch_svc, _ = self._make_consumer()
        msg = make_message({"transaction_id": "T1"})  # dict, not list

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        consumer, batch_svc, _ = self._make_consumer()
        msg = MagicMock()
        msg.body = b"not-valid-json{{{"
        msg.delivery_tag = 99
        msg.headers = {"tenant_id": "tenant-abc"}
        msg.reply_to = None
        msg.message_id = "m1"
        msg.correlation_id = "c1"

        @asynccontextmanager
        async def _process():
            yield

        msg.process = _process

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_tenant_id_header_skipped(self):
        consumer, batch_svc, _ = self._make_consumer()
        msg = make_message([{"transaction_id": "T1", "fee_amount": 1.0}], tenant_id="")

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_tenant_id_skipped(self):
        consumer, batch_svc, _ = self._make_consumer()
        msg = make_message(
            [{"transaction_id": "T1", "fee_amount": 1.0}],
            tenant_id="unknown-tenant",
        )

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_to_set_publishes_result(self):
        batch_result = make_batch_result(status="ok")
        consumer, _, mock_channel = self._make_consumer(batch_result=batch_result)
        records = [{"transaction_id": "T1", "fee_amount": 50.0}]
        msg = make_message(records, reply_to="reply-queue-xyz", correlation_id="corr-999")

        with patch("src.services.amqp_consumer.aio_pika") as mock_aio_pika:
            mock_msg_cls = MagicMock()
            mock_aio_pika.Message = mock_msg_cls

            await consumer._handle_message(msg)

        mock_channel.default_exchange.publish.assert_called_once()
        call_kwargs = mock_channel.default_exchange.publish.call_args
        # routing_key must be reply-queue-xyz
        assert call_kwargs.kwargs.get("routing_key") == "reply-queue-xyz"

    @pytest.mark.asyncio
    async def test_reply_to_absent_no_publish(self):
        consumer, batch_svc, mock_channel = self._make_consumer()
        records = [{"transaction_id": "T1", "fee_amount": 50.0}]
        msg = make_message(records, reply_to="")  # reply_to=None after make_message

        await consumer._handle_message(msg)

        batch_svc.process_batch.assert_called_once()
        mock_channel.default_exchange.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_service_none_skips_silently(self):
        config = make_config(url="amqp://localhost/")
        tenant = make_tenant()
        consumer = AMQPConsumer(
            config,
            batch_service=None,
            tenant_registry={"tenant-abc": tenant},
        )
        consumer.is_connected = True

        records = [{"transaction_id": "T1", "fee_amount": 50.0}]
        msg = make_message(records)

        await consumer._handle_message(msg)  # must not raise


# ── health_status property ────────────────────────────────────────────────────


class TestHealthStatus:
    def test_disabled_when_url_empty(self):
        consumer = AMQPConsumer(make_config(url=""), None, {})
        consumer._disabled = True
        assert consumer.health_status == "disabled"

    def test_connected_when_is_connected_true(self):
        consumer = AMQPConsumer(make_config(url="amqp://localhost/"), None, {})
        consumer._disabled = False
        consumer.is_connected = True
        assert consumer.health_status == "connected"

    def test_disconnected_when_configured_but_not_connected(self):
        consumer = AMQPConsumer(make_config(url="amqp://localhost/"), None, {})
        consumer._disabled = False
        consumer.is_connected = False
        assert consumer.health_status == "disconnected"


# ── GET /health includes amqp key ─────────────────────────────────────────────


class TestHealthEndpointAMQP:
    @pytest.mark.asyncio
    async def test_health_includes_amqp_disabled(self):
        """When amqp_url is empty the health endpoint reports amqp=disabled."""
        import respx
        import httpx
        from asgi_lifespan import LifespanManager
        from httpx import AsyncClient, ASGITransport
        from src.api.app import create_app

        config = RelayConfig(
            host="127.0.0.1",
            port=8082,
            instance_id="relay-amqp-health-test",
            require_encryption=False,
            internal_service_token="test-token",
            heartbeat_api_key="test-relay-key",
            heartbeat_s2s_signing_key="0123456789abcdef" * 4,
            amqp_url="",  # AMQP disabled
        )

        from datetime import datetime, timezone
        transforma_stub = {
            "modules": [
                {
                    "module_name": "irn_generator",
                    "source_code": (
                        'def generate_irn(invoice_data: dict) -> str:\n'
                        '    return "TEST-IRN-001"\n'
                    ),
                    "version": "1.0.0-stub",
                    "checksum": "sha256:stub",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "module_name": "qr_generator",
                    "source_code": (
                        'def generate_qr_data(irn: str, keys=None) -> str:\n'
                        '    return "stub-qr"\n'
                        'def create_qr_image_bytes(qr_data: str) -> bytes:\n'
                        '    return b"PNG"\n'
                    ),
                    "version": "1.0.0-stub",
                    "checksum": "sha256:stub",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
            "service_keys": {
                "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nSTUB\n-----END PUBLIC KEY-----",
                "csid": "STUB-CSID",
                "csid_expires_at": "2030-01-01T00:00:00Z",
                "certificate": "c3R1Yl9jZXJ0",
            },
        }

        with respx.mock:
            respx.post("http://localhost:9000/api/v1/heartbeat/config").mock(
                return_value=httpx.Response(200, json={
                    "tenant": {"tenant_id": "t1", "tenant_name": "T", "tier": "test"},
                    "tier_limits": {"daily_upload_limit": 500},
                    "service_endpoints": [],
                })
            )
            respx.post("http://localhost:9000/api/platform/transforma/config").mock(
                return_value=httpx.Response(200, json=transforma_stub)
            )
            respx.get("http://localhost:9000/health").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )

            app = create_app(config=config, api_key_secrets={})
            async with LifespanManager(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "amqp" in data["services"]
        assert data["services"]["amqp"] == "disabled"

    @pytest.mark.asyncio
    async def test_health_amqp_key_present_when_no_consumer_state(self):
        """Simulate app.state.amqp_consumer missing (pre-wiring compatibility)."""
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        from src.api.routes.health import router
        from src.api.models import HealthResponse

        app = FastAPI()
        app.include_router(router)

        # Set minimal state without amqp_consumer
        @app.on_event("startup")
        async def setup_state():
            app.state.config = RelayConfig(instance_id="test")
            app.state.module_cache = MagicMock(is_loaded=True)
            app.state.redis = MagicMock(is_available=True)

            class FakeHB:
                async def health_check(self):
                    return True
                async def close(self):
                    pass

            app.state.heartbeat = FakeHB()
            # deliberately NOT setting app.state.amqp_consumer

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        # amqp should still appear with "disabled" fallback
        assert data["services"].get("amqp") == "disabled"
