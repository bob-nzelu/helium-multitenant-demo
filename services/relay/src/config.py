"""
Relay-API Configuration

Single dataclass with from_env() classmethod loading RELAY_* environment variables.
Follows the Helium config convention (see HIS, PDP configs).

All settings have sensible defaults for local development.
Production values come from environment variables.
"""

import os
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class RelayConfig:
    """
    Relay-API service configuration.

    Load from environment:
        config = RelayConfig.from_env()

    All RELAY_* env vars are optional — defaults target local dev.
    """

    # ── Server ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8082
    instance_id: str = "relay-api-1"

    # ── Upstream services ─────────────────────────────────────────────────
    core_api_url: str = "http://localhost:8080"
    heartbeat_api_url: str = "http://localhost:9000"
    heartbeat_api_key: str = ""       # RELAY_HEARTBEAT_API_KEY (service creds for HeartBeat)
    heartbeat_api_secret: str = ""    # RELAY_HEARTBEAT_API_SECRET

    # ── Encryption ────────────────────────────────────────────────────────
    require_encryption: bool = True
    private_key_path: str = ""
    # Empty = auto-generate ephemeral key (dev/test only)

    # ── File limits ───────────────────────────────────────────────────────
    max_files: int = 3
    max_file_size_mb: float = 10.0
    max_total_size_mb: float = 30.0
    allowed_extensions: Tuple[str, ...] = (
        ".pdf", ".xml", ".json", ".csv", ".xlsx",
    )

    # ── Timeouts ──────────────────────────────────────────────────────────
    preview_timeout_s: int = 300       # 5 minutes for bulk preview
    request_timeout_s: int = 30        # General HTTP request timeout

    # ── Retry ─────────────────────────────────────────────────────────────
    max_retry_attempts: int = 5
    retry_initial_delay_s: float = 1.0

    # ── Poller ────────────────────────────────────────────────────────────
    poller_enabled: bool = False
    poller_source_type: str = ""       # filesystem | sftp | http
    poller_interval_s: int = 300
    poller_company_id: str = ""
    poller_directory: str = ""         # For filesystem source
    poller_sftp_host: str = ""         # For SFTP source
    poller_sftp_port: int = 22
    poller_sftp_user: str = ""
    poller_sftp_key_path: str = ""
    poller_http_url: str = ""          # For HTTP source

    # ── Transforma module cache ──────────────────────────────────────────
    module_cache_refresh_interval_s: int = 43200  # 12 hours
    internal_service_token: str = ""              # For /internal/ auth from HeartBeat

    # ── Redis (rate limiting) ───────────────────────────────────────────
    redis_url: str = ""                 # Empty = disabled (graceful degradation)
    redis_prefix: str = "relay"
    rate_limit_daily: int = 500         # Default daily uploads per company

    # ── Workers ──────────────────────────────────────────────────────────
    workers: int = 1                    # uvicorn --workers (production)

    # ── Malware scanning ──────────────────────────────────────────────────
    malware_scan_enabled: bool = False
    malware_clamd_socket: str = ""
    malware_clamd_host: str = "localhost"
    malware_clamd_port: int = 3310
    malware_scan_timeout_s: int = 30
    malware_on_unavailable: str = "allow"  # "allow" | "block"

    @classmethod
    def from_env(cls) -> "RelayConfig":
        """
        Load configuration from RELAY_* environment variables.

        Every field maps to RELAY_{FIELD_NAME_UPPER}. For example:
            port          → RELAY_PORT
            core_api_url  → RELAY_CORE_API_URL
            max_files     → RELAY_MAX_FILES

        Returns:
            RelayConfig with environment overrides applied.
        """
        kwargs = {}

        # Helper: read env var, return None if not set
        def env(name: str) -> str | None:
            return os.environ.get(f"RELAY_{name}")

        # ── Server
        if v := env("HOST"):
            kwargs["host"] = v
        if v := env("PORT"):
            kwargs["port"] = int(v)
        if v := env("INSTANCE_ID"):
            kwargs["instance_id"] = v

        # ── Upstream services
        if v := env("CORE_API_URL"):
            kwargs["core_api_url"] = v
        if v := env("HEARTBEAT_API_URL"):
            kwargs["heartbeat_api_url"] = v
        if v := env("HEARTBEAT_API_KEY"):
            kwargs["heartbeat_api_key"] = v
        if v := env("HEARTBEAT_API_SECRET"):
            kwargs["heartbeat_api_secret"] = v

        # ── Encryption
        if v := env("REQUIRE_ENCRYPTION"):
            kwargs["require_encryption"] = v.lower() in ("true", "1", "yes")
        if v := env("PRIVATE_KEY_PATH"):
            kwargs["private_key_path"] = v

        # ── File limits
        if v := env("MAX_FILES"):
            kwargs["max_files"] = int(v)
        if v := env("MAX_FILE_SIZE_MB"):
            kwargs["max_file_size_mb"] = float(v)
        if v := env("MAX_TOTAL_SIZE_MB"):
            kwargs["max_total_size_mb"] = float(v)
        if v := env("ALLOWED_EXTENSIONS"):
            kwargs["allowed_extensions"] = tuple(
                ext.strip().lower() for ext in v.split(",")
            )

        # ── Timeouts
        if v := env("PREVIEW_TIMEOUT_S"):
            kwargs["preview_timeout_s"] = int(v)
        if v := env("REQUEST_TIMEOUT_S"):
            kwargs["request_timeout_s"] = int(v)

        # ── Retry
        if v := env("MAX_RETRY_ATTEMPTS"):
            kwargs["max_retry_attempts"] = int(v)
        if v := env("RETRY_INITIAL_DELAY_S"):
            kwargs["retry_initial_delay_s"] = float(v)

        # ── Poller
        if v := env("POLLER_ENABLED"):
            kwargs["poller_enabled"] = v.lower() in ("true", "1", "yes")
        if v := env("POLLER_SOURCE_TYPE"):
            kwargs["poller_source_type"] = v
        if v := env("POLLER_INTERVAL_S"):
            kwargs["poller_interval_s"] = int(v)
        if v := env("POLLER_COMPANY_ID"):
            kwargs["poller_company_id"] = v
        if v := env("POLLER_DIRECTORY"):
            kwargs["poller_directory"] = v
        if v := env("POLLER_SFTP_HOST"):
            kwargs["poller_sftp_host"] = v
        if v := env("POLLER_SFTP_PORT"):
            kwargs["poller_sftp_port"] = int(v)
        if v := env("POLLER_SFTP_USER"):
            kwargs["poller_sftp_user"] = v
        if v := env("POLLER_SFTP_KEY_PATH"):
            kwargs["poller_sftp_key_path"] = v
        if v := env("POLLER_HTTP_URL"):
            kwargs["poller_http_url"] = v

        # ── Transforma module cache
        if v := env("MODULE_CACHE_REFRESH_INTERVAL_S"):
            kwargs["module_cache_refresh_interval_s"] = int(v)
        if v := env("INTERNAL_SERVICE_TOKEN"):
            kwargs["internal_service_token"] = v

        # ── Redis
        if v := env("REDIS_URL"):
            kwargs["redis_url"] = v
        if v := env("REDIS_PREFIX"):
            kwargs["redis_prefix"] = v
        if v := env("RATE_LIMIT_DAILY"):
            kwargs["rate_limit_daily"] = int(v)

        # ── Workers
        if v := env("WORKERS"):
            kwargs["workers"] = int(v)

        # ── Malware scanning
        if v := env("MALWARE_SCAN_ENABLED"):
            kwargs["malware_scan_enabled"] = v.lower() in ("true", "1", "yes")
        if v := env("MALWARE_CLAMD_SOCKET"):
            kwargs["malware_clamd_socket"] = v
        if v := env("MALWARE_CLAMD_HOST"):
            kwargs["malware_clamd_host"] = v
        if v := env("MALWARE_CLAMD_PORT"):
            kwargs["malware_clamd_port"] = int(v)
        if v := env("MALWARE_SCAN_TIMEOUT_S"):
            kwargs["malware_scan_timeout_s"] = int(v)
        if v := env("MALWARE_ON_UNAVAILABLE"):
            kwargs["malware_on_unavailable"] = v.lower()

        return cls(**kwargs)
