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
    heartbeat_api_key: str = ""       # RELAY_HEARTBEAT_API_KEY (service api_key for HeartBeat)
    heartbeat_api_secret: str = ""    # RELAY_HEARTBEAT_API_SECRET (legacy bcrypt secret; retained for back-compat — HMAC cutover means HB no longer reads it on the request path; safe to leave empty in fresh deploys)
    heartbeat_s2s_signing_key: str = ""  # RELAY_S2S_SIGNING_KEY — 64-hex per-service HMAC key from HB's startup WARNING log; required post-HMAC-cutover (2026-05-08) for every Relay→HB call hitting verify_service_credentials. NTP discipline required (skew >300s causes HMAC_TIMESTAMP_SKEW). See HMAC_S2S_MIGRATION_SPEC.md §1.3 + §2.2.

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

    # ── Multi-tenant (demo infrastructure) ──────────────────────────────
    tenants_file: str = ""                  # Path to tenants.json (empty = single-tenant mode)


    # ── OAuth / JWKS (Q37 Gap #2) ────────────────────────────────────────────
    # URL to HeartBeat's /.well-known/jwks.json (HB O3 endpoint).
    # Empty = JWKS validation disabled; external OAuth Bearer tokens fall
    # through to the existing introspect path unchanged.  Set
    # RELAY_JWKS_URL once HB O3 ships.
    jwks_url: str = ""

    # ── relay.db ingest ledger (Q28 / Frontdoor §2, §6) ─────────────────
    # Per-tenant SQLite file — the LOCKED all-PG exception. Path to the
    # durable write-first ingest ledger (crash-survival + idempotency on
    # tenant_id+file_sha256). Empty = ledger DISABLED (no behaviour change;
    # the synchronous ingest path runs exactly as before). Do NOT point this
    # at PostgreSQL — relay.db is intentionally SQLite (FRONTDOOR_ARCHITECTURE
    # §2.3 lock). Rollback = delete the file or unset this var.
    relay_db_path: str = ""                 # RELAY_DB_PATH

    # ── Malware scanning ──────────────────────────────────────────────────
    malware_scan_enabled: bool = False
    malware_clamd_socket: str = ""
    malware_clamd_host: str = "localhost"
    malware_clamd_port: int = 3310
    malware_scan_timeout_s: int = 30
    malware_on_unavailable: str = "allow"  # "allow" | "block"

    # ── AMQP ingestion consumer (optional — empty URL = disabled) ────────
    # Set RELAY_AMQP_URL to enable per-tenant AMQP batch ingestion (Q37 Gap #6).
    # When configured the consumer runs in the background alongside FastAPI.
    # Relay starts normally when these are empty.
    amqp_url: str = ""              # amqp://user:pass@host:5672/ (empty = disabled)
    amqp_exchange: str = "helium.ingest"  # Per-tenant exchange name
    amqp_queue: str = ""            # Per-tenant ingest queue (empty = use "relay.ingest")
    amqp_reply_queue: str = ""      # Per-tenant reply queue (empty = use reply_to header)
    amqp_routing_key: str = "ingest"  # Routing key for ingest messages

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
        # The HMAC s2s signing key uses ``RELAY_S2S_SIGNING_KEY`` (NOT
        # ``RELAY_HEARTBEAT_S2S_SIGNING_KEY``) per HMAC_S2S_MIGRATION_SPEC
        # §1.3 + RELAY_NEXT_STEPS_NOTE §1.2 — the operator pulls the
        # value from HB's startup WARNING log and pastes it into the
        # caller-side env under that exact name.
        if v := os.environ.get("RELAY_S2S_SIGNING_KEY"):
            kwargs["heartbeat_s2s_signing_key"] = v

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

        # ── Multi-tenant
        if v := env("TENANTS_FILE"):
            kwargs["tenants_file"] = v


        # ── OAuth / JWKS
        if v := env("JWKS_URL"):
            kwargs["jwks_url"] = v

        # ── relay.db ingest ledger
        if v := env("DB_PATH"):
            kwargs["relay_db_path"] = v

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

        # ── AMQP ingestion consumer
        if v := env("AMQP_URL"):
            kwargs["amqp_url"] = v
        if v := env("AMQP_EXCHANGE"):
            kwargs["amqp_exchange"] = v
        if v := env("AMQP_QUEUE"):
            kwargs["amqp_queue"] = v
        if v := env("AMQP_REPLY_QUEUE"):
            kwargs["amqp_reply_queue"] = v
        if v := env("AMQP_ROUTING_KEY"):
            kwargs["amqp_routing_key"] = v

        return cls(**kwargs)
