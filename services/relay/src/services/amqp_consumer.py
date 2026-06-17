"""
AMQP Ingestion Consumer (Q37 Gap #6)

Per-tenant AMQP consumer that:
  - Connects to RabbitMQ using aio_pika (optional dep — disabled gracefully if absent)
  - Subscribes to the configured exchange/queue/routing-key from RelayConfig
  - Receives messages whose body is the same JSON array as POST /api/ingest
  - Processes records via BatchExternalService (reuses the HTTP-path pipeline)
  - Publishes a BatchIngestResponse to the per-message reply_to queue

AMQP is fully optional:
  - amqp_url empty  → consumer stays disabled, Relay starts normally
  - aio_pika absent → consumer stays disabled, Relay starts normally
  - connection error at startup → logs a warning, marks is_connected=False (non-fatal)

Tenant identification:
  Messages MUST carry a ``tenant_id`` header matching a key in the tenant registry
  (built from tenants.json, keyed by tenant_id). Unknown tenant → message skipped.
  Use the message ``correlation_id`` and ``message_id`` for tracing.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# aio_pika is an optional dependency — import guarded so that Relay starts
# normally when the package is not installed.
try:
    import aio_pika
    _AIO_PIKA_AVAILABLE = True
except ImportError:  # pragma: no cover
    aio_pika = None  # type: ignore[assignment]
    _AIO_PIKA_AVAILABLE = False


class AMQPConsumer:
    """Per-tenant AMQP ingestion consumer.

    Subscribes to the configured exchange/queue, processes batches via
    BatchExternalService, and publishes results to the reply queue.

    Instantiate from app lifespan; call ``await start()`` on startup and
    ``await stop()`` on shutdown.  Both are always safe to call even when
    AMQP is disabled or aio_pika is not installed.
    """

    def __init__(
        self,
        config: Any,
        batch_service: Any,
        tenant_registry: Dict[str, Any],
    ) -> None:
        """
        Args:
            config:           RelayConfig instance.
            batch_service:    BatchExternalService instance (or None when Gap #3 not yet live).
            tenant_registry:  Dict keyed by tenant_id → TenantConfig (NOT api_key).
        """
        self._config = config
        self._batch_service = batch_service
        self._tenants = tenant_registry  # tenant_id → TenantConfig

        self._connection: Optional[Any] = None
        self._channel: Optional[Any] = None
        self._queue: Optional[Any] = None

        #: Public status flag read by GET /health
        self.is_connected: bool = False
        self._disabled: bool = False  # True when URL empty or aio_pika absent

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect and start consuming.  Called from FastAPI lifespan startup.

        Non-fatal: logs a warning on any connection error and sets
        ``is_connected = False`` so the health endpoint reports "disconnected".
        """
        if not _AIO_PIKA_AVAILABLE:
            logger.info(
                "AMQP consumer disabled — aio_pika is not installed; "
                "install it with: pip install aio-pika"
            )
            self._disabled = True
            return

        if not self._config.amqp_url:
            logger.info("AMQP consumer disabled (RELAY_AMQP_URL not set)")
            self._disabled = True
            return

        if self._batch_service is None:
            logger.warning(
                "AMQP consumer: BatchExternalService not available yet — "
                "starting in receive-only mode (messages will be skipped)"
            )

        try:
            self._connection = await aio_pika.connect_robust(self._config.amqp_url)
            self._channel = await self._connection.channel()

            # Declare exchange
            exchange = await self._channel.declare_exchange(
                self._config.amqp_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )

            # Declare and bind ingest queue
            queue_name = self._config.amqp_queue or "relay.ingest"
            self._queue = await self._channel.declare_queue(queue_name, durable=True)
            await self._queue.bind(exchange, routing_key=self._config.amqp_routing_key)

            # Start consuming (aio_pika delivers messages asynchronously)
            await self._queue.consume(self._handle_message)

            self.is_connected = True
            logger.info(
                "AMQP consumer started — exchange=%s queue=%s routing_key=%s",
                self._config.amqp_exchange,
                queue_name,
                self._config.amqp_routing_key,
            )
        except Exception as exc:
            logger.warning(
                "AMQP consumer failed to start (non-fatal): %s — "
                "Relay will continue without AMQP ingestion",
                exc,
            )
            self.is_connected = False

    async def stop(self) -> None:
        """Close the AMQP connection.  Called from FastAPI lifespan shutdown."""
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as exc:
                logger.warning("AMQP consumer error during shutdown: %s", exc)
        self.is_connected = False

    # ── Message handler ───────────────────────────────────────────────────────

    async def _handle_message(self, message: Any) -> None:
        """Process one AMQP message.

        Protocol:
          body              — JSON array of invoice records (same schema as POST /api/ingest)
          headers.tenant_id — tenant_id matching a key in the registry
          message_id        — used as batch_id + trace_id
          reply_to          — optional; when present, publish BatchIngestResponse there
          correlation_id    — echoed back in the reply message

        Errors are caught and logged; the message is always acked (no requeue on
        logical errors to avoid poison-message loops).
        """
        async with message.process():
            try:
                await self._process(message)
            except Exception as exc:
                logger.exception(
                    "AMQP message processing raised unexpected error: %s", exc
                )

    async def _process(self, message: Any) -> None:
        """Inner handler — separated so _handle_message can cleanly catch all errors."""
        # ── 1. Parse body ────────────────────────────────────────────────────
        try:
            records = json.loads(message.body)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "AMQP message body is not valid JSON — skipping (delivery_tag=%s): %s",
                message.delivery_tag,
                exc,
            )
            return

        if not isinstance(records, list):
            logger.warning(
                "AMQP message body is not a JSON array — skipping (delivery_tag=%s)",
                message.delivery_tag,
            )
            return

        # ── 2. Resolve tenant ────────────────────────────────────────────────
        headers = message.headers or {}
        tenant_id = headers.get("tenant_id", "")
        if not tenant_id:
            logger.warning(
                "AMQP message missing tenant_id header — skipping (delivery_tag=%s)",
                message.delivery_tag,
            )
            return

        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            logger.warning(
                "AMQP message has unknown tenant_id=%s — skipping (delivery_tag=%s)",
                tenant_id,
                message.delivery_tag,
            )
            return

        # ── 3. Guard: batch service must be available ─────────────────────────
        if self._batch_service is None:
            logger.warning(
                "AMQP consumer: BatchExternalService unavailable — "
                "skipping message for tenant=%s (delivery_tag=%s)",
                tenant_id,
                message.delivery_tag,
            )
            return

        # ── 4. Process via BatchExternalService ──────────────────────────────
        batch_id = message.message_id or f"amqp-{message.delivery_tag}"
        trace_id = batch_id

        logger.info(
            "AMQP processing batch — batch_id=%s tenant=%s records=%d",
            batch_id,
            tenant_id,
            len(records),
        )

        result = await self._batch_service.process_batch(
            records=records,
            batch_id=batch_id,
            tenant=tenant,
            trace_id=trace_id,
            jwt_token=None,
        )

        # ── 5. Publish result to reply queue (if reply_to present) ───────────
        reply_to = message.reply_to
        if reply_to:
            try:
                response = result.to_response()
                reply_body = response.model_dump_json().encode("utf-8")

                # Declare the reply queue passively (it must already exist on
                # the broker side, created by the caller).
                await self._channel.default_exchange.publish(
                    aio_pika.Message(
                        body=reply_body,
                        content_type="application/json",
                        correlation_id=message.correlation_id or "",
                    ),
                    routing_key=reply_to,
                )
                logger.info(
                    "AMQP reply published — batch_id=%s reply_to=%s status=%s",
                    batch_id,
                    reply_to,
                    response.status,
                )
            except Exception as exc:
                logger.warning(
                    "AMQP reply publish failed (batch_id=%s reply_to=%s): %s",
                    batch_id,
                    reply_to,
                    exc,
                )
        else:
            logger.debug(
                "AMQP message has no reply_to — result not published (batch_id=%s)",
                batch_id,
            )

    # ── Health status ─────────────────────────────────────────────────────────

    @property
    def health_status(self) -> str:
        """Return a string suitable for inclusion in GET /health services dict.

        Returns:
            "disabled"      when amqp_url is empty or aio_pika is not installed
            "connected"     when the consumer is actively consuming
            "disconnected"  when configured but not currently connected
        """
        if self._disabled:
            return "disabled"
        return "connected" if self.is_connected else "disconnected"
