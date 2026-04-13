"""
Core Service Configuration

Single dataclass with from_env() classmethod loading CORE_* environment variables.
Follows the Helium config convention (see Relay, HIS configs).

All settings have sensible defaults for local development.
Production values come from environment variables.
"""

import os
from dataclasses import dataclass


@dataclass
class CoreConfig:
    """
    Core service configuration.

    Load from environment:
        config = CoreConfig.from_env()

    All CORE_* env vars are optional — defaults target local dev.
    """

    # ── Server ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080

    # ── Database ──────────────────────────────────────────────────────────
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "helium"
    db_password: str = "helium_dev"
    db_name: str = "helium_core"
    db_pool_min: int = 5
    db_pool_max: int = 20

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Processing ────────────────────────────────────────────────────────
    batch_size: int = 100
    worker_type: str = "thread"  # "thread" or "celery"

    # ── Upstream services ─────────────────────────────────────────────────
    heartbeat_url: str = "http://localhost:9000"
    heartbeat_api_key: str = ""
    edge_url: str = "http://localhost:8090"

    # ── Ingestion (WS1) ───────────────────────────────────────────────────
    scanner_interval: int = 60  # seconds between safety-net scans
    scanner_stale_threshold: int = 300  # seconds before PROCESSING is stale (5 min)
    blob_fetch_timeout: int = 30  # seconds for HeartBeat blob download

    # ── SSE ───────────────────────────────────────────────────────────────
    sse_buffer_size: int = 1000
    sse_heartbeat_interval: int = 15  # seconds
    sse_ledger_retention_hours: int = 48  # event ledger retention window
    sse_ledger_prune_interval: int = 21600  # prune every 6 hours (seconds)

    # ── JWT (EdDSA public key from HeartBeat) ─────────────────────────────
    jwt_public_key: str = ""  # Ed25519 PEM public key or raw base64
    jwt_algorithm: str = "EdDSA"  # EdDSA (Ed25519) or HS256 for dev

    # ── Processing Pipeline (WS2) ──────────────────────────────────────
    # Phase 3: Transformation
    script_timeout_seconds: float = 30.0
    default_due_date_days: int = 30

    # Phase 4: Enrichment (HIS / Pronalytics)
    his_base_url: str = "http://localhost:8500"
    his_api_key: str = ""
    his_timeout: float = 10.0
    his_max_retries: int = 3
    his_concurrent_invoices: int = 10

    # Phase 4: HIS Intelligence Feedback
    his_feedback_enabled: bool = True
    his_feedback_timeout: float = 10.0

    # Phase 4: Circuit breaker
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 60.0
    circuit_success_threshold: int = 2

    # Phase 5: Entity resolution
    fuzzy_match_threshold: float = 0.85
    fuzzy_auto_select_threshold: float = 0.95
    max_fuzzy_candidates: int = 50

    # ── Resource Limits (EH-007) ────────────────────────────────────────
    max_file_size_mb: int = 50        # Reject files larger than this at /enqueue
    max_invoices_per_batch: int = 1000  # Reject batches with more invoices than this

    # ── Observability (WS6) ───────────────────────────────────────────────
    metrics_collect_interval: int = 30  # seconds between gauge updates
    notification_ttl_hours: int = 720  # 30 days
    notification_cleanup_interval: int = 3600  # seconds (1 hour)

    # ── CORS ──────────────────────────────────────────────────────────────
    cors_origins: str = "*"  # Comma-separated in production

    @property
    def conninfo(self) -> str:
        """Build psycopg3 connection string."""
        return (
            f"host={self.db_host} port={self.db_port} "
            f"dbname={self.db_name} user={self.db_user} "
            f"password={self.db_password}"
        )

    @classmethod
    def from_env(cls) -> "CoreConfig":
        """
        Load configuration from CORE_* environment variables.

        Every field maps to CORE_{FIELD_NAME_UPPER}. For example:
            port          → CORE_PORT
            db_host       → CORE_DB_HOST
            sse_buffer_size → CORE_SSE_BUFFER_SIZE
        """
        kwargs = {}

        def env(name: str) -> str | None:
            return os.environ.get(f"CORE_{name}")

        # ── Server
        if v := env("HOST"):
            kwargs["host"] = v
        if v := env("PORT"):
            kwargs["port"] = int(v)

        # ── Database
        if v := env("DB_HOST"):
            kwargs["db_host"] = v
        if v := env("DB_PORT"):
            kwargs["db_port"] = int(v)
        if v := env("DB_USER"):
            kwargs["db_user"] = v
        if v := env("DB_PASSWORD"):
            kwargs["db_password"] = v
        if v := env("DB_NAME"):
            kwargs["db_name"] = v
        if v := env("DB_POOL_MIN"):
            kwargs["db_pool_min"] = int(v)
        if v := env("DB_POOL_MAX"):
            kwargs["db_pool_max"] = int(v)

        # ── Logging
        if v := env("LOG_LEVEL"):
            kwargs["log_level"] = v

        # ── Processing
        if v := env("BATCH_SIZE"):
            kwargs["batch_size"] = int(v)
        if v := env("WORKER_TYPE"):
            kwargs["worker_type"] = v

        # ── Upstream services
        if v := env("HEARTBEAT_URL"):
            kwargs["heartbeat_url"] = v
        if v := env("HEARTBEAT_API_KEY"):
            kwargs["heartbeat_api_key"] = v
        if v := env("EDGE_URL"):
            kwargs["edge_url"] = v

        # ── Ingestion (WS1)
        if v := env("SCANNER_INTERVAL"):
            kwargs["scanner_interval"] = int(v)
        if v := env("SCANNER_STALE_THRESHOLD"):
            kwargs["scanner_stale_threshold"] = int(v)
        if v := env("BLOB_FETCH_TIMEOUT"):
            kwargs["blob_fetch_timeout"] = int(v)

        # ── SSE
        if v := env("SSE_BUFFER_SIZE"):
            kwargs["sse_buffer_size"] = int(v)
        if v := env("SSE_HEARTBEAT_INTERVAL"):
            kwargs["sse_heartbeat_interval"] = int(v)
        if v := env("SSE_LEDGER_RETENTION_HOURS"):
            kwargs["sse_ledger_retention_hours"] = int(v)
        if v := env("SSE_LEDGER_PRUNE_INTERVAL"):
            kwargs["sse_ledger_prune_interval"] = int(v)

        # ── JWT
        if v := env("JWT_PUBLIC_KEY"):
            kwargs["jwt_public_key"] = v
        if v := env("JWT_ALGORITHM"):
            kwargs["jwt_algorithm"] = v

        # ── Processing Pipeline (WS2)
        if v := env("SCRIPT_TIMEOUT_SECONDS"):
            kwargs["script_timeout_seconds"] = float(v)
        if v := env("DEFAULT_DUE_DATE_DAYS"):
            kwargs["default_due_date_days"] = int(v)
        if v := env("HIS_BASE_URL"):
            kwargs["his_base_url"] = v
        if v := env("HIS_API_KEY"):
            kwargs["his_api_key"] = v
        if v := env("HIS_TIMEOUT"):
            kwargs["his_timeout"] = float(v)
        if v := env("HIS_MAX_RETRIES"):
            kwargs["his_max_retries"] = int(v)
        if v := env("HIS_CONCURRENT_INVOICES"):
            kwargs["his_concurrent_invoices"] = int(v)
        if v := env("HIS_FEEDBACK_ENABLED"):
            kwargs["his_feedback_enabled"] = v.lower() in ("true", "1", "yes")
        if v := env("HIS_FEEDBACK_TIMEOUT"):
            kwargs["his_feedback_timeout"] = float(v)
        if v := env("CIRCUIT_FAILURE_THRESHOLD"):
            kwargs["circuit_failure_threshold"] = int(v)
        if v := env("CIRCUIT_RECOVERY_TIMEOUT"):
            kwargs["circuit_recovery_timeout"] = float(v)
        if v := env("CIRCUIT_SUCCESS_THRESHOLD"):
            kwargs["circuit_success_threshold"] = int(v)
        if v := env("FUZZY_MATCH_THRESHOLD"):
            kwargs["fuzzy_match_threshold"] = float(v)
        if v := env("FUZZY_AUTO_SELECT_THRESHOLD"):
            kwargs["fuzzy_auto_select_threshold"] = float(v)
        if v := env("MAX_FUZZY_CANDIDATES"):
            kwargs["max_fuzzy_candidates"] = int(v)

        # ── Observability (WS6)
        if v := env("METRICS_COLLECT_INTERVAL"):
            kwargs["metrics_collect_interval"] = int(v)
        if v := env("NOTIFICATION_TTL_HOURS"):
            kwargs["notification_ttl_hours"] = int(v)
        if v := env("NOTIFICATION_CLEANUP_INTERVAL"):
            kwargs["notification_cleanup_interval"] = int(v)

        # ── CORS
        if v := env("CORS_ORIGINS"):
            kwargs["cors_origins"] = v

        return cls(**kwargs)
