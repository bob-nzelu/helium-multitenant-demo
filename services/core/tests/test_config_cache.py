"""
Tests for TenantConfigCache and config_changed webhook.

Verifies:
- Config fetched from HeartBeat at startup
- Cache returns values after load
- Refresh re-fetches full config
- Webhook endpoint triggers refresh
- Graceful fallback when HeartBeat is down
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.config_cache import TenantConfigCache
from src.webhook import router as webhook_router


SAMPLE_CONFIG = {
    "tier": "standard",
    "company_id": "acme-001",
    "company_name": "Acme Corp",
    "tier_settings": {
        "max_workers": 10,
        "max_file_size_mb": 50,
        "max_invoices_per_batch": 1000,
    },
    "eic_config": {
        "signing_enabled": True,
    },
}


# ── TenantConfigCache Tests ──────────────────────────────────────────


@pytest.mark.asyncio
class TestTenantConfigCache:

    async def test_load_success(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        result = await cache.load()

        assert result is True
        assert cache.is_loaded is True
        assert cache.get("tier") == "standard"
        assert cache.get("company_id") == "acme-001"
        hb.fetch_config.assert_called_once()

    async def test_load_failure_returns_false(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(side_effect=Exception("HeartBeat down"))

        cache = TenantConfigCache(hb)
        result = await cache.load()

        assert result is False
        assert cache.is_loaded is False
        assert cache.get("tier") is None

    async def test_get_with_default(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        await cache.load()

        assert cache.get("nonexistent", "fallback") == "fallback"
        assert cache.get("tier", "fallback") == "standard"

    async def test_get_section(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        await cache.load()

        tier_settings = cache.get_section("tier_settings")
        assert tier_settings["max_workers"] == 10
        assert tier_settings["max_file_size_mb"] == 50

    async def test_get_section_missing_returns_empty_dict(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        await cache.load()

        assert cache.get_section("nonexistent") == {}

    async def test_refresh_re_fetches(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        await cache.load()
        assert cache.get("tier") == "standard"

        updated_config = {**SAMPLE_CONFIG, "tier": "pro"}
        hb.fetch_config = AsyncMock(return_value=updated_config)

        await cache.refresh(changed=["tier_settings"])
        assert cache.get("tier") == "pro"

    async def test_raw_returns_copy(self):
        hb = AsyncMock()
        hb.fetch_config = AsyncMock(return_value=SAMPLE_CONFIG)

        cache = TenantConfigCache(hb)
        await cache.load()

        raw = cache.raw
        raw["tier"] = "tampered"
        assert cache.get("tier") == "standard"


# ── Webhook Endpoint Tests ───────────────────────────────────────────


class TestConfigChangedWebhook:

    def _create_app(self, config_cache=None, audit_logger=None):
        app = FastAPI()
        app.state.config_cache = config_cache
        app.state.audit_logger = audit_logger
        app.include_router(webhook_router)
        return app

    def test_webhook_triggers_refresh(self):
        cache = MagicMock()
        cache.refresh = AsyncMock(return_value=True)

        app = self._create_app(config_cache=cache)
        client = TestClient(app)

        resp = client.post("/api/v1/webhook/config_changed", json={
            "changed": ["tier_settings"],
            "timestamp": "2026-03-31T10:00:00Z",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["refreshed"] is True
        cache.refresh.assert_called_once_with(changed=["tier_settings"])

    def test_webhook_handles_refresh_failure(self):
        cache = MagicMock()
        cache.refresh = AsyncMock(return_value=False)

        app = self._create_app(config_cache=cache)
        client = TestClient(app)

        resp = client.post("/api/v1/webhook/config_changed", json={
            "changed": ["eic_config"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["refreshed"] is False
        assert "failed" in data["message"].lower() or "previous" in data["message"].lower()

    def test_webhook_no_cache_initialized(self):
        app = self._create_app(config_cache=None)
        client = TestClient(app)

        resp = client.post("/api/v1/webhook/config_changed", json={
            "changed": ["tier_settings"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["refreshed"] is False

    def test_webhook_multiple_changed_categories(self):
        cache = MagicMock()
        cache.refresh = AsyncMock(return_value=True)

        app = self._create_app(config_cache=cache)
        client = TestClient(app)

        resp = client.post("/api/v1/webhook/config_changed", json={
            "changed": ["tier_settings", "eic_config", "tenant_details"],
            "source": "config.db",
        })

        assert resp.status_code == 200
        cache.refresh.assert_called_once_with(
            changed=["tier_settings", "eic_config", "tenant_details"]
        )

    def test_webhook_logs_audit_event(self):
        cache = MagicMock()
        cache.refresh = AsyncMock(return_value=True)
        audit = MagicMock()
        audit.log = AsyncMock()

        app = self._create_app(config_cache=cache, audit_logger=audit)
        client = TestClient(app)

        client.post("/api/v1/webhook/config_changed", json={
            "changed": ["tenant_details"],
        })

        audit.log.assert_called_once()
        call_kwargs = audit.log.call_args[1]
        assert call_kwargs["event_type"] == "config.changed"
        assert "tenant_details" in call_kwargs["metadata"]["changed"]
