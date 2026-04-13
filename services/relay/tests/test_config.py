"""
Tests for RelayConfig
"""

import os
import pytest

from src.config import RelayConfig


class TestRelayConfigDefaults:
    """Test default configuration values."""

    def test_defaults(self):
        config = RelayConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8082
        assert config.instance_id == "relay-api-1"
        assert config.core_api_url == "http://localhost:8080"
        assert config.heartbeat_api_url == "http://localhost:9000"
        assert config.require_encryption is True
        assert config.private_key_path == ""
        assert config.max_files == 3
        assert config.max_file_size_mb == 10.0
        assert config.max_total_size_mb == 30.0
        assert ".pdf" in config.allowed_extensions
        assert ".xml" in config.allowed_extensions
        assert ".json" in config.allowed_extensions
        assert ".csv" in config.allowed_extensions
        assert ".xlsx" in config.allowed_extensions
        assert config.preview_timeout_s == 300
        assert config.request_timeout_s == 30
        assert config.max_retry_attempts == 5
        assert config.retry_initial_delay_s == 1.0
        assert config.poller_enabled is False
        assert config.poller_source_type == ""
        assert config.poller_interval_s == 300
        assert config.malware_scan_enabled is False
        assert config.malware_on_unavailable == "allow"
        assert config.module_cache_refresh_interval_s == 43200
        assert config.internal_service_token == ""
        # Redis + Workers
        assert config.redis_url == ""
        assert config.redis_prefix == "relay"
        assert config.rate_limit_daily == 500
        assert config.workers == 1

    def test_custom_values(self):
        config = RelayConfig(
            port=9999,
            max_files=5,
            allowed_extensions=(".pdf", ".zip"),
            poller_enabled=True,
            poller_source_type="sftp",
        )
        assert config.port == 9999
        assert config.max_files == 5
        assert config.allowed_extensions == (".pdf", ".zip")
        assert config.poller_enabled is True
        assert config.poller_source_type == "sftp"


class TestRelayConfigFromEnv:
    """Test from_env() loading."""

    def test_from_env_empty(self, monkeypatch):
        """No RELAY_* vars → all defaults."""
        # Clear any existing RELAY_ vars
        for key in list(os.environ):
            if key.startswith("RELAY_"):
                monkeypatch.delenv(key, raising=False)

        config = RelayConfig.from_env()
        assert config.port == 8082
        assert config.host == "0.0.0.0"

    def test_from_env_server(self, monkeypatch):
        monkeypatch.setenv("RELAY_HOST", "192.168.1.10")
        monkeypatch.setenv("RELAY_PORT", "9090")
        monkeypatch.setenv("RELAY_INSTANCE_ID", "relay-prod-3")

        config = RelayConfig.from_env()
        assert config.host == "192.168.1.10"
        assert config.port == 9090
        assert config.instance_id == "relay-prod-3"

    def test_from_env_upstream(self, monkeypatch):
        monkeypatch.setenv("RELAY_CORE_API_URL", "https://core.prod.internal")
        monkeypatch.setenv("RELAY_HEARTBEAT_API_URL", "https://hb.prod.internal")

        config = RelayConfig.from_env()
        assert config.core_api_url == "https://core.prod.internal"
        assert config.heartbeat_api_url == "https://hb.prod.internal"

    def test_from_env_encryption(self, monkeypatch):
        monkeypatch.setenv("RELAY_REQUIRE_ENCRYPTION", "false")
        monkeypatch.setenv("RELAY_PRIVATE_KEY_PATH", "/etc/relay/key.hex")

        config = RelayConfig.from_env()
        assert config.require_encryption is False
        assert config.private_key_path == "/etc/relay/key.hex"

    def test_from_env_encryption_true_variants(self, monkeypatch):
        for val in ("true", "1", "yes", "True", "YES"):
            monkeypatch.setenv("RELAY_REQUIRE_ENCRYPTION", val)
            config = RelayConfig.from_env()
            assert config.require_encryption is True

    def test_from_env_file_limits(self, monkeypatch):
        monkeypatch.setenv("RELAY_MAX_FILES", "10")
        monkeypatch.setenv("RELAY_MAX_FILE_SIZE_MB", "50")
        monkeypatch.setenv("RELAY_MAX_TOTAL_SIZE_MB", "100")
        monkeypatch.setenv("RELAY_ALLOWED_EXTENSIONS", ".pdf, .xml, .zip")

        config = RelayConfig.from_env()
        assert config.max_files == 10
        assert config.max_file_size_mb == 50.0
        assert config.max_total_size_mb == 100.0
        assert config.allowed_extensions == (".pdf", ".xml", ".zip")

    def test_from_env_timeouts(self, monkeypatch):
        monkeypatch.setenv("RELAY_PREVIEW_TIMEOUT_S", "600")
        monkeypatch.setenv("RELAY_REQUEST_TIMEOUT_S", "60")

        config = RelayConfig.from_env()
        assert config.preview_timeout_s == 600
        assert config.request_timeout_s == 60

    def test_from_env_retry(self, monkeypatch):
        monkeypatch.setenv("RELAY_MAX_RETRY_ATTEMPTS", "3")
        monkeypatch.setenv("RELAY_RETRY_INITIAL_DELAY_S", "2.5")

        config = RelayConfig.from_env()
        assert config.max_retry_attempts == 3
        assert config.retry_initial_delay_s == 2.5

    def test_from_env_poller(self, monkeypatch):
        monkeypatch.setenv("RELAY_POLLER_ENABLED", "true")
        monkeypatch.setenv("RELAY_POLLER_SOURCE_TYPE", "filesystem")
        monkeypatch.setenv("RELAY_POLLER_INTERVAL_S", "120")
        monkeypatch.setenv("RELAY_POLLER_COMPANY_ID", "pikwik-001")
        monkeypatch.setenv("RELAY_POLLER_DIRECTORY", "/mnt/incoming")

        config = RelayConfig.from_env()
        assert config.poller_enabled is True
        assert config.poller_source_type == "filesystem"
        assert config.poller_interval_s == 120
        assert config.poller_company_id == "pikwik-001"
        assert config.poller_directory == "/mnt/incoming"

    def test_from_env_sftp_poller(self, monkeypatch):
        monkeypatch.setenv("RELAY_POLLER_SFTP_HOST", "sftp.example.com")
        monkeypatch.setenv("RELAY_POLLER_SFTP_PORT", "2222")
        monkeypatch.setenv("RELAY_POLLER_SFTP_USER", "relay_user")
        monkeypatch.setenv("RELAY_POLLER_SFTP_KEY_PATH", "/keys/id_rsa")
        monkeypatch.setenv("RELAY_POLLER_HTTP_URL", "https://api.example.com/files")

        config = RelayConfig.from_env()
        assert config.poller_sftp_host == "sftp.example.com"
        assert config.poller_sftp_port == 2222
        assert config.poller_sftp_user == "relay_user"
        assert config.poller_sftp_key_path == "/keys/id_rsa"
        assert config.poller_http_url == "https://api.example.com/files"

    def test_from_env_module_cache(self, monkeypatch):
        monkeypatch.setenv("RELAY_MODULE_CACHE_REFRESH_INTERVAL_S", "3600")
        monkeypatch.setenv("RELAY_INTERNAL_SERVICE_TOKEN", "secret-heartbeat-token")

        config = RelayConfig.from_env()
        assert config.module_cache_refresh_interval_s == 3600
        assert config.internal_service_token == "secret-heartbeat-token"

    def test_from_env_malware(self, monkeypatch):
        monkeypatch.setenv("RELAY_MALWARE_SCAN_ENABLED", "true")
        monkeypatch.setenv("RELAY_MALWARE_CLAMD_SOCKET", "/var/run/clamav/clamd.sock")
        monkeypatch.setenv("RELAY_MALWARE_CLAMD_HOST", "clamav.local")
        monkeypatch.setenv("RELAY_MALWARE_CLAMD_PORT", "3311")
        monkeypatch.setenv("RELAY_MALWARE_SCAN_TIMEOUT_S", "60")
        monkeypatch.setenv("RELAY_MALWARE_ON_UNAVAILABLE", "block")

        config = RelayConfig.from_env()
        assert config.malware_scan_enabled is True
        assert config.malware_clamd_socket == "/var/run/clamav/clamd.sock"
        assert config.malware_clamd_host == "clamav.local"
        assert config.malware_clamd_port == 3311
        assert config.malware_scan_timeout_s == 60
        assert config.malware_on_unavailable == "block"

    def test_from_env_redis(self, monkeypatch):
        monkeypatch.setenv("RELAY_REDIS_URL", "redis://prod-redis:6379/2")
        monkeypatch.setenv("RELAY_REDIS_PREFIX", "relay-prod")
        monkeypatch.setenv("RELAY_RATE_LIMIT_DAILY", "5000")

        config = RelayConfig.from_env()
        assert config.redis_url == "redis://prod-redis:6379/2"
        assert config.redis_prefix == "relay-prod"
        assert config.rate_limit_daily == 5000

    def test_from_env_workers(self, monkeypatch):
        monkeypatch.setenv("RELAY_WORKERS", "4")

        config = RelayConfig.from_env()
        assert config.workers == 4

    def test_from_env_redis_empty_url_keeps_default(self):
        """Without RELAY_REDIS_URL, redis_url stays empty (disabled)."""
        config = RelayConfig.from_env()
        assert config.redis_url == ""
