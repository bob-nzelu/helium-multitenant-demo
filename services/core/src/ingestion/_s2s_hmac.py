"""
Service-to-service HMAC signing helper for Relay → HeartBeat calls.

Per HMAC_S2S_MIGRATION_SPEC.md §2 (canonical wire format) and §9.1
(copy-paste-ready signing helper). Replaces the legacy
``Authorization: Bearer api_key:api_secret`` form which HeartBeat now
rejects with ``401 BEARER_S2S_REMOVED`` (locked decision L5,
2026-05-08).

Wire format (PINNED — drift breaks every caller):

    X-API-Key:    <service_api_key>          # e.g. "rl_test_relay001"
    X-Timestamp:  <unix_epoch_seconds>       # integer ASCII
    X-Nonce:      <uuid4-with-dashes>        # 36 chars
    X-Signature:  hex(HMAC-SHA256(signing_key, signing_input))

Where ``signing_input`` is the byte string::

    f"{METHOD}\\n{path}\\n{timestamp}\\n{nonce}\\n{body_sha256}".encode("utf-8")

with:
- ``{METHOD}`` upper-case verb (``POST``, ``GET``).
- ``{path}`` request path with leading slash, no query string, no host.
- ``{timestamp}`` and ``{nonce}`` the EXACT ASCII bytes used in the
  headers (do not re-stringify ints; sign what the wire shows).
- ``{body_sha256}`` ``hashlib.sha256(raw_body_bytes).hexdigest()`` —
  empty body uses ``sha256(b"").hexdigest()``, NOT the SHA of the
  literal string ``""``.
- Single ``\\n`` separator. No trailing newline.

Body-bytes discipline (from HMAC_S2S_MIGRATION_SPEC §8.1 step 3): the
caller MUST sign the EXACT bytes it sends. If the HTTP client
serialises JSON, capture the serialised bytes once and pass the same
``bytes`` object to both ``content=`` (or equivalent) and this helper.
Never sign a Python dict and serialise separately.

NTP discipline (spec §8.1 step 4): the caller's host MUST run NTP.
Skew > 60 s vs HB causes intermittent ``HMAC_TIMESTAMP_SKEW`` 401s
(spec §3 verification step 2 — 300 s wall-clock window).
"""

import hashlib
import hmac
import time
import uuid
from typing import Mapping


def build_s2s_hmac_headers(
    *,
    method: str,
    path: str,
    body_bytes: bytes,
    api_key: str,
    signing_key: str,
) -> Mapping[str, str]:
    """Build the four HMAC headers for a service-to-service call to HeartBeat.

    Args:
        method: Upper-case HTTP verb, e.g. ``"POST"``. Lower-case input
            is upper-cased for the signing input.
        path: Request path with leading slash, no query string, no host.
            For FastAPI on the receiver side this is
            ``request.url.path`` verbatim.
        body_bytes: The EXACT bytes that will be sent on the wire. If
            the HTTP client serialises JSON, capture the serialised
            bytes and pass them BOTH as the request body and here.
            Empty body must be ``b""`` (NOT ``""`` or ``None``).
        api_key: The caller's HB-issued api_key (e.g.
            ``"rl_test_relay001"``).
        signing_key: The per-service s2s signing key from HB's bootstrap
            log, read from the ``RELAY_S2S_SIGNING_KEY`` env var. 64-hex
            chars (32 bytes). Never log this; never return in HTTP
            responses; never check into git.

    Returns:
        Mapping with the four canonical headers:
        ``X-API-Key``, ``X-Timestamp``, ``X-Nonce``, ``X-Signature``.

    Raises:
        ValueError: if ``signing_key`` is empty (operator misconfig
            should fail loudly before any HTTP I/O).
    """
    if not signing_key:
        raise ValueError(
            "RELAY_S2S_SIGNING_KEY is empty. Pull the value from HB's "
            "startup WARNING log per RELAY_NEXT_STEPS_NOTE §1.3."
        )
    if not api_key:
        raise ValueError("api_key is empty; cannot construct HMAC headers.")

    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    signing_input = (
        f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_sha256}"
    ).encode("utf-8")
    signature = hmac.new(
        signing_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-API-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


__all__ = ["build_s2s_hmac_headers"]
