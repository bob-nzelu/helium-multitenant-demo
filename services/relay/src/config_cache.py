"""
Tenant Config Cache — In-memory cache of HeartBeat config.

Per UNIFIED_SERVICE_BOOTSTRAP_SPEC.md: every backend service fetches full
config at startup, caches in memory, and re-fetches on webhook notification.

Usage:
    cache = ConfigCache(heartbeat_client)
    await cache.load()                  # Startup
    await cache.refresh(["tier_settings"])  # Webhook

    tier = cache.get_tenant_tier()      # Runtime read (no HTTP)
    limit = cache.get_tier_limit("daily_upload_limit", 100)
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConfigCache:
    """
    In-memory cache of HeartBeat tenant configuration.

    Populated at startup via GET /api/v1/heartbeat/config.
    Refreshed when webhook fires with changed categories.
    All runtime reads are from cache — zero HTTP calls.
    """

    def __init__(self, heartbeat_client):
        self._heartbeat = heartbeat_client
        self._config: Dict[str, Any] = {}
        self._loaded_at: Optional[str] = None
        self._is_loaded: bool = False

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def loaded_at(self) -> Optional[str]:
        return self._loaded_at

    async def load(self) -> bool:
        """
        Fetch full config from HeartBeat at startup.

        Returns True on success, False on failure (degraded mode).
        """
        try:
            self._config = await self._heartbeat.fetch_config()
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._is_loaded = True
            logger.info(
                "Config cache loaded from HeartBeat — "
                f"tenant={self.get_tenant_name()}, tier={self.get_tenant_tier()}"
            )
            return True
        except Exception as e:
            logger.warning(f"HeartBeat config fetch failed — starting degraded: {e}")
            self._is_loaded = False
            return False

    async def refresh(self, changed: Optional[List[str]] = None) -> bool:
        """
        Re-fetch config from HeartBeat (triggered by webhook).

        Args:
            changed: List of changed categories (for logging). Full config
                     is always re-fetched regardless.

        Returns True on success.
        """
        try:
            self._config = await self._heartbeat.fetch_config()
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._is_loaded = True
            logger.info(
                f"Config cache refreshed — changed={changed or 'full'}, "
                f"tenant={self.get_tenant_name()}"
            )
            return True
        except Exception as e:
            logger.warning(f"Config cache refresh failed: {e}")
            return False

    # ── Convenience accessors (zero HTTP) ────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get a top-level config key."""
        return self._config.get(key, default)

    def get_tenant(self) -> Dict[str, Any]:
        """Get tenant section."""
        return self._config.get("tenant", {})

    def get_tenant_name(self) -> str:
        return self.get_tenant().get("company_name", "unknown")

    def get_tenant_tier(self) -> str:
        return self.get_tenant().get("tier", "standard")

    def get_tenant_id(self) -> str:
        return self.get_tenant().get("tenant_id", "")

    def get_tier_limits(self) -> Dict[str, Any]:
        """Get tier_limits dict."""
        return self._config.get("tier_limits", {})

    def get_tier_limit(self, key: str, default: int = 0) -> int:
        """Get a specific tier limit value."""
        val = self.get_tier_limits().get(key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def get_feature_flags(self) -> Dict[str, bool]:
        """Get feature_flags dict."""
        return self._config.get("feature_flags", {})

    def is_feature_enabled(self, flag: str, default: bool = False) -> bool:
        """Check if a feature flag is enabled."""
        return self.get_feature_flags().get(flag, default)

    def get_firs(self) -> Dict[str, Any]:
        """Get FIRS config section."""
        return self._config.get("firs", {})

    def get_service_endpoints(self) -> List[Dict[str, Any]]:
        """Get service endpoints list."""
        return self._config.get("service_endpoints", [])

    def get_endpoint_url(self, service_name: str) -> Optional[str]:
        """Get api_url for a specific service."""
        for ep in self.get_service_endpoints():
            if ep.get("service_name") == service_name:
                return ep.get("api_url")
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Return full config dict (for debugging/health endpoint)."""
        return {
            "is_loaded": self._is_loaded,
            "loaded_at": self._loaded_at,
            "tenant_id": self.get_tenant_id(),
            "tenant_name": self.get_tenant_name(),
            "tier": self.get_tenant_tier(),
        }
