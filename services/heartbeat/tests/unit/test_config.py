"""
Tests for HeartBeat Configuration

Tests config.py — HeartBeatConfig dataclass and from_env().
"""

import os
import pytest


class TestHeartBeatConfig:
    """Tests for HeartBeatConfig."""

    def test_default_config(self):
        """Default config has sensible values."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig()
        assert config.mode == "primary"
        assert config.port == 9000
        assert config.default_daily_limit == 1000
        assert config.retention_years == 7

    def test_is_primary(self):
        """is_primary returns True for primary mode."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(mode="primary")
        assert config.is_primary is True
        assert config.is_satellite is False

    def test_is_satellite(self):
        """is_satellite returns True for satellite mode."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(mode="satellite")
        assert config.is_primary is False
        assert config.is_satellite is True

    def test_from_env(self, monkeypatch):
        """from_env reads HEARTBEAT_* environment variables."""
        monkeypatch.setenv("HEARTBEAT_MODE", "satellite")
        monkeypatch.setenv("HEARTBEAT_PORT", "9999")
        monkeypatch.setenv("HEARTBEAT_DEFAULT_DAILY_LIMIT", "500")

        from src.config import HeartBeatConfig
        config = HeartBeatConfig.from_env()
        assert config.mode == "satellite"
        assert config.port == 9999
        assert config.default_daily_limit == 500

    def test_from_env_bool_parsing(self, monkeypatch):
        """Boolean env vars accept true/1/yes."""
        for val in ("true", "1", "yes", "True", "YES"):
            monkeypatch.setenv("HEARTBEAT_AUTH_ENABLED", val)
            from src.config import HeartBeatConfig
            config = HeartBeatConfig.from_env()
            assert config.auth_enabled is True

        for val in ("false", "0", "no"):
            monkeypatch.setenv("HEARTBEAT_AUTH_ENABLED", val)
            config = HeartBeatConfig.from_env()
            assert config.auth_enabled is False

    def test_get_blob_db_path_auto(self):
        """Auto-detect db path when not set."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(blob_db_path="")
        path = config.get_blob_db_path()
        assert path.endswith("blob.db")
        assert "databases" in path

    def test_get_blob_db_path_explicit(self):
        """Use explicit db path when set."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(blob_db_path="/custom/path/blob.db")
        assert config.get_blob_db_path() == "/custom/path/blob.db"

    def test_get_registry_db_path_auto(self):
        """Auto-detect registry.db path when not set."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(registry_db_path="")
        path = config.get_registry_db_path()
        assert path.endswith("registry.db")
        assert "databases" in path

    def test_get_blob_storage_root_auto(self):
        """Auto-detect blob storage root when not set."""
        from src.config import HeartBeatConfig
        config = HeartBeatConfig(blob_storage_root="")
        root = config.get_blob_storage_root()
        assert "dev_blobs" in root

    def test_from_env_blob_storage(self, monkeypatch):
        """Blob storage root reads from env."""
        monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", "/tmp/blobs")
        from src.config import HeartBeatConfig
        config = HeartBeatConfig.from_env()
        assert config.blob_storage_root == "/tmp/blobs"


class TestConfigSingleton:
    """Tests for config singleton management."""

    def test_reset_config(self):
        """reset_config clears the singleton."""
        from src.config import get_config, reset_config, set_config, HeartBeatConfig

        # Set a custom config
        custom = HeartBeatConfig(port=1234)
        set_config(custom)
        assert get_config().port == 1234

        # Reset
        reset_config()

        # Next get_config() creates fresh from env
        config = get_config()
        assert config.port == 9000  # Default

        # Cleanup
        reset_config()
