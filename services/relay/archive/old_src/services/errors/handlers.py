"""
Error Response Formatting

Formats errors into standardized JSON responses for API endpoints.
"""

from datetime import datetime
from typing import Dict, Any
from uuid import uuid4

from .exceptions import RelayError


def format_error_response(
    error: RelayError,
    request_id: str = None,
    timestamp: datetime = None,
) -> Dict[str, Any]:
    """Format a RelayError into standardized API response"""

    if request_id is None:
        request_id = f"req_{uuid4()}"
    if timestamp is None:
        timestamp = datetime.utcnow()

    return {
        "status": "error",
        "error_code": error.error_code,
        "message": error.message,
        "details": error.details,
        "request_id": request_id,
        "timestamp": timestamp.isoformat() + "Z",
    }


def format_success_response(
    data: Dict[str, Any],
    request_id: str = None,
    timestamp: datetime = None,
) -> Dict[str, Any]:
    """Format a successful response"""

    if request_id is None:
        request_id = f"req_{uuid4()}"
    if timestamp is None:
        timestamp = datetime.utcnow()

    return {
        "status": "success",
        "data": data,
        "request_id": request_id,
        "timestamp": timestamp.isoformat() + "Z",
    }
