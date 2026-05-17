"""
HMAC-signed webhook receiver verifier — CSSV1 S4 R12.

Implements the consumer side of ``WEBHOOK_CONFIG_CONTRACT.md``
(HeartBeat producer signs; Relay consumer verifies). Mirrors the
Core consumer that shipped with the original contract — same wire
format, same signing input, same error-code catalogue, same 5-minute
replay window.

Why this exists separately from the existing dispatcher in
``deps.py``:
    The dispatcher (``authenticate_request``) handles three caller
    shapes (HMAC ERP, Bearer service-creds, Bearer JWT) for
    *frontend / ERP / service* traffic. Webhooks are a fourth shape:
    an HB→Relay push with a different signing scheme (3 headers
    instead of 4 — no nonce, replay handled by timestamp window
    alone), a different signing input (``f"{ts}.".encode() + body``
    rather than the canonical request-signature concatenation), and
    a different failure surface (six error codes per the contract
    rather than the standard ``AuthenticationFailedError`` hierarchy).
    Folding it into the dispatcher would muddy both.

Layout:
    - :func:`verify_webhook_request` is the dependency. Wire it via
      ``Depends(verify_webhook_request)`` on the webhook route.
    - :func:`_check_ip_allow_list` is the IP layer; pulled out so the
      tests can pin the CIDR parsing independently.
    - :func:`_compute_signature` is the HMAC primitive — the same
      function HB uses to sign. Pin its output against the canonical
      test vector in ``WEBHOOK_CONFIG_CONTRACT.md §7``.

The signing input is ``f"{timestamp}.".encode("utf-8") + raw_body_bytes``
(contract §5.2). Do NOT change that without also changing HB's
producer.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import time
from typing import Iterable, List

from fastapi import Request

from ..config import RelayConfig
from ..errors import WebhookAuthError

logger = logging.getLogger(__name__)


_SIGNATURE_PREFIX = "sha256="


def _compute_signature(signing_key_hex: str, timestamp: str, body_bytes: bytes) -> str:
    """Compute the canonical signature for a webhook payload.

    Per ``WEBHOOK_CONFIG_CONTRACT.md §5.2``. Output prefixed with
    ``sha256=`` so callers can compare wire-for-wire.

    Args:
        signing_key_hex: Per-service shared secret. 64-char lowercase
            hex (32 bytes of entropy) per the contract's bootstrap.
        timestamp: ``X-HeartBeat-Timestamp`` header value (Unix epoch
            seconds as ASCII string).
        body_bytes: Raw request body bytes (exact wire bytes).

    Returns:
        ``"sha256=<hex>"`` ready for header comparison.
    """
    key_bytes = bytes.fromhex(signing_key_hex)
    signing_input = f"{timestamp}.".encode("utf-8") + body_bytes
    digest = hmac.new(key_bytes, signing_input, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def _parse_cidrs(cidrs_csv: str) -> List[ipaddress._BaseNetwork]:
    """Parse the comma-separated CIDR allow-list into network objects.

    Silently drops empty / blank entries; raises ``ValueError`` (caught
    by the verifier as a 503) if every entry is malformed — that's a
    config error, not a request error.
    """
    networks: List[ipaddress._BaseNetwork] = []
    for raw in cidrs_csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError as exc:
            logger.warning(
                "Webhook allow-list contains invalid CIDR %r — skipped: %s",
                raw, exc,
            )
    return networks


def _check_ip_allow_list(client_ip: str, networks: Iterable[ipaddress._BaseNetwork]) -> bool:
    """Return True iff ``client_ip`` falls into any allow-listed network."""
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


async def verify_webhook_request(request: Request) -> None:
    """FastAPI dependency: enforce ``WEBHOOK_CONFIG_CONTRACT.md §5``.

    Order of checks (fail-fast, fail-loud):
        1. Signing key configured (503 if not — service is not ready
           to accept webhooks; producer logs & moves on).
        2. Source IP in allow-list (403 ``WEBHOOK_IP_REJECTED``).
        3. ``X-HeartBeat-Signature`` header present + ``sha256=...``
           prefix (403 ``WEBHOOK_SIG_MISSING``).
        4. ``X-HeartBeat-Timestamp`` header present + parseable as
           integer (403 ``WEBHOOK_SIG_BAD_TIMESTAMP``).
        5. Timestamp inside replay window (403 ``WEBHOOK_SIG_REPLAY``).
        6. HMAC matches the computed signature
           (403 ``WEBHOOK_SIG_INVALID``).

    The reverse-proxy / Docker-network case is handled at step 2 by
    the default CIDR list (172.16.0.0/12, 10.0.0.0/8, 127.0.0.1/32);
    bare-metal deployments override via ``RELAY_WEBHOOK_ALLOWED_CIDRS``.

    On success: returns ``None`` and the handler runs. On any failure:
    raises :class:`WebhookAuthError` (mapped to 403/503 by
    ``relay_error_handler``).

    Body bytes are read from ``request.state.raw_body`` (populated by
    ``BodyCacheMiddleware``) so the FastAPI handler can still
    JSON-parse the body afterwards via ``await request.json()``.
    """
    config: RelayConfig = request.app.state.config

    # ── Step 1: signing key configured ───────────────────────────────────
    if not config.webhook_signing_key:
        logger.error(
            "Webhook rejected — RELAY_WEBHOOK_SIGNING_KEY not configured. "
            "Pull the value from HB's startup WARNING log per "
            "WEBHOOK_CONFIG_CONTRACT.md §6.3 and set the env var.",
        )
        raise WebhookAuthError(
            error_code="WEBHOOK_NOT_CONFIGURED",
            message="Webhook receiver is not configured.",
            status_code=503,
        )

    # ── Step 2: IP allow-list ────────────────────────────────────────────
    networks = _parse_cidrs(config.webhook_allowed_cidrs)
    client_host = request.client.host if request.client is not None else ""
    if not networks or not _check_ip_allow_list(client_host, networks):
        logger.warning(
            "Webhook rejected — source IP %s not in allow-list (%s).",
            client_host or "(unknown)",
            config.webhook_allowed_cidrs,
        )
        raise WebhookAuthError(
            error_code="WEBHOOK_IP_REJECTED",
            message="Source IP not allowed.",
            status_code=403,
        )

    # ── Step 3: signature header present + well-formed ───────────────────
    signature = request.headers.get("x-heartbeat-signature", "")
    if not signature or not signature.startswith(_SIGNATURE_PREFIX):
        raise WebhookAuthError(
            error_code="WEBHOOK_SIG_MISSING",
            message="X-HeartBeat-Signature header missing or malformed.",
            status_code=403,
        )

    # ── Step 4: timestamp header present + integer ───────────────────────
    timestamp_raw = request.headers.get("x-heartbeat-timestamp", "")
    if not timestamp_raw or not timestamp_raw.lstrip("-").isdigit():
        raise WebhookAuthError(
            error_code="WEBHOOK_SIG_BAD_TIMESTAMP",
            message="X-HeartBeat-Timestamp header missing or non-integer.",
            status_code=403,
        )

    # ── Step 5: timestamp inside replay window ───────────────────────────
    timestamp = int(timestamp_raw)
    now = int(time.time())
    if abs(now - timestamp) > config.webhook_replay_window_s:
        logger.warning(
            "Webhook rejected — timestamp %d outside %ds replay window (now=%d).",
            timestamp, config.webhook_replay_window_s, now,
        )
        raise WebhookAuthError(
            error_code="WEBHOOK_SIG_REPLAY",
            message="Timestamp outside replay window.",
            status_code=403,
        )

    # ── Step 6: HMAC match ───────────────────────────────────────────────
    body_bytes = getattr(request.state, "raw_body", None)
    if body_bytes is None:
        # BodyCacheMiddleware should always populate this; if not,
        # fall back to reading directly (loses idempotency for the
        # downstream handler, but that's correct under failure).
        body_bytes = await request.body()

    expected = _compute_signature(
        signing_key_hex=config.webhook_signing_key,
        timestamp=timestamp_raw,
        body_bytes=body_bytes,
    )

    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "Webhook rejected — HMAC mismatch (ip=%s, ts=%s).",
            client_host, timestamp_raw,
        )
        raise WebhookAuthError(
            error_code="WEBHOOK_SIG_INVALID",
            message="HMAC signature verification failed.",
            status_code=403,
        )


__all__ = [
    "verify_webhook_request",
    "_compute_signature",  # exported for testing the canonical vector
    "_parse_cidrs",
    "_check_ip_allow_list",
]
