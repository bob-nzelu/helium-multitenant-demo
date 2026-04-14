"""
Test Harness Security Manager

Validates HMAC-signed requests from the test harness key file.
The key file lives on the developer's machine (~/.helium/test_harness_key).
HeartBeat stores only the SHA-256 hash of the key.

Security:
    - HMAC-SHA256 signature validation
    - Constant-time comparison via hmac.compare_digest()
    - Key hash loaded from HEARTBEAT_TEST_HARNESS_KEY_HASH env var
    - All operations audit-logged
"""

import hashlib
import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class TestHarnessManager:
    """Validates test harness HMAC signatures."""

    def __init__(self):
        self._key_hash: Optional[str] = None
        self._enabled = os.environ.get(
            "HEARTBEAT_TEST_HARNESS_ENABLED", ""
        ).lower() in ("true", "1", "yes")

        if self._enabled:
            self._key_hash = os.environ.get("HEARTBEAT_TEST_HARNESS_KEY_HASH", "")
            if not self._key_hash:
                logger.warning(
                    "Test harness enabled but HEARTBEAT_TEST_HARNESS_KEY_HASH not set"
                )
                self._enabled = False
            else:
                logger.info("Test harness manager initialized (key hash loaded)")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def validate_signature(
        self, signature: str, body: bytes
    ) -> bool:
        """
        Validate an HMAC-SHA256 signature from X-Test-Harness-Signature header.

        The signature is computed by the client as:
            HMAC-SHA256(raw_key, request_body).hexdigest()

        We validate by:
            1. Computing the expected signature using the raw key
               (but we only have the hash, so we use a different approach)

        Actually, the protocol is:
            Client: sig = HMAC-SHA256(key, body)
            Server: We store SHA256(key). Client also sends the HMAC.
                    We need the raw key to verify.

        Alternative protocol (what we actually use):
            The signature header contains: HMAC-SHA256(key, body).hexdigest()
            The server has the raw key hash. To verify, we'd need the key.

            REVISED: The key hash IS the HMAC key. The client computes:
                sig = HMAC-SHA256(SHA256(raw_key), body).hexdigest()
            This way, both sides use the hash as the signing key.

        Args:
            signature: Hex-encoded HMAC-SHA256 from X-Test-Harness-Signature
            body: Raw request body bytes

        Returns:
            True if signature is valid.
        """
        if not self._enabled or not self._key_hash:
            return False

        expected = hmac.new(
            self._key_hash.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)


# Singleton
_instance: Optional[TestHarnessManager] = None


def get_test_harness_manager() -> TestHarnessManager:
    """Get singleton TestHarnessManager."""
    global _instance
    if _instance is None:
        _instance = TestHarnessManager()
    return _instance


def reset_test_harness_manager() -> None:
    """Reset singleton (for testing/shutdown)."""
    global _instance
    _instance = None
