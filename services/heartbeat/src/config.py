"""
HeartBeat Service Configuration

Dataclass with from_env() classmethod loading HEARTBEAT_* env vars.
Supports Primary and Satellite modes.

Environment Variables:
    HEARTBEAT_MODE              — "primary" (default) or "satellite"
    HEARTBEAT_HOST              — Bind host (default: 0.0.0.0)
    HEARTBEAT_PORT              — Bind port (default: 9000)

    # Database
    HEARTBEAT_BLOB_DB_PATH      — Path to blob.db
    HEARTBEAT_REGISTRY_DB_PATH  — Path to registry.db

    # Blob Storage (Filesystem)
    HEARTBEAT_BLOB_STORAGE_ROOT — Root directory for file blob storage

    # Primary URL (Satellite only)
    HEARTBEAT_PRIMARY_URL       — URL of HeartBeat Primary (Satellite mode)

    # Limits
    HEARTBEAT_DEFAULT_DAILY_LIMIT — Default daily file limit per company (default: 1000)

    # Auth
    HEARTBEAT_AUTH_ENABLED      — Enable Bearer token auth (default: true)

    # Auth Database (Part 4)
    HEARTBEAT_AUTH_DB_PATH      — Path to auth.db (default: databases/auth.db)
    HEARTBEAT_AUTH_DB_KEY       — SQLCipher encryption key for auth.db
    HEARTBEAT_JWT_PRIVATE_KEY_PATH — Ed25519 private key PEM
    HEARTBEAT_JWT_PUBLIC_KEY_PATH  — Ed25519 public key PEM
    HEARTBEAT_SESSION_HOURS     — Session validity in hours (default: 8)
    HEARTBEAT_JWT_EXPIRY_MINUTES — JWT token lifetime in minutes (default: 30)

    # License Database
    HEARTBEAT_LICENSE_DB_PATH   — Path to license.db (default: databases/license.db)

    # Schemas
    HEARTBEAT_SCHEMAS_DIR       — Path to canonical schemas directory (default: databases/schemas/)
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HeartBeatConfig:
    """HeartBeat service configuration."""

    # ── Service ─────────────────────────────────────────────────────────
    mode: str = "primary"                       # "primary" or "satellite"
    host: str = "0.0.0.0"
    port: int = 9000
    service_name: str = "heartbeat"

    # ── Database ────────────────────────────────────────────────────────
    blob_db_path: str = ""                      # Empty = auto-detect relative to src/
    registry_db_path: str = ""                  # Empty = auto-detect (databases/registry.db)
    config_db_path: str = ""                    # Empty = auto-detect (databases/config.db)

    # ── Blob Storage (Filesystem) ───────────────────────────────────────
    blob_storage_root: str = ""                 # Filesystem root for blob storage

    # ── Primary URL (Satellite only) ────────────────────────────────────
    primary_url: str = ""                       # e.g., "http://10.0.1.5:9000"

    # ── Limits ──────────────────────────────────────────────────────────
    default_daily_limit: int = 1000

    # ── Auth ────────────────────────────────────────────────────────────
    auth_enabled: bool = True

    # ── PostgreSQL ────────────────────────────────────────────────────
    pg_dsn: str = ""                              # Full DSN overrides individual pg_* fields
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_database: str = "heartbeat"
    pg_min_connections: int = 2
    pg_max_connections: int = 10

    # ── Auth Database (Part 4) ────────────────────────────────────────
    auth_db_path: str = ""                        # Legacy SQLite path (dev only)
    auth_db_key: str = ""                         # Legacy SQLCipher key (dev only)
    jwt_private_key_path: str = ""                # Empty = auto-detect (databases/keys/jwt_private.pem)
    jwt_public_key_path: str = ""                 # Empty = auto-detect (databases/keys/jwt_public.pem)
    session_hours: int = 8                        # Session hard cap in hours (re-auth required)
    jwt_expiry_minutes: int = 30                  # JWT token lifetime in minutes (silent refresh)
    cipher_window_seconds: int = 540              # Cipher text time window (~9 min)
    max_concurrent_sessions: int = 1              # Default concurrent session limit

    # ── License Database ──────────────────────────────────────────────
    license_db_path: str = ""                   # Empty = auto-detect (databases/license.db)

    # ── Deployment ─────────────────────────────────────────────────────
    tier: str = "test"                          # "test", "standard", "pro", "enterprise"
    service_dir: str = ""                       # Root dir for managed service executables
    log_dir: str = ""                           # Root dir for service log files

    # ── Retention ───────────────────────────────────────────────────────
    retention_years: int = 7                    # FIRS compliance: 7-year retention

    # ── Tenant ────────────────────────────────────────────────────────
    company_id: str = "default"                 # Tenant company ID (SSE Spec event_ledger)

    # ── Schemas ────────────────────────────────────────────────────────
    schemas_dir: str = ""                       # Empty = auto-detect (databases/schemas/)

    # ── Migrations ───────────────────────────────────────────────────────
    auto_migrate: bool = True                   # Run pending migrations on startup
    migrations_base_dir: str = ""               # Empty = auto-detect (databases/migrations/)

    @property
    def is_primary(self) -> bool:
        return self.mode == "primary"

    @property
    def is_satellite(self) -> bool:
        return self.mode == "satellite"

    def get_pg_dsn(self) -> str:
        """Build PostgreSQL connection string."""
        if self.pg_dsn:
            return self.pg_dsn
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    def get_blob_db_path(self) -> str:
        """Resolve blob.db path (auto-detect if not set)."""
        if self.blob_db_path:
            return self.blob_db_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "blob.db",
        )

    def get_registry_db_path(self) -> str:
        """Resolve registry.db path (auto-detect if not set)."""
        if self.registry_db_path:
            return self.registry_db_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "registry.db",
        )

    def get_config_db_path(self) -> str:
        """Resolve config.db path (auto-detect if not set)."""
        if self.config_db_path:
            return self.config_db_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "config.db",
        )

    def get_license_db_path(self) -> str:
        """Resolve license.db path (auto-detect if not set)."""
        if self.license_db_path:
            return self.license_db_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "license.db",
        )

    def get_auth_db_path(self) -> str:
        """Resolve auth.db path (auto-detect if not set)."""
        if self.auth_db_path:
            return self.auth_db_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "auth.db",
        )

    def get_jwt_private_key_path(self) -> str:
        """Resolve Ed25519 private key path (auto-detect if not set)."""
        if self.jwt_private_key_path:
            return self.jwt_private_key_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "keys",
            "jwt_private.pem",
        )

    def get_jwt_public_key_path(self) -> str:
        """Resolve Ed25519 public key path (auto-detect if not set)."""
        if self.jwt_public_key_path:
            return self.jwt_public_key_path
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "keys",
            "jwt_public.pem",
        )

    def get_service_dir(self) -> str:
        """Resolve managed service executables directory (auto-detect if not set)."""
        if self.service_dir:
            return self.service_dir
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "services",
        )

    def get_log_dir(self) -> str:
        """Resolve service log files directory (auto-detect if not set)."""
        if self.log_dir:
            return self.log_dir
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "logs",
        )

    @property
    def is_headless(self) -> bool:
        """Pro/Enterprise tiers run headless (no tray app)."""
        return self.tier in ("pro", "enterprise")

    @property
    def uses_nssm(self) -> bool:
        """Pro/Enterprise tiers use NSSM for HeartBeat's own lifecycle."""
        return self.tier in ("pro", "enterprise")

    def get_blob_storage_root(self) -> str:
        """Resolve filesystem blob storage root."""
        if self.blob_storage_root:
            return self.blob_storage_root
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "dev_blobs",
        )

    def get_schemas_dir(self) -> str:
        """Resolve canonical schemas directory (auto-detect if not set)."""
        if self.schemas_dir:
            return self.schemas_dir
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "schemas",
        )

    def get_migrations_base_dir(self) -> str:
        """Resolve migrations base directory (auto-detect if not set)."""
        if self.migrations_base_dir:
            return self.migrations_base_dir
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "databases",
            "migrations",
        )

    @classmethod
    def from_env(cls) -> "HeartBeatConfig":
        """Load configuration from HEARTBEAT_* environment variables."""
        kwargs = {}

        env_map = {
            "mode": ("HEARTBEAT_MODE", str),
            "host": ("HEARTBEAT_HOST", str),
            "port": ("HEARTBEAT_PORT", int),
            "blob_db_path": ("HEARTBEAT_BLOB_DB_PATH", str),
            "registry_db_path": ("HEARTBEAT_REGISTRY_DB_PATH", str),
            "config_db_path": ("HEARTBEAT_CONFIG_DB_PATH", str),
            "blob_storage_root": ("HEARTBEAT_BLOB_STORAGE_ROOT", str),
            "primary_url": ("HEARTBEAT_PRIMARY_URL", str),
            "default_daily_limit": ("HEARTBEAT_DEFAULT_DAILY_LIMIT", int),
            "auth_enabled": ("HEARTBEAT_AUTH_ENABLED", bool),
            "license_db_path": ("HEARTBEAT_LICENSE_DB_PATH", str),
            "auth_db_path": ("HEARTBEAT_AUTH_DB_PATH", str),
            "auth_db_key": ("HEARTBEAT_AUTH_DB_KEY", str),
            "jwt_private_key_path": ("HEARTBEAT_JWT_PRIVATE_KEY_PATH", str),
            "jwt_public_key_path": ("HEARTBEAT_JWT_PUBLIC_KEY_PATH", str),
            "session_hours": ("HEARTBEAT_SESSION_HOURS", int),
            "jwt_expiry_minutes": ("HEARTBEAT_JWT_EXPIRY_MINUTES", int),
            "cipher_window_seconds": ("HEARTBEAT_CIPHER_WINDOW_SECONDS", int),
            "max_concurrent_sessions": ("HEARTBEAT_MAX_CONCURRENT_SESSIONS", int),
            "retention_years": ("HEARTBEAT_RETENTION_YEARS", int),
            "schemas_dir": ("HEARTBEAT_SCHEMAS_DIR", str),
            "tier": ("HEARTBEAT_TIER", str),
            "service_dir": ("HEARTBEAT_SERVICE_DIR", str),
            "log_dir": ("HEARTBEAT_LOG_DIR", str),
            "company_id": ("HEARTBEAT_COMPANY_ID", str),
            "auto_migrate": ("HEARTBEAT_AUTO_MIGRATE", bool),
            "migrations_base_dir": ("HEARTBEAT_MIGRATIONS_BASE_DIR", str),
            # PostgreSQL
            "pg_dsn": ("HEARTBEAT_PG_DSN", str),
            "pg_host": ("HEARTBEAT_PG_HOST", str),
            "pg_port": ("HEARTBEAT_PG_PORT", int),
            "pg_user": ("HEARTBEAT_PG_USER", str),
            "pg_password": ("HEARTBEAT_PG_PASSWORD", str),
            "pg_database": ("HEARTBEAT_PG_DATABASE", str),
            "pg_min_connections": ("HEARTBEAT_PG_MIN_CONNECTIONS", int),
            "pg_max_connections": ("HEARTBEAT_PG_MAX_CONNECTIONS", int),
        }

        for field_name, (env_var, field_type) in env_map.items():
            value = os.environ.get(env_var)
            if value is not None:
                if field_type is bool:
                    kwargs[field_name] = value.lower() in ("true", "1", "yes")
                elif field_type is int:
                    kwargs[field_name] = int(value)
                else:
                    kwargs[field_name] = value

        return cls(**kwargs)


# Singleton
_config: Optional[HeartBeatConfig] = None


def get_config() -> HeartBeatConfig:
    """Get singleton HeartBeatConfig (loads from env on first call)."""
    global _config
    if _config is None:
        _config = HeartBeatConfig.from_env()
    return _config


def set_config(config: HeartBeatConfig) -> None:
    """Override config (for testing)."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    _config = None
