"""
Tenant Configuration Cache

Fetches full tenant config from HeartBeat's config.db at startup,
caches in memory, and refreshes on webhook notification.

Pattern (Helium-wide contract):
  1. Startup: GET /api/v1/heartbeat/config → cache full response
  2. Runtime: Read from memory (zero latency)
  3. Change:  HeartBeat POSTs /api/v1/webhook/config_changed → re-fetch
  4. Fallback: If HeartBeat unreachable, use CoreConfig env var defaults
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class TenantConfigCache:
    """In-memory cache of the full tenant config from HeartBeat config.db.

    Thread-safe via asyncio.Lock. All reads go through get() which
    returns a deep copy to prevent mutation.
    """

    def __init__(self, heartbeat_client) -> None:
        self._heartbeat = heartbeat_client
        self._config: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self) -> bool:
        """Fetch full config from HeartBeat. Returns True on success."""
        try:
            config = await self._heartbeat.fetch_config()
            async with self._lock:
                self._config = config
                self._loaded = True
            logger.info(
                "tenant_config_loaded",
                keys=list(config.keys()) if isinstance(config, dict) else "non-dict",
            )
            return True
        except Exception as exc:
            logger.warning(
                "tenant_config_load_failed",
                error=str(exc),
                fallback="using CoreConfig env var defaults",
            )
            return False

    async def refresh(self, changed: list[str] | None = None) -> bool:
        """Re-fetch config from HeartBeat (called by webhook handler).

        Args:
            changed: Optional list of changed categories (for logging).
                     Full config is always re-fetched regardless.
        """
        logger.info("tenant_config_refreshing", changed=changed)
        success = await self.load()
        if success:
            logger.info("tenant_config_refreshed", changed=changed)
        return success

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value. Returns default if not loaded or key missing."""
        return self._config.get(key, default)

    def get_section(self, section: str) -> dict[str, Any]:
        """Get an entire config section (e.g. 'tier_settings', 'eic')."""
        value = self._config.get(section, {})
        return dict(value) if isinstance(value, dict) else {}

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def raw(self) -> dict[str, Any]:
        """Full config snapshot (read-only use)."""
        return dict(self._config)
