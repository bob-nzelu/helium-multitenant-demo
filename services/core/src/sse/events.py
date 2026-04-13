"""SSE event type constants and helper functions for WS3 event emission."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

EVENT_PROCESSING_LOG = "processing.log"
EVENT_PROCESSING_PROGRESS = "processing.progress"
EVENT_PROCESSING_COMPLETE = "processing.complete"

# ---------------------------------------------------------------------------
# Helpers — build event payloads
# ---------------------------------------------------------------------------


def make_log_event(data_uuid: str, message: str, level: str = "info") -> dict:
    """Build a processing.log SSE payload.

    Args:
        data_uuid: Identifies the processing request.
        message: Human-readable status message.
        level: One of "info", "success", "warning", "error".

    Returns:
        Dict ready for SSEManager.publish().
    """
    return {
        "data_uuid": data_uuid,
        "message": message,
        "level": level,
    }


def make_progress_event(data_uuid: str, ready: int, total: int) -> dict:
    """Build a processing.progress SSE payload.

    Args:
        data_uuid: Identifies the processing request.
        ready: Number of invoices that completed all phases.
        total: Total invoices to process.

    Returns:
        Dict ready for SSEManager.publish().
    """
    return {
        "data_uuid": data_uuid,
        "invoices_ready": ready,
        "invoices_total": total,
    }
