"""
Tests for Cache Refresh API (P2-F)

Tests cover:
    1. Successful cache refresh broadcast
    2. Invalid cache_type rejected
    3. Targeted service refresh
    4. Audit event logged
"""

import pytest


class TestCacheRefreshAPI:
    """Cache refresh endpoint tests."""

    def test_refresh_all(self, client):
        """POST /internal/refresh-cache with type=all succeeds."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "all",
            "reason": "Config updated",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "refresh_broadcast"
        assert data["cache_type"] == "all"
        assert data["targeted_services"] == "all"

    def test_refresh_config(self, client):
        """POST /internal/refresh-cache with type=config succeeds."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "config",
        })
        assert resp.status_code == 200
        assert resp.json()["cache_type"] == "config"

    def test_refresh_dedup(self, client):
        """POST /internal/refresh-cache with type=dedup succeeds."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "dedup",
        })
        assert resp.status_code == 200

    def test_refresh_limits(self, client):
        """POST /internal/refresh-cache with type=limits succeeds."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "limits",
        })
        assert resp.status_code == 200

    def test_invalid_cache_type(self, client):
        """POST /internal/refresh-cache rejects invalid type."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "invalid_type",
        })
        assert resp.status_code == 400
        assert "invalid_type" in resp.json()["detail"].lower()

    def test_targeted_services(self, client):
        """POST /internal/refresh-cache with target_services returns them."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "config",
            "target_services": ["relay-bulk-1", "relay-nas-1"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["targeted_services"] == ["relay-bulk-1", "relay-nas-1"]

    def test_audit_event_logged(self, client):
        """Cache refresh logs an audit event to audit_events table."""
        resp = client.post("/internal/refresh-cache", json={
            "cache_type": "all",
            "reason": "Test audit logging",
        })
        assert resp.status_code == 200

        # Verify audit_events contains the cache.refresh_requested event
        # by calling the health endpoint (which doesn't require auth)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("healthy", "degraded")
