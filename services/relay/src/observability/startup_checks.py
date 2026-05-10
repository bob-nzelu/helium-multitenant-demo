"""
Startup configuration checks (CSSV1 R9.1 + R9.2).

Two checks fire BEFORE the lifespan yields traffic:

- :func:`validate_signing_key_shape` (R9.1) — confirm
  ``RELAY_S2S_SIGNING_KEY`` is exactly 64 lowercase-hex chars when it is
  set. Empty is allowed (degraded dev mode logs WARNING); anything else
  raises :class:`~src.errors.ConfigError` so the container fails fast.

- :func:`check_clock_skew_against_heartbeat` (R9.2) — best-effort NTP
  discipline check. Hits HB's ``/health`` and reads the RFC 7231 ``Date``
  response header, comparing it with local UTC time. Skew > 60 s →
  :class:`ConfigError`; HB unreachable / ``Date`` unparseable → WARNING,
  continue (HB may still be cold-starting). Skew is well below HB's
  300 s ``HMAC_TIMESTAMP_SKEW`` window — a 60 s soft floor catches gross
  misconfiguration without flapping on small jitter.

Both checks are pure functions (no global state) so they can run in
arbitrary order from the lifespan and be unit-tested directly.
"""

from __future__ import annotations

import logging
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..errors import ConfigError

logger = logging.getLogger(__name__)


# 64 lowercase-hex chars — matches ``secrets.token_hex(32)`` output, the
# generator HB uses for ``s2s_signing_key`` per HMAC_S2S_MIGRATION_SPEC §5.
_SIGNING_KEY_RE = re.compile(r"\A[0-9a-f]{64}\Z")

# Soft floor for clock skew before we bail. Well below HB's
# HMAC_TIMESTAMP_SKEW window (300 s); chosen so transient NTP drift
# (typically <5 s) doesn't cause false positives but a forgotten-NTP-
# daemon container (typically minutes off) bails immediately.
_CLOCK_SKEW_BAIL_S: float = 60.0


def validate_signing_key_shape(signing_key: str) -> None:
    """CSSV1 R9.1 — fail fast on a malformed ``RELAY_S2S_SIGNING_KEY``.

    Empty string is allowed (degraded dev mode, logs WARNING). Anything
    else must match :data:`_SIGNING_KEY_RE` exactly — uppercase hex,
    leading/trailing whitespace, prefix like ``hex:``, all reject.

    Raises:
        ConfigError: When the key is set but malformed. The container
            fails its health probe so the orchestrator surfaces the
            misconfiguration in deploy logs rather than mystery 401s
            mid-flight.
    """
    if not signing_key:
        logger.warning(
            "RELAY_S2S_SIGNING_KEY is not set. Relay is running in "
            "DEGRADED dev/test mode — Relay→HB HMAC s2s calls will fail. "
            "For real deploys, pull the key from HB's startup WARNING log "
            "per RELAY_NEXT_STEPS_NOTE_2026_05_09 §1.3."
        )
        return

    if not _SIGNING_KEY_RE.fullmatch(signing_key):
        # Don't put the key in the message — it could end up in deploy
        # logs. Just describe the shape mismatch.
        raise ConfigError(
            "RELAY_S2S_SIGNING_KEY is set but does not match the expected "
            "shape (64 lowercase-hex chars, no prefix, no whitespace). "
            "The value must equal what HB printed in its startup WARNING "
            "log verbatim (HMAC_S2S_MIGRATION_SPEC §5)."
        )


async def check_clock_skew_against_heartbeat(
    heartbeat_api_url: str,
    *,
    timeout_s: float = 3.0,
    bail_threshold_s: float = _CLOCK_SKEW_BAIL_S,
) -> Optional[float]:
    """CSSV1 R9.2 — best-effort NTP discipline check.

    Hits ``GET /health`` on HB with a tight timeout, parses the RFC 7231
    ``Date`` response header, and compares it against ``datetime.now(UTC)``.

    Returns the absolute skew in seconds when both endpoints replied with
    a parseable ``Date`` header; returns ``None`` when the check could
    not run (HB unreachable / no ``Date`` / parse fail). Raises
    :class:`ConfigError` ONLY when HB returned a parseable timestamp and
    the skew exceeds ``bail_threshold_s``.

    Soft-fail design:
    - HB unreachable → WARNING + continue (HB may be cold-starting; a
      hard fail here would create a chicken-and-egg startup ordering bug).
    - ``Date`` missing/unparseable → WARNING + continue (some proxies
      strip the header).
    - Skew > ``bail_threshold_s`` → ConfigError. A ~60 s clock skew at
      startup virtually guarantees HMAC_TIMESTAMP_SKEW 401s at runtime
      (HB's window is 300 s but NTP drift compounds); bail loudly so ops
      sees the misconfig in deploy logs.
    """
    url = heartbeat_api_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            resp = await http.get(url)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
        logger.warning(
            "NTP discipline check skipped — HB unreachable at %s (%s). "
            "Relay startup continues; if HMAC_TIMESTAMP_SKEW 401s appear "
            "post-deploy, run `chronyc tracking` on the container.",
            url, type(e).__name__,
        )
        return None
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "NTP discipline check skipped — unexpected %s on %s: %s",
            type(e).__name__, url, e,
        )
        return None

    date_hdr = resp.headers.get("date") or resp.headers.get("Date")
    if not date_hdr:
        logger.warning(
            "NTP discipline check skipped — HB /health response had no "
            "Date header (proxy may have stripped it). URL=%s",
            url,
        )
        return None

    try:
        hb_time = parsedate_to_datetime(date_hdr)
    except (TypeError, ValueError) as e:
        logger.warning(
            "NTP discipline check skipped — could not parse HB Date %r: %s",
            date_hdr, e,
        )
        return None

    if hb_time.tzinfo is None:
        # parsedate_to_datetime returns naive only when the input was
        # malformed in subtle ways; treat as UTC-best-effort.
        hb_time = hb_time.replace(tzinfo=timezone.utc)

    local_now = datetime.now(timezone.utc)
    skew_s = abs((local_now - hb_time).total_seconds())

    if skew_s > bail_threshold_s:
        raise ConfigError(
            f"Clock skew vs HeartBeat is {skew_s:.1f}s "
            f"(threshold {bail_threshold_s:.0f}s). "
            "Relay's HMAC s2s headers will be rejected by HB once "
            "HMAC_TIMESTAMP_SKEW (300s) trips. Confirm chrony/ntpd is "
            "running on the Relay container before redeploying."
        )

    logger.info(
        "NTP discipline OK — Relay clock vs HB skew=%.2fs (threshold=%.0fs)",
        skew_s, bail_threshold_s,
    )
    return skew_s


__all__ = [
    "validate_signing_key_shape",
    "check_clock_skew_against_heartbeat",
]
