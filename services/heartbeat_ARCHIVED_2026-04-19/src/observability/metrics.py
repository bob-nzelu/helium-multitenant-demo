"""
HeartBeat Prometheus Metric Definitions (P2-A)

Central registry of all Prometheus metrics for the HeartBeat service.
Import metrics from this module to instrument code.

Naming convention: heartbeat_{domain}_{metric_type}
    e.g., heartbeat_blob_registrations_total
          heartbeat_request_duration_seconds

Usage:
    from src.observability.metrics import BLOB_REGISTRATIONS, REQUEST_DURATION

    BLOB_REGISTRATIONS.labels(status="success", source_type="bulk").inc()

    with REQUEST_DURATION.labels(method="POST", endpoint="/api/blobs/register").time():
        ...
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)


# ── Custom Registry ─────────────────────────────────────────────────────
# Use a custom registry to avoid polluting the default with test artifacts.
# The /metrics endpoint uses this registry.

REGISTRY = CollectorRegistry()


# ── Service Info ──────────────────────────────────────────────────────────

SERVICE_INFO = Info(
    "heartbeat",
    "HeartBeat service information",
    registry=REGISTRY,
)


# ── Blob Metrics ──────────────────────────────────────────────────────────

BLOB_REGISTRATIONS = Counter(
    "heartbeat_blob_registrations_total",
    "Total blob registrations",
    ["status", "source_type"],
    registry=REGISTRY,
)

BLOB_STATUS_CHANGES = Counter(
    "heartbeat_blob_status_changes_total",
    "Total blob status transitions",
    ["from_status", "to_status"],
    registry=REGISTRY,
)

BLOB_SIZE_BYTES = Histogram(
    "heartbeat_blob_size_bytes",
    "Size of registered blobs in bytes",
    ["source_type"],
    buckets=[1024, 10240, 102400, 1048576, 10485760, 104857600],  # 1KB to 100MB
    registry=REGISTRY,
)

BLOBS_ACTIVE = Gauge(
    "heartbeat_blobs_active",
    "Number of blobs in each status",
    ["status"],
    registry=REGISTRY,
)


# ── Deduplication Metrics ─────────────────────────────────────────────────

DEDUP_CHECKS = Counter(
    "heartbeat_dedup_checks_total",
    "Total deduplication lookups",
    ["result"],  # "unique" or "duplicate"
    registry=REGISTRY,
)


# ── Credential Metrics ───────────────────────────────────────────────────

CREDENTIAL_OPERATIONS = Counter(
    "heartbeat_credential_operations_total",
    "Credential lifecycle operations",
    ["operation"],  # "created", "rotated", "revoked", "validated"
    registry=REGISTRY,
)

AUTH_ATTEMPTS = Counter(
    "heartbeat_auth_attempts_total",
    "Authentication attempts",
    ["result"],  # "success", "failure", "expired"
    registry=REGISTRY,
)


# ── Registry Metrics ─────────────────────────────────────────────────────

REGISTRY_REGISTRATIONS = Counter(
    "heartbeat_registry_registrations_total",
    "Service instance registrations",
    ["service_name"],
    registry=REGISTRY,
)

SERVICES_ACTIVE = Gauge(
    "heartbeat_services_active",
    "Number of active service instances",
    ["service_name"],
    registry=REGISTRY,
)


# ── Request Metrics ──────────────────────────────────────────────────────

REQUEST_DURATION = Histogram(
    "heartbeat_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY,
)

REQUEST_COUNT = Counter(
    "heartbeat_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
    registry=REGISTRY,
)

REQUESTS_IN_PROGRESS = Gauge(
    "heartbeat_requests_in_progress",
    "Number of HTTP requests currently being processed",
    registry=REGISTRY,
)


# ── Audit Metrics ─────────────────────────────────────────────────────────

AUDIT_EVENTS_LOGGED = Counter(
    "heartbeat_audit_events_total",
    "Total audit events logged",
    ["service", "event_type"],
    registry=REGISTRY,
)


# ── Storage Metrics ──────────────────────────────────────────────────────

STORAGE_OPERATIONS = Counter(
    "heartbeat_storage_operations_total",
    "Filesystem storage operations",
    ["operation", "result"],  # operation: "put", "get", "delete"; result: "success", "error"
    registry=REGISTRY,
)


# ── Daily Usage Metrics ──────────────────────────────────────────────────

DAILY_USAGE_FILES = Gauge(
    "heartbeat_daily_usage_files",
    "Files uploaded today per company",
    ["company_id"],
    registry=REGISTRY,
)

DAILY_LIMIT_REJECTIONS = Counter(
    "heartbeat_daily_limit_rejections_total",
    "Number of uploads rejected due to daily limit",
    ["company_id"],
    registry=REGISTRY,
)


# ── Migration Metrics ────────────────────────────────────────────────────

MIGRATIONS_APPLIED = Counter(
    "heartbeat_migrations_applied_total",
    "Number of database migrations applied",
    ["db_name"],
    registry=REGISTRY,
)

MIGRATION_DRIFT_DETECTED = Counter(
    "heartbeat_migration_drift_detected_total",
    "Number of migration drift detections",
    ["db_name"],
    registry=REGISTRY,
)


# ── SSE Metrics (SSE Spec Section 10.1) ─────────────────────────────────

SSE_CONNECTIONS_ACTIVE = Gauge(
    "helium_sse_connections_active",
    "Currently connected SSE clients",
    ["service"],
    registry=REGISTRY,
)

SSE_EVENTS_PUBLISHED = Counter(
    "helium_sse_events_published_total",
    "Total events published to SSE",
    ["service", "event_type"],
    registry=REGISTRY,
)

SSE_EVENTS_DROPPED = Counter(
    "helium_sse_events_dropped_total",
    "Events dropped (queue_full, eviction)",
    ["service", "reason"],
    registry=REGISTRY,
)

SSE_CATCHUP_REQUESTS = Counter(
    "helium_sse_catchup_requests_total",
    "Catchup endpoint calls",
    ["service"],
    registry=REGISTRY,
)

SSE_RECONNECTIONS = Counter(
    "helium_sse_reconnections_total",
    "Client reconnections (Last-Event-ID present)",
    ["service"],
    registry=REGISTRY,
)

SSE_LEDGER_SIZE = Gauge(
    "helium_sse_ledger_size",
    "Current row count in event_ledger",
    ["service"],
    registry=REGISTRY,
)
