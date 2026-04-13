"""
WS6: Prometheus Metric Definitions

Module-level metric singletons. Import and use from any workstream.
The /metrics endpoint serves these via prometheus_client.generate_latest().

NOTE: sse_connections_active lives in src/sse/manager.py — do NOT redefine here.
"""

from prometheus_client import Counter, Gauge, Histogram, Info

# ── HTTP Request Metrics ───────────────────────────────────────────────────

http_requests_total = Counter(
    "core_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "core_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
)

# ── Pipeline Metrics ──────────────────────────────────────────────────────

pipeline_runs_total = Counter(
    "core_pipeline_runs_total",
    "Total pipeline executions",
    ["status"],  # success, failed, timeout
)

pipeline_duration_seconds = Histogram(
    "core_pipeline_duration_seconds",
    "Pipeline execution duration in seconds",
    ["phase"],  # parse, transform, enrich, resolve, branch, preview
)

invoices_processed_total = Counter(
    "core_invoices_processed_total",
    "Total invoices processed",
    ["direction", "transaction_type", "status"],
)

# ── Queue Metrics ─────────────────────────────────────────────────────────

queue_depth = Gauge(
    "core_queue_depth",
    "Current queue depth by status",
    ["status"],  # PENDING, PROCESSING
)

queue_processing_duration_seconds = Histogram(
    "core_queue_processing_duration_seconds",
    "Time from enqueue to completion in seconds",
)

# ── External Service Metrics ──────────────────────────────────────────────

external_service_requests_total = Counter(
    "core_external_service_requests_total",
    "Requests to external services",
    ["service", "status"],  # service: heartbeat, his, edge
)

external_service_duration_seconds = Histogram(
    "core_external_service_duration_seconds",
    "External service call duration in seconds",
    ["service"],
)

circuit_breaker_state = Gauge(
    "core_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["service"],
)

# ── Entity Metrics ────────────────────────────────────────────────────────

entity_count = Gauge(
    "core_entity_count",
    "Total entity count by type",
    ["entity_type"],  # invoice, customer, inventory
)

# ── System Info ───────────────────────────────────────────────────────────

core_info = Info("core", "Core service metadata")
