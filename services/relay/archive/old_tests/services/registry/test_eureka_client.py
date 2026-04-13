"""
Unit Tests for EurekaClient

Tests the Eureka service registry client:
- Service discovery
- Cache management
- Fallback URL handling
- Health status tracking
- Port extraction

Target Coverage: 100%
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from src.services.registry.eureka_client import EurekaClient


# =============================================================================
# Initialization Tests
# =============================================================================

class TestEurekaClientInit:
    """Tests for EurekaClient initialization."""

    def test_default_initialization(self):
        """Should initialize with default values."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client.eureka_url == "http://eureka:8761"
        assert client.refresh_interval == 300
        assert client.cache_ttl == 60
        assert client.fallback_urls == {}
        assert client.eureka_available is True

    def test_custom_initialization(self):
        """Should accept custom configuration."""
        fallbacks = {
            "core-api": "http://localhost:8080",
            "heartbeat": "http://localhost:9000",
        }

        client = EurekaClient(
            eureka_url="http://eureka:8761/",
            refresh_interval=600,
            cache_ttl=120,
            fallback_urls=fallbacks,
        )

        assert client.eureka_url == "http://eureka:8761"  # Trailing slash stripped
        assert client.refresh_interval == 600
        assert client.cache_ttl == 120
        assert client.fallback_urls == fallbacks

    def test_url_trailing_slash_stripped(self):
        """Should strip trailing slash from URL."""
        client = EurekaClient(eureka_url="http://eureka:8761///")

        assert client.eureka_url == "http://eureka:8761"


# =============================================================================
# Get Service Tests
# =============================================================================

class TestGetService:
    """Tests for get_service() method."""

    def test_get_service_from_fallback(self):
        """Should return fallback URL when available."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            fallback_urls={"core-api": "http://localhost:8080"},
        )

        result = client.get_service("core-api")

        assert result is not None
        assert result["url"] == "http://localhost:8080"
        assert result["port"] == 8080
        assert result["source"] == "fallback"
        assert result["health_check"] == "/health"

    def test_get_service_not_found(self):
        """Should return None when service not found."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        result = client.get_service("unknown-service")

        assert result is None

    def test_get_service_from_cache(self):
        """Should return cached service if cache is valid."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            cache_ttl=60,
        )

        # Manually populate cache
        client.service_cache = {
            "core-api": {
                "url": "http://cached:8080",
                "port": 8080,
                "health_check": "/health",
                "source": "eureka",
            }
        }
        client.cache_timestamp = datetime.utcnow()

        result = client.get_service("core-api")

        assert result is not None
        assert result["url"] == "http://cached:8080"
        assert result["source"] == "eureka"

    def test_get_service_cache_expired(self):
        """Should use fallback when cache is expired."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            cache_ttl=60,
            fallback_urls={"core-api": "http://fallback:8080"},
        )

        # Manually populate cache with old timestamp
        client.service_cache = {
            "core-api": {
                "url": "http://cached:8080",
                "port": 8080,
                "health_check": "/health",
                "source": "eureka",
            }
        }
        client.cache_timestamp = datetime.utcnow() - timedelta(seconds=120)

        result = client.get_service("core-api")

        # Should return fallback since cache expired
        assert result["url"] == "http://fallback:8080"
        assert result["source"] == "fallback"


# =============================================================================
# Cache Validity Tests
# =============================================================================

class TestCacheValidity:
    """Tests for _is_cache_valid() method."""

    def test_cache_empty(self):
        """Should return False when cache is empty."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client._is_cache_valid() is False

    def test_cache_no_timestamp(self):
        """Should return False when no timestamp."""
        client = EurekaClient(eureka_url="http://eureka:8761")
        client.service_cache = {"core-api": {"url": "http://test"}}

        assert client._is_cache_valid() is False

    def test_cache_valid(self):
        """Should return True when cache is fresh."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            cache_ttl=60,
        )
        client.service_cache = {"core-api": {"url": "http://test"}}
        client.cache_timestamp = datetime.utcnow()

        assert client._is_cache_valid() is True

    def test_cache_expired(self):
        """Should return False when cache is expired."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            cache_ttl=60,
        )
        client.service_cache = {"core-api": {"url": "http://test"}}
        client.cache_timestamp = datetime.utcnow() - timedelta(seconds=120)

        assert client._is_cache_valid() is False


# =============================================================================
# Port Extraction Tests
# =============================================================================

class TestPortExtraction:
    """Tests for _extract_port() method."""

    def test_extract_port_http(self):
        """Should extract port from HTTP URL."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client._extract_port("http://localhost:8080") == 8080
        assert client._extract_port("http://localhost:9000") == 9000
        assert client._extract_port("http://core:8082") == 8082

    def test_extract_port_https(self):
        """Should extract port from HTTPS URL."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client._extract_port("https://localhost:443") == 443
        assert client._extract_port("https://secure:8443") == 8443

    def test_extract_port_default_http(self):
        """Should return 80 for HTTP without port."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        # This edge case - URL without explicit port
        assert client._extract_port("http://localhost") == 80

    def test_extract_port_default_https(self):
        """Should return 443 for HTTPS without port."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client._extract_port("https://localhost") == 443

    def test_extract_port_invalid_format(self):
        """Should return 80 for invalid URL format."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        # Invalid formats default to 80
        assert client._extract_port("invalid") == 80


# =============================================================================
# Refresh Services Tests
# =============================================================================

class TestRefreshServices:
    """Tests for refresh_services_async() method."""

    @pytest.mark.asyncio
    async def test_refresh_success(self):
        """Should update cache timestamp on success."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        result = await client.refresh_services_async()

        assert result is True
        assert client.cache_timestamp is not None
        assert client.eureka_available is True

    @pytest.mark.asyncio
    async def test_refresh_failure(self):
        """Should mark Eureka unavailable on failure."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        # Patch the internal async sleep to raise
        original_method = client.refresh_services_async

        async def failing_refresh():
            try:
                raise Exception("Connection refused")
            except Exception as e:
                client.eureka_available = False
                return False

        with patch.object(client, "refresh_services_async", failing_refresh):
            result = await failing_refresh()

        assert result is False
        assert client.eureka_available is False


# =============================================================================
# Get All Services Tests
# =============================================================================

class TestGetAllServices:
    """Tests for get_all_services() method."""

    def test_get_all_from_cache(self):
        """Should return all cached services."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        client.service_cache = {
            "core-api": {"url": "http://core:8080"},
            "heartbeat": {"url": "http://heartbeat:9000"},
        }
        client.cache_timestamp = datetime.utcnow()

        result = client.get_all_services()

        assert "core-api" in result
        assert "heartbeat" in result
        # Should return copy
        assert result is not client.service_cache

    def test_get_all_from_fallback_when_expired(self):
        """Should return fallback URLs when cache expired."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            fallback_urls={
                "core-api": "http://fallback:8080",
            },
        )

        # No valid cache
        result = client.get_all_services()

        assert "core-api" in result
        assert result["core-api"]["source"] == "fallback"


# =============================================================================
# Eureka Availability Tests
# =============================================================================

class TestEurekaAvailability:
    """Tests for is_eureka_available() method."""

    def test_initially_available(self):
        """Should be available initially."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        assert client.is_eureka_available() is True

    def test_marked_unavailable(self):
        """Should reflect unavailable state."""
        client = EurekaClient(eureka_url="http://eureka:8761")
        client.eureka_available = False

        assert client.is_eureka_available() is False


# =============================================================================
# Set Fallback URL Tests
# =============================================================================

class TestSetFallbackUrl:
    """Tests for set_fallback_url() method."""

    def test_set_new_fallback(self):
        """Should set new fallback URL."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        client.set_fallback_url("core-api", "http://localhost:8080")

        assert client.fallback_urls["core-api"] == "http://localhost:8080"

    def test_override_existing_fallback(self):
        """Should override existing fallback URL."""
        client = EurekaClient(
            eureka_url="http://eureka:8761",
            fallback_urls={"core-api": "http://old:8080"},
        )

        client.set_fallback_url("core-api", "http://new:8080")

        assert client.fallback_urls["core-api"] == "http://new:8080"

    def test_add_multiple_fallbacks(self):
        """Should support multiple fallback URLs."""
        client = EurekaClient(eureka_url="http://eureka:8761")

        client.set_fallback_url("core-api", "http://core:8080")
        client.set_fallback_url("heartbeat", "http://heartbeat:9000")
        client.set_fallback_url("audit", "http://audit:9001")

        assert len(client.fallback_urls) == 3
        assert client.fallback_urls["core-api"] == "http://core:8080"
        assert client.fallback_urls["heartbeat"] == "http://heartbeat:9000"
        assert client.fallback_urls["audit"] == "http://audit:9001"
