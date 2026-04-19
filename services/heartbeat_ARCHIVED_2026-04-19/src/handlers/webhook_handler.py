"""
Webhook Handler — Notifies downstream services when config changes.

Per WEBHOOK_CONFIG_CONTRACT.md: HeartBeat calls POST /api/v1/webhook/config_changed
on all registered downstream services when config.db changes.

The webhook payload contains ONLY the list of changed categories — no config
values. Services then re-fetch full config via GET /api/v1/heartbeat/config.

Changed categories:
    tier_settings, eic_config, tenant_details, transforma_config,
    rbac_roles, notification_settings
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from ..database.config_db import get_config_database

logger = logging.getLogger(__name__)

# Timeout for webhook calls (quick fire — services should respond fast)
WEBHOOK_TIMEOUT = 10.0


async def notify_config_changed(
    changed: List[str],
    source: str = "heartbeat",
) -> dict:
    """
    Notify all registered downstream services that config has changed.

    Args:
        changed: List of changed categories (e.g., ["tenant_details", "tier_settings"])
        source: Who triggered the change (admin, system, etc.)

    Returns:
        Summary dict with success/failure counts
    """
    config_db = get_config_database()

    # Get all active service endpoints (backend services only — not Float/SDK)
    endpoints = config_db.execute_query(
        """SELECT service_name, api_url FROM tenant_service_endpoints
           WHERE service_name IN ('relay', 'core', 'his', 'edge')"""
    )

    if not endpoints:
        logger.info("No downstream services registered — skipping webhook notify")
        return {"notified": 0, "failed": 0, "services": []}

    payload = {
        "changed": changed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }

    results = []
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
        for ep in endpoints:
            service = ep["service_name"]
            base_url = ep["api_url"].rstrip("/")
            webhook_url = f"{base_url}/api/v1/webhook/config_changed"

            try:
                resp = await client.post(webhook_url, json=payload)
                success = resp.is_success
                results.append({
                    "service": service,
                    "url": webhook_url,
                    "status": resp.status_code,
                    "success": success,
                })
                if success:
                    logger.info(f"Webhook notify {service}: OK ({resp.status_code})")
                else:
                    logger.warning(f"Webhook notify {service}: {resp.status_code}")
            except Exception as e:
                results.append({
                    "service": service,
                    "url": webhook_url,
                    "status": None,
                    "success": False,
                    "error": str(e),
                })
                logger.warning(f"Webhook notify {service} failed: {e}")

    succeeded = sum(1 for r in results if r["success"])
    failed = len(results) - succeeded

    # Also publish SSE event for Float/SDK clients
    config_event_data = {
        "changed": changed,
        "timestamp": payload["timestamp"],
        "source": source,
    }
    try:
        from ..events import get_event_bus
        bus = get_event_bus()
        await bus.publish("config.updated", config_event_data)
    except Exception as e:
        logger.warning(f"P2-D publish config.updated failed: {e}")

    # SSE Spec: publish to authenticated stream + event ledger
    try:
        from ..sse.publish import publish_event
        await publish_event("config.updated", config_event_data)
    except Exception as e:
        logger.warning(f"SSE ledger publish config.updated failed: {e}")

    return {
        "notified": succeeded,
        "failed": failed,
        "services": results,
    }
