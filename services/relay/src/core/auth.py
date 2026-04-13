"""
HMAC-SHA256 Authentication

Cherry-picked from old_src/bulk/validation.py (lines 80-165).
Extracted to standalone functions for reuse by both bulk and external flows.

Signature scheme:
    message = "{api_key}:{timestamp}:{sha256(body)}"
    signature = HMAC-SHA256(secret, message)

Headers:
    X-API-Key:    Client API key
    X-Timestamp:  ISO 8601 UTC (e.g., "2026-01-31T10:00:00Z")
    X-Signature:  Hex-encoded HMAC-SHA256

The HMAC is computed OVER the encrypted envelope (if encrypted) or
the raw body (if plaintext). This means authentication happens BEFORE
decryption — authenticate-then-decrypt.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Dict

from ..errors import (
    AuthenticationFailedError,
    InvalidAPIKeyError,
    SignatureVerificationFailedError,
    TimestampExpiredError,
)

logger = logging.getLogger(__name__)

# Timestamp validity window (seconds)
TIMESTAMP_WINDOW_S = 300  # 5 minutes


def validate_timestamp(timestamp: str) -> datetime:
    """
    Validate that timestamp is within the 5-minute window.

    Args:
        timestamp: ISO 8601 UTC string (e.g., "2026-01-31T10:00:00Z").

    Returns:
        Parsed datetime object.

    Raises:
        AuthenticationFailedError: If timestamp format is invalid.
        TimestampExpiredError: If timestamp is outside the window.
    """
    try:
        request_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        raise AuthenticationFailedError(
            f"Invalid timestamp format: '{timestamp}'. "
            "Use ISO 8601 with Z suffix (e.g., 2026-01-31T10:00:00Z)."
        )

    now = datetime.now(timezone.utc)
    age_seconds = abs((now - request_time).total_seconds())

    if age_seconds > TIMESTAMP_WINDOW_S:
        raise TimestampExpiredError(age_seconds=int(age_seconds))

    return request_time


def validate_api_key(api_key: str, api_key_secrets: Dict[str, str]) -> str:
    """
    Look up the shared secret for an API key.

    Args:
        api_key: Client API key from X-API-Key header.
        api_key_secrets: Mapping of api_key → shared_secret.

    Returns:
        The shared secret.

    Raises:
        InvalidAPIKeyError: If api_key not in the map.
    """
    secret = api_key_secrets.get(api_key)
    if secret is None:
        logger.warning(f"Unknown API key attempted: {api_key[:8]}...")
        raise InvalidAPIKeyError()
    return secret


def compute_signature(api_key: str, timestamp: str, body: bytes, secret: str) -> str:
    """
    Compute HMAC-SHA256 signature.

    Args:
        api_key: Client API key.
        timestamp: ISO 8601 timestamp string.
        body: Raw request body bytes (encrypted or plaintext).
        secret: Shared secret for this API key.

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    api_key: str,
    timestamp: str,
    signature: str,
    body: bytes,
    secret: str,
) -> None:
    """
    Verify HMAC-SHA256 signature using constant-time comparison.

    Args:
        api_key: Client API key.
        timestamp: ISO 8601 timestamp string.
        signature: Hex-encoded signature from X-Signature header.
        body: Raw request body bytes.
        secret: Shared secret for this API key.

    Raises:
        SignatureVerificationFailedError: If signature doesn't match.
    """
    expected = compute_signature(api_key, timestamp, body, secret)

    if not hmac.compare_digest(signature, expected):
        logger.warning(f"HMAC mismatch for api_key={api_key[:8]}...")
        raise SignatureVerificationFailedError()


def authenticate(
    api_key: str,
    timestamp: str,
    signature: str,
    body: bytes,
    api_key_secrets: Dict[str, str],
    trace_id: str = "",
) -> str:
    """
    Full authentication flow: timestamp → api_key → signature.

    This is the main entry point called by the API middleware.

    Args:
        api_key: From X-API-Key header.
        timestamp: From X-Timestamp header.
        signature: From X-Signature header.
        body: Raw request body bytes.
        api_key_secrets: Mapping of api_key → secret.
        trace_id: Optional trace ID for logging.

    Returns:
        Validated api_key.

    Raises:
        AuthenticationFailedError: On any auth failure.
    """
    # 1. Validate timestamp
    validate_timestamp(timestamp)

    # 2. Look up API key secret
    secret = validate_api_key(api_key, api_key_secrets)

    # 3. Verify signature
    verify_signature(api_key, timestamp, signature, body, secret)

    logger.info(
        f"Authentication successful — api_key={api_key[:8]}...",
        extra={"trace_id": trace_id},
    )

    return api_key
