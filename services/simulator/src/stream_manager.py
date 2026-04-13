"""
Stream Manager — runs continuous invoice simulation in the background.

Each stream is an asyncio task that generates and sends invoices at a
configurable cadence until stopped.
"""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .catalog import CatalogManager
from .generators import InboundGenerator, OutboundGenerator
from .hmac_signer import HMACSigner

logger = logging.getLogger(__name__)

CADENCE_MAP = {
    "1m": 60,
    "5m": 300,
    "30m": 1800,
}


@dataclass
class StreamStats:
    outbound_sent: int = 0
    inbound_sent: int = 0
    errors: int = 0
    started_at: str = ""
    last_sent_at: str = ""


@dataclass
class StreamState:
    stream_id: str
    tenant_id: str
    cadence: str
    include_inbound: bool
    inbound_ratio: float
    task: Optional[asyncio.Task] = None
    stats: StreamStats = field(default_factory=StreamStats)


class StreamManager:
    """Manages background simulation streams."""

    def __init__(
        self,
        outbound_gen: OutboundGenerator,
        inbound_gen: InboundGenerator,
        signer: HMACSigner,
        relay_url: str,
    ):
        self._outbound = outbound_gen
        self._inbound = inbound_gen
        self._signer = signer
        self._relay_url = relay_url
        self._streams: dict[str, StreamState] = {}
        self._counter = 0

    def _next_stream_id(self, tenant_id: str) -> str:
        self._counter += 1
        return f"sim-{tenant_id}-{self._counter:03d}"

    @property
    def active_stream_ids(self) -> list[str]:
        return [
            sid for sid, s in self._streams.items()
            if s.task and not s.task.done()
        ]

    def start(
        self,
        tenant_id: str,
        cadence: str = "1m",
        include_inbound: bool = True,
        inbound_ratio: float = 0.2,
    ) -> StreamState:
        stream_id = self._next_stream_id(tenant_id)
        state = StreamState(
            stream_id=stream_id,
            tenant_id=tenant_id,
            cadence=cadence,
            include_inbound=include_inbound,
            inbound_ratio=inbound_ratio,
            stats=StreamStats(
                started_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

        task = asyncio.create_task(self._run_loop(state))
        state.task = task
        self._streams[stream_id] = state
        logger.info(f"Stream {stream_id} started — cadence={cadence}")
        return state

    def stop(self, stream_id: str) -> bool:
        state = self._streams.get(stream_id)
        if not state or not state.task:
            return False
        state.task.cancel()
        logger.info(f"Stream {stream_id} stopped")
        return True

    def get_status(self, stream_id: str) -> Optional[dict]:
        state = self._streams.get(stream_id)
        if not state:
            return None
        running = state.task is not None and not state.task.done()
        return {
            "stream_id": state.stream_id,
            "running": running,
            "cadence": state.cadence,
            "include_inbound": state.include_inbound,
            "stats": {
                "outbound_sent": state.stats.outbound_sent,
                "inbound_sent": state.stats.inbound_sent,
                "errors": state.stats.errors,
                "started_at": state.stats.started_at,
                "last_sent_at": state.stats.last_sent_at,
            },
        }

    async def stop_all(self) -> None:
        for stream_id in list(self._streams):
            self.stop(stream_id)

    async def _run_loop(self, state: StreamState) -> None:
        interval = CADENCE_MAP.get(state.cadence, 60)
        try:
            while True:
                await self._tick(state)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info(f"Stream {state.stream_id} cancelled")

    async def _tick(self, state: StreamState) -> None:
        tenant_id = state.tenant_id
        now_iso = datetime.now(timezone.utc).isoformat()

        # Send 1-3 outbound invoices
        out_count = random.randint(1, 3)
        for _ in range(out_count):
            try:
                invoice = self._outbound.generate(tenant_id)
                await self._signer.send_to_relay(
                    self._relay_url, tenant_id, invoice,
                )
                state.stats.outbound_sent += 1
                state.stats.last_sent_at = now_iso
            except Exception as e:
                logger.error(f"Stream {state.stream_id} outbound error: {e}")
                state.stats.errors += 1

        # Optionally send inbound
        if state.include_inbound and random.random() < state.inbound_ratio:
            try:
                invoice = self._inbound.generate(tenant_id)
                await self._signer.send_to_relay(
                    self._relay_url, tenant_id, invoice,
                )
                state.stats.inbound_sent += 1
                state.stats.last_sent_at = now_iso
            except Exception as e:
                logger.error(f"Stream {state.stream_id} inbound error: {e}")
                state.stats.errors += 1
