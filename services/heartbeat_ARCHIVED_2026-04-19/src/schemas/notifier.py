"""
Schema Change Notifier — pushes schema update notifications to consumers.

Two-channel architecture:
  1. HTTP callbacks: POST {base_url}/internal/schema-refresh to Core, Edge, etc.
  2. SSE event: Publish "schema.updated" on EventBus for Float SDK instances.

Services register via HeartBeat's service registry. The notifier queries all
active service instances and POSTs to each one. Fire-and-forget — failures are
logged but not retried (services catch up at next startup).

Float SDK instances receive notifications via the existing HeartBeat SSE stream
(they already subscribe to blob.* events; adding schema.* is automatic via
EventBus fnmatch pattern matching).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..events.event_bus import get_event_bus
from ..database.registry import RegistryDatabase

logger = logging.getLogger(__name__)


class SchemaNotifier:
    """
    Notifies downstream services and SDK instances of schema changes.

    Uses two channels:
      - SSE via EventBus ("schema.updated" event)
      - HTTP POST to /internal/schema-refresh on each registered service
    """

    def __init__(
        self,
        registry_db: RegistryDatabase,
        http_timeout: float = 5.0,
    ):
        self._registry_db = registry_db
        self._http_timeout = http_timeout

    async def notify_schema_change(
        self,
        schema_name: str,
        old_version: str,
        new_version: str,
    ) -> dict:
        """
        Notify all consumers of a schema change.

        1. Publishes "schema.updated" SSE event on the EventBus.
        2. POSTs to /internal/schema-refresh on each active service instance
           (except HeartBeat itself).

        Args:
            schema_name: Name of the updated schema (e.g. "invoices")
            old_version: Previous version (or "0.0" if new schema)
            new_version: New version string

        Returns:
            Summary dict with keys:
              - sse_published (bool)
              - callbacks_sent (int)
              - callbacks_failed (int)
              - failures (list of {service, error})
        """
        payload = {
            "schema_name": schema_name,
            "old_version": old_version,
            "new_version": new_version,
            "fetch_url": f"/api/schemas/{schema_name}/sql",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ── 1. SSE via P2-D EventBus (backwards-compat) ──────────────────
        sse_published = False
        try:
            await get_event_bus().publish("schema.updated", payload)
            sse_published = True
            logger.info(
                f"SSE event published: schema.updated "
                f"({schema_name} v{old_version} -> v{new_version})"
            )
        except Exception as e:
            logger.error(f"Failed to publish P2-D SSE event: {e}")

        # ── 1b. SSE Spec: authenticated stream + event ledger ──────────
        try:
            from ..sse.publish import publish_event
            await publish_event("schema.updated", payload)
        except Exception as e:
            logger.error(f"Failed to publish SSE ledger event: {e}")

        # ── 2. HTTP callbacks to registered services ─────────────────────
        callbacks_sent = 0
        callbacks_failed = 0
        failures = []

        try:
            instances = self._registry_db.get_all_instances(active_only=True)
        except Exception as e:
            logger.error(f"Failed to query service instances: {e}")
            instances = []

        # Filter out HeartBeat itself
        targets = [
            inst for inst in instances
            if inst.get("service_name", "").lower() != "heartbeat"
        ]

        if not targets:
            logger.info("No target services for schema change callback")
            return {
                "sse_published": sse_published,
                "callbacks_sent": 0,
                "callbacks_failed": 0,
                "failures": [],
            }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._http_timeout)
        ) as client:
            for instance in targets:
                service_name = instance.get("service_name", "unknown")
                base_url = instance.get("base_url", "")
                callback_url = f"{base_url.rstrip('/')}/internal/schema-refresh"

                try:
                    response = await client.post(callback_url, json=payload)
                    response.raise_for_status()
                    callbacks_sent += 1
                    logger.info(
                        f"Schema callback sent to {service_name} "
                        f"({callback_url}) — HTTP {response.status_code}"
                    )
                except Exception as e:
                    callbacks_failed += 1
                    error_msg = str(e)
                    failures.append({
                        "service": service_name,
                        "error": error_msg,
                    })
                    logger.warning(
                        f"Schema callback failed for {service_name} "
                        f"({callback_url}): {error_msg}"
                    )

        result = {
            "sse_published": sse_published,
            "callbacks_sent": callbacks_sent,
            "callbacks_failed": callbacks_failed,
            "failures": failures,
        }
        logger.info(
            f"Schema notification complete: {callbacks_sent} sent, "
            f"{callbacks_failed} failed"
        )
        return result


# ── Singleton ────────────────────────────────────────────────────────────

_notifier: Optional[SchemaNotifier] = None


def get_schema_notifier() -> SchemaNotifier:
    """
    Get singleton SchemaNotifier.

    Raises RuntimeError if init_schema_notifier() hasn't been called yet.
    """
    global _notifier
    if _notifier is None:
        raise RuntimeError(
            "SchemaNotifier not initialized. "
            "Call init_schema_notifier(registry_db) first."
        )
    return _notifier


def init_schema_notifier(
    registry_db: RegistryDatabase,
    http_timeout: float = 5.0,
) -> SchemaNotifier:
    """
    Initialize the singleton SchemaNotifier.

    Args:
        registry_db: RegistryDatabase instance for querying service instances.
        http_timeout: Timeout in seconds for HTTP callback POSTs.

    Returns:
        The initialized SchemaNotifier.
    """
    global _notifier
    _notifier = SchemaNotifier(
        registry_db=registry_db,
        http_timeout=http_timeout,
    )
    logger.info("SchemaNotifier initialized")
    return _notifier


def reset_schema_notifier() -> None:
    """Reset singleton (for testing)."""
    global _notifier
    _notifier = None
