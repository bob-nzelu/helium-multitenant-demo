"""
Simple SSE Manager for Mock Core.

Publishes events to connected clients, with optional data_uuid filtering.
"""

import asyncio
import logging
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger("core.mock.sse")

_seq = 0


class SSEManager:
    def __init__(self):
        self._clients: Dict[str, dict] = {}  # client_id → {queue, filter}

    def add_client(self, queue: asyncio.Queue, data_uuid_filter: Optional[str] = None) -> str:
        client_id = f"sse-{uuid.uuid4().hex[:8]}"
        self._clients[client_id] = {"queue": queue, "filter": data_uuid_filter}
        logger.info(f"SSE client connected: {client_id} (filter={data_uuid_filter})")
        return client_id

    def remove_client(self, client_id: str):
        self._clients.pop(client_id, None)
        logger.info(f"SSE client disconnected: {client_id}")

    async def publish(self, event: dict):
        global _seq
        _seq += 1
        event["id"] = _seq

        data_uuid = event.get("data_uuid")
        event_type = event.get("event_type", "unknown")

        sent_to = 0
        for client_id, client in list(self._clients.items()):
            f = client["filter"]
            if f and data_uuid and f != data_uuid:
                continue
            try:
                client["queue"].put_nowait(event)
                sent_to += 1
            except asyncio.QueueFull:
                logger.warning(f"SSE queue full for {client_id}, dropping event")

        if sent_to > 0:
            logger.debug(f"SSE published: {event_type} → {sent_to} client(s)")
