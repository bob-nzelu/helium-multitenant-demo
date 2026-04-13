"""Tests for CoreConfig — defaults, env overrides, conninfo."""

import os

import pytest

from src.config import CoreConfig


class TestCoreConfigDefaults:
    """Verify all default values match MENTAL_MODEL spec."""

    def test_server_defaults(self):
        c = CoreConfig()
        assert c.host == "0.0.0.0"
        assert c.port == 8080

    def test_database_defaults(self):
        c = CoreConfig()
        assert c.db_host == "localhost"
        assert c.db_port == 5432
        assert c.db_user == "helium"
        assert c.db_password == "helium_dev"
        assert c.db_name == "helium_core"
        assert c.db_pool_min == 5
        assert c.db_pool_max == 20

    def test_logging_defaults(self):
        c = CoreConfig()
        assert c.log_level == "INFO"

    def test_processing_defaults(self):
        c = CoreConfig()
        assert c.batch_size == 100
        assert c.worker_type == "thread"

    def test_upstream_defaults(self):
        c = CoreConfig()
        assert c.heartbeat_url == "http://localhost:9000"
        assert c.edge_url == "http://localhost:8090"

    def test_sse_defaults(self):
        c = CoreConfig()
        assert c.sse_buffer_size == 1000
        assert c.sse_heartbeat_interval == 15

    def test_cors_defaults(self):
        c = CoreConfig()
        assert c.cors_origins == "*"


class TestCoreConfigConninfo:
    """Verify conninfo property builds correct psycopg3 string."""

    def test_default_conninfo(self):
        c = CoreConfig()
        assert c.conninfo == (
            "host=localhost port=5432 "
            "dbname=helium_core user=helium "
            "password=helium_dev"
        )

    def test_custom_conninfo(self):
        c = CoreConfig(db_host="pg.prod", db_port=5433, db_name="mydb",
                       db_user="admin", db_password="secret")
        assert "host=pg.prod" in c.conninfo
        assert "port=5433" in c.conninfo
        assert "dbname=mydb" in c.conninfo
        assert "user=admin" in c.conninfo
        assert "password=secret" in c.conninfo


class TestCoreConfigFromEnv:
    """Verify from_env() reads CORE_* env vars correctly."""

    def test_from_env_defaults_when_no_vars(self, monkeypatch):
        # Clear any CORE_ vars
        for key in list(os.environ):
            if key.startswith("CORE_"):
                monkeypatch.delenv(key, raising=False)
        c = CoreConfig.from_env()
        assert c.port == 8080
        assert c.db_host == "localhost"

    def test_from_env_server_overrides(self, monkeypatch):
        monkeypatch.setenv("CORE_HOST", "127.0.0.1")
        monkeypatch.setenv("CORE_PORT", "9090")
        c = CoreConfig.from_env()
        assert c.host == "127.0.0.1"
        assert c.port == 9090

    def test_from_env_database_overrides(self, monkeypatch):
        monkeypatch.setenv("CORE_DB_HOST", "pg.staging")
        monkeypatch.setenv("CORE_DB_PORT", "5433")
        monkeypatch.setenv("CORE_DB_USER", "staging_user")
        monkeypatch.setenv("CORE_DB_PASSWORD", "staging_pw")
        monkeypatch.setenv("CORE_DB_NAME", "staging_core")
        monkeypatch.setenv("CORE_DB_POOL_MIN", "2")
        monkeypatch.setenv("CORE_DB_POOL_MAX", "10")
        c = CoreConfig.from_env()
        assert c.db_host == "pg.staging"
        assert c.db_port == 5433
        assert c.db_user == "staging_user"
        assert c.db_password == "staging_pw"
        assert c.db_name == "staging_core"
        assert c.db_pool_min == 2
        assert c.db_pool_max == 10

    def test_from_env_sse_overrides(self, monkeypatch):
        monkeypatch.setenv("CORE_SSE_BUFFER_SIZE", "500")
        monkeypatch.setenv("CORE_SSE_HEARTBEAT_INTERVAL", "30")
        c = CoreConfig.from_env()
        assert c.sse_buffer_size == 500
        assert c.sse_heartbeat_interval == 30

    def test_from_env_cors_override(self, monkeypatch):
        monkeypatch.setenv("CORE_CORS_ORIGINS", "http://localhost:3000,http://app.helium.ng")
        c = CoreConfig.from_env()
        assert c.cors_origins == "http://localhost:3000,http://app.helium.ng"

    def test_from_env_processing_overrides(self, monkeypatch):
        monkeypatch.setenv("CORE_BATCH_SIZE", "50")
        monkeypatch.setenv("CORE_WORKER_TYPE", "celery")
        monkeypatch.setenv("CORE_LOG_LEVEL", "DEBUG")
        c = CoreConfig.from_env()
        assert c.batch_size == 50
        assert c.worker_type == "celery"
        assert c.log_level == "DEBUG"

    def test_from_env_upstream_overrides(self, monkeypatch):
        monkeypatch.setenv("CORE_HEARTBEAT_URL", "http://heartbeat:9000")
        monkeypatch.setenv("CORE_EDGE_URL", "http://edge:8090")
        c = CoreConfig.from_env()
        assert c.heartbeat_url == "http://heartbeat:9000"
        assert c.edge_url == "http://edge:8090"
