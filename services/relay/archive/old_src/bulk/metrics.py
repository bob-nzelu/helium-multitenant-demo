"""
Prometheus Metrics for Relay Bulk Upload Service

Exports metrics for monitoring:
- Files ingested (counter)
- Processing duration (histogram)
- Error rates (counter)
- Active requests (gauge)
- Health status (gauge)

Decision from RELAY_DECISIONS.md (Decision 5A):
Relay exports Prometheus metrics on /metrics endpoint.

Usage:
    from .metrics import METRICS, track_request

    # Track a file ingestion
    METRICS.files_ingested.labels(relay_type="bulk", status="success").inc()

    # Track processing time
    with METRICS.processing_duration.labels(relay_type="bulk").time():
        await process_file()

    # Use decorator for requests
    @track_request
    async def handle_ingest():
        ...
"""

import time
import functools
from typing import Callable, Optional
from contextlib import contextmanager

try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        Info,
        generate_latest,
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# =============================================================================
# Metric Definitions
# =============================================================================

class RelayMetrics:
    """
    Prometheus metrics for Relay service.

    All metrics follow the naming convention:
    helium_relay_{metric_name}_{unit}

    Labels:
    - relay_type: Type of relay (bulk, watcher, etc.)
    - status: Result status (success, error, duplicate, etc.)
    - error_code: Specific error code (for error counters)
    """

    def __init__(self, registry=None):
        """
        Initialize metrics.

        Args:
            registry: Optional custom registry (for testing)
        """
        self.registry = registry or REGISTRY
        self._initialized = False

        if PROMETHEUS_AVAILABLE:
            self._init_metrics()

    def _init_metrics(self):
        """Initialize Prometheus metrics."""
        if self._initialized:
            return

        # Counter: Total files ingested
        self.files_ingested = Counter(
            "helium_relay_files_ingested_total",
            "Total number of files ingested",
            ["relay_type", "status"],
            registry=self.registry,
        )

        # Counter: Total batches processed
        self.batches_processed = Counter(
            "helium_relay_batches_processed_total",
            "Total number of batches processed",
            ["relay_type", "status"],
            registry=self.registry,
        )

        # Histogram: Processing duration
        self.processing_duration = Histogram(
            "helium_relay_processing_duration_seconds",
            "Time spent processing files",
            ["relay_type", "operation"],
            buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
            registry=self.registry,
        )

        # Counter: Errors by code
        self.errors = Counter(
            "helium_relay_errors_total",
            "Total number of errors",
            ["relay_type", "error_code"],
            registry=self.registry,
        )

        # Gauge: Active requests
        self.active_requests = Gauge(
            "helium_relay_active_requests",
            "Number of requests currently being processed",
            ["relay_type"],
            registry=self.registry,
        )

        # Gauge: Health status (1=healthy, 0=unhealthy)
        self.health_status = Gauge(
            "helium_relay_health_status",
            "Health status of dependencies",
            ["service"],
            registry=self.registry,
        )

        # Histogram: File sizes
        self.file_size = Histogram(
            "helium_relay_file_size_bytes",
            "Size of uploaded files in bytes",
            ["relay_type"],
            buckets=[1024, 10240, 102400, 1048576, 5242880, 10485760, 31457280],
            registry=self.registry,
        )

        # Counter: Duplicates detected
        self.duplicates_detected = Counter(
            "helium_relay_duplicates_detected_total",
            "Total number of duplicate files detected",
            ["relay_type", "source"],  # source: session_cache or heartbeat
            registry=self.registry,
        )

        # Info: Service information
        self.info = Info(
            "helium_relay",
            "Relay service information",
            registry=self.registry,
        )
        self.info.info({
            "version": "1.0.0",
            "service": "relay-bulk",
        })

        self._initialized = True

    def is_available(self) -> bool:
        """Check if Prometheus client is available."""
        return PROMETHEUS_AVAILABLE and self._initialized


# Global metrics instance
METRICS = RelayMetrics()


# =============================================================================
# Metric Helpers
# =============================================================================

def get_metrics_output() -> tuple:
    """
    Get Prometheus metrics in exposition format.

    Returns:
        Tuple of (content_bytes, content_type)
    """
    if not PROMETHEUS_AVAILABLE:
        return (
            b"# Prometheus client not installed\n",
            "text/plain",
        )

    return (
        generate_latest(REGISTRY),
        CONTENT_TYPE_LATEST,
    )


@contextmanager
def track_duration(relay_type: str, operation: str):
    """
    Context manager to track operation duration.

    Args:
        relay_type: Type of relay (bulk, watcher, etc.)
        operation: Operation name (ingest, finalize, etc.)

    Usage:
        with track_duration("bulk", "ingest"):
            await process_files()
    """
    if not METRICS.is_available():
        yield
        return

    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        METRICS.processing_duration.labels(
            relay_type=relay_type,
            operation=operation,
        ).observe(duration)


def track_request(relay_type: str = "bulk"):
    """
    Decorator to track request metrics.

    Tracks:
    - Active requests
    - Processing duration
    - Errors

    Args:
        relay_type: Type of relay

    Usage:
        @track_request("bulk")
        async def handle_ingest(request):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not METRICS.is_available():
                return await func(*args, **kwargs)

            # Increment active requests
            METRICS.active_requests.labels(relay_type=relay_type).inc()

            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                # Track error
                error_code = getattr(e, "error_code", "UNKNOWN_ERROR")
                METRICS.errors.labels(
                    relay_type=relay_type,
                    error_code=error_code,
                ).inc()
                raise
            finally:
                # Decrement active requests
                METRICS.active_requests.labels(relay_type=relay_type).dec()

                # Track duration
                duration = time.time() - start_time
                METRICS.processing_duration.labels(
                    relay_type=relay_type,
                    operation="request",
                ).observe(duration)

        return wrapper
    return decorator


def record_file_ingested(
    relay_type: str,
    status: str,
    file_size: Optional[int] = None,
):
    """
    Record a file ingestion event.

    Args:
        relay_type: Type of relay
        status: Ingestion status (success, error, duplicate)
        file_size: Optional file size in bytes
    """
    if not METRICS.is_available():
        return

    METRICS.files_ingested.labels(
        relay_type=relay_type,
        status=status,
    ).inc()

    if file_size is not None:
        METRICS.file_size.labels(relay_type=relay_type).observe(file_size)


def record_batch_processed(relay_type: str, status: str):
    """
    Record a batch processing event.

    Args:
        relay_type: Type of relay
        status: Batch status (success, partial, error)
    """
    if not METRICS.is_available():
        return

    METRICS.batches_processed.labels(
        relay_type=relay_type,
        status=status,
    ).inc()


def record_duplicate_detected(relay_type: str, source: str):
    """
    Record a duplicate file detection.

    Args:
        relay_type: Type of relay
        source: Where duplicate was detected (session_cache, heartbeat)
    """
    if not METRICS.is_available():
        return

    METRICS.duplicates_detected.labels(
        relay_type=relay_type,
        source=source,
    ).inc()


def record_error(relay_type: str, error_code: str):
    """
    Record an error event.

    Args:
        relay_type: Type of relay
        error_code: Error code (from RELAY_BULK_SPEC.md)
    """
    if not METRICS.is_available():
        return

    METRICS.errors.labels(
        relay_type=relay_type,
        error_code=error_code,
    ).inc()


def update_health_status(service: str, healthy: bool):
    """
    Update health status for a dependency.

    Args:
        service: Service name (core_api, heartbeat, audit_service)
        healthy: Whether service is healthy
    """
    if not METRICS.is_available():
        return

    METRICS.health_status.labels(service=service).set(1 if healthy else 0)
