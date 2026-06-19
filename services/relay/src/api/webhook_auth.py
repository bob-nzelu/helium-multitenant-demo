"""
Ed25519-signed webhook receiver verifier — L5 (reworked from symmetric HMAC).

HeartBeat (producer) SIGNS each webhook with **Ed25519**, reusing its OAuth
JWKS Ed25519 infrastructure and publishing the webhook public key. Relay
(consumer) VERIFIES that signature here against HB's published webhook public
key, fetched by ``kid`` via the shared :class:`~src.core.jwks_cache.JWKSCache`.

This REPLACES the earlier symmetric, shared-secret receiver (the 2-header /
``sha256=`` HMAC ``verify_webhook_request`` that shipped on
``feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook``). Per the ARCH
"Bob ratification pass" 2026-06-19 (ledger L5), the symmetric-HMAC ruling was
**reversed**: there is intentionally **no symmetric HMAC code path** in this
module — no shared secret, no ``hmac``/``hashlib`` import, no per-service
signing key. Verification is asymmetric only.

Reuse (no second JWKS/Ed25519 fetcher is written here):
    - :class:`src.core.jwks_cache.JWKSCache` — fetches Ed25519 public keys
      from HB's JWKS by ``kid`` (1h TTL, online rotation, fail-soft).
    - The Ed25519 verify pattern + base64url-decode helper are modelled on
      ``src.core.oauth_validator`` (``pub_key.verify(raw_sig, signing_input)``
      + ``InvalidSignature`` handling).

Layout:
    - :class:`WebhookVerifier` is the verifier. It takes a ``JWKSCache`` and a
      replay window; ``verify(request)`` runs the full check chain and raises
      :class:`~src.errors.WebhookSignatureError` (HTTP 401) on any failure.
    - :func:`get_webhook_verifier` is the FastAPI dependency — pulls the
      verifier off ``app.state.webhook_verifier`` (wired in ``create_app``'s
      lifespan). Wire it via ``Depends(get_webhook_verifier)`` on the route,
      then ``await verifier.verify(request)``.
    - :func:`build_webhook_verifier` constructs the verifier + its backing
      ``JWKSCache`` from a :class:`~src.config.RelayConfig` and an
      ``httpx.AsyncClient`` (used by the lifespan).

╔══════════════════════════════════════════════════════════════════════════╗
║  NEEDS-FROM-HB  —  PROVISIONAL CONTRACT (not finalized; do NOT treat as   ║
║                    settled until HeartBeat publishes the webhook spec).   ║
╠══════════════════════════════════════════════════════════════════════════╣
║ Everything below is Relay's *best-guess* placeholder so the receiver is   ║
║ buildable + testable TODAY. Each item is owned by HeartBeat and MUST be   ║
║ reconciled before this path is enabled against a real HB producer. The    ║
║ constants are centralised (module-level) precisely so a one-line change   ║
║ reconciles each one once HB confirms.                                     ║
║                                                                           ║
║ 1. EXACT SIGNING INPUT (the bytes HB feeds to Ed25519.sign).              ║
║    Provisional: the ASCII bytes of                                        ║
║        f"{webhook_id}:{timestamp}:{sha256_hex(body)}"                     ║
║    i.e. ``"<X-HeartBeat-Webhook-Id>:<X-HeartBeat-Timestamp>:"`` followed  ║
║    by the lowercase hex SHA-256 of the EXACT raw request body bytes.      ║
║    OPEN: does HB sign the raw body directly (like the old HMAC receiver,  ║
║    which signed ``f"{ts}.".encode()+body``) or this digest-of-body form?  ║
║    OPEN: field order, separator char (':' vs '.'), and whether            ║
║    ``webhook_id`` participates at all.                                    ║
║                                                                           ║
║ 2. EXACT HEADER NAMES.                                                    ║
║    Provisional:                                                           ║
║        X-HeartBeat-Key-Id     → JWKS ``kid`` of the webhook signing key   ║
║        X-HeartBeat-Timestamp  → Unix epoch seconds (ASCII int)            ║
║        X-HeartBeat-Signature  → base64url(Ed25519 signature), no padding  ║
║        X-HeartBeat-Webhook-Id → opaque per-delivery id (in signing input) ║
║    OPEN: HB may keep the old ``X-HeartBeat-Signature`` name but DROP the  ║
║    ``sha256=`` prefix (this scheme has no prefix), may rename Key-Id to   ║
║    ``X-HeartBeat-Kid``, and may not send a Webhook-Id at all.             ║
║                                                                           ║
║ 3. WHERE HB PUBLISHES THE WEBHOOK PUBLIC KEY.                             ║
║    Provisional: the SAME ``/.well-known/jwks.json`` as the OAuth signer,  ║
║    distinguished by a distinct ``kid`` and ``use:"sig"`` (so ``jwks_url`` ║
║    is reused and ``RELAY_WEBHOOK_JWKS_URL`` is left empty).               ║
║    OPEN: HB may instead expose a DEDICATED webhook-keys endpoint; if so,  ║
║    set ``RELAY_WEBHOOK_JWKS_URL`` to it (already plumbed in config.py).   ║
║                                                                           ║
║ 4. (separate harmonization, NOT this task) the MESSAGE-TYPE CATALOGUE —   ║
║    what message kinds HB pushes and how Relay dispatches them. The route  ║
║    below verifies + 200s only; dispatch is a documented TODO there.       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from typing import Optional

from cryptography.exceptions import InvalidSignature
from fastapi import Request

from ..config import RelayConfig
from ..core.jwks_cache import JWKSCache
from ..errors import WebhookSignatureError

logger = logging.getLogger(__name__)


# ── PROVISIONAL contract constants (see NEEDS-FROM-HB in the module docstring) ──
#
# These are the single source of truth for the provisional wire format. When
# HeartBeat finalizes the webhook signing spec, reconcile by editing HERE.

#: Header carrying the JWKS ``kid`` of the webhook signing key. PROVISIONAL.
HEADER_KEY_ID = "x-heartbeat-key-id"
#: Header carrying the Unix-epoch-seconds timestamp (ASCII int). PROVISIONAL.
HEADER_TIMESTAMP = "x-heartbeat-timestamp"
#: Header carrying base64url(Ed25519 signature), no padding. PROVISIONAL.
HEADER_SIGNATURE = "x-heartbeat-signature"
#: Header carrying the opaque per-delivery webhook id. PROVISIONAL.
HEADER_WEBHOOK_ID = "x-heartbeat-webhook-id"


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (issuer may omit padding).

    Modelled on ``src.core.oauth_validator._b64url_decode`` — the same helper
    the OAuth path uses to decode the JWT signature segment, kept byte-for-byte
    compatible so both paths agree on the encoding HB emits.
    """
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _build_signing_input(webhook_id: str, timestamp: str, body_bytes: bytes) -> bytes:
    """Reconstruct the exact bytes HeartBeat signed.

    PROVISIONAL (NEEDS-FROM-HB item #1): the ASCII bytes of
    ``f"{webhook_id}:{timestamp}:{sha256_hex(body)}"``. The body is reduced to
    its lowercase-hex SHA-256 so the signed message is bounded regardless of
    payload size; HB MUST sign the identical construction.
    """
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    return f"{webhook_id}:{timestamp}:{body_hash}".encode("ascii")


class WebhookVerifier:
    """Verifies an inbound HeartBeat webhook's **Ed25519** signature.

    The webhook public key is fetched by ``kid`` from HB's published JWKS via
    the shared :class:`JWKSCache` (no second fetcher). On ANY failure a
    :class:`WebhookSignatureError` (HTTP 401) is raised — the route lets it
    bubble to the global ``relay_error_handler``.

    Parameters
    ----------
    jwks_cache:
        Shared :class:`JWKSCache` pointed at HB's webhook-key JWKS URL.
    replay_window_s:
        Max allowed absolute skew (seconds) between the signed timestamp and
        ``now`` before the delivery is rejected as a replay. PROVISIONAL.
    """

    def __init__(self, jwks_cache: JWKSCache, replay_window_s: int = 300) -> None:
        self._cache = jwks_cache
        self._replay_window_s = replay_window_s

    async def verify(self, request: Request) -> None:
        """Run the full Ed25519 verification chain for one webhook delivery.

        Order (fail-fast, fail-loud — every branch is a 401
        :class:`WebhookSignatureError`; the client-facing message is generic
        so a probe cannot distinguish the failure modes):

            1. ``kid`` header present.
            2. ``timestamp`` header present + integer.
            3. ``signature`` header present.
            4. timestamp inside the replay window.
            5. webhook public key for ``kid`` resolvable from the JWKS.
            6. signature base64url-decodes.
            7. Ed25519 verify of the signature over the reconstructed input.

        On success returns ``None`` and the handler runs. Body bytes are read
        from ``request.state.raw_body`` (populated by ``BodyCacheMiddleware``)
        so the route can still ``await request.json()`` afterwards; falls back
        to ``await request.body()`` if the cache middleware did not run.
        """
        headers = request.headers

        # ── Step 1: kid header ───────────────────────────────────────────
        kid = headers.get(HEADER_KEY_ID, "")
        if not kid:
            raise WebhookSignatureError(reason="missing key-id header")

        # ── Step 2: timestamp header present + integer ───────────────────
        timestamp_raw = headers.get(HEADER_TIMESTAMP, "")
        if not timestamp_raw or not timestamp_raw.lstrip("-").isdigit():
            raise WebhookSignatureError(reason="missing or non-integer timestamp")

        # ── Step 3: signature header present ─────────────────────────────
        signature_b64 = headers.get(HEADER_SIGNATURE, "")
        if not signature_b64:
            raise WebhookSignatureError(reason="missing signature header")

        # ── Step 4: timestamp inside replay window ───────────────────────
        timestamp = int(timestamp_raw)
        now = int(time.time())
        if abs(now - timestamp) > self._replay_window_s:
            logger.warning(
                "Webhook rejected — timestamp %d outside %ds replay window (now=%d).",
                timestamp, self._replay_window_s, now,
            )
            raise WebhookSignatureError(reason="timestamp outside replay window")

        # ── Step 5: resolve webhook public key by kid ────────────────────
        try:
            pub_key = await self._cache.get_key(kid)
        except Exception as exc:
            # Cold-start JWKS fetch failure — fail closed (401), never admit
            # an unverified webhook. JWKSCache itself is fail-soft on refresh.
            logger.warning("Webhook rejected — JWKS fetch failed: %s", exc)
            raise WebhookSignatureError(reason="jwks fetch failed") from exc
        if pub_key is None:
            raise WebhookSignatureError(reason=f"kid={kid!r} not in webhook JWKS")

        # ── Step 6: decode signature ─────────────────────────────────────
        try:
            raw_sig = _b64url_decode(signature_b64)
        except Exception as exc:
            raise WebhookSignatureError(reason="signature decode failed") from exc

        # ── Step 7: Ed25519 verify over the reconstructed signing input ──
        webhook_id = headers.get(HEADER_WEBHOOK_ID, "")
        body_bytes = await self._read_body(request)
        signing_input = _build_signing_input(webhook_id, timestamp_raw, body_bytes)
        try:
            pub_key.verify(raw_sig, signing_input)
        except InvalidSignature as exc:
            logger.warning(
                "Webhook rejected — Ed25519 signature mismatch (kid=%s, ts=%s).",
                kid, timestamp_raw,
            )
            raise WebhookSignatureError(reason="ed25519 signature mismatch") from exc
        except Exception as exc:
            raise WebhookSignatureError(
                reason=f"ed25519 verify error: {exc}"
            ) from exc

        logger.debug(
            "Webhook Ed25519 signature verified — kid=%s webhook_id=%s ts=%s",
            kid, webhook_id or "(none)", timestamp_raw,
        )

    @staticmethod
    async def _read_body(request: Request) -> bytes:
        """Return the raw request body bytes (from the body cache if present)."""
        body_bytes = getattr(request.state, "raw_body", None)
        if body_bytes is None:
            # BodyCacheMiddleware should populate this; if not, read directly
            # (downstream handler loses idempotency, which is correct on this
            # failure path).
            body_bytes = await request.body()
        return body_bytes


def build_webhook_verifier(
    config: RelayConfig,
    http_client,  # httpx.AsyncClient — typed loosely to avoid a hard import here
) -> WebhookVerifier:
    """Construct a :class:`WebhookVerifier` + its backing :class:`JWKSCache`.

    The JWKS URL is ``RELAY_WEBHOOK_JWKS_URL`` if set, else falls back to the
    base ``RELAY_JWKS_URL`` (the same-JWKS-distinct-kid hypothesis — see
    NEEDS-FROM-HB item #3). Called once at startup by ``create_app``'s
    lifespan with the shared httpx client.
    """
    jwks_url = config.webhook_jwks_url or config.jwks_url
    cache = JWKSCache(jwks_url=jwks_url, http_client=http_client)
    return WebhookVerifier(cache, replay_window_s=config.webhook_replay_window_s)


async def get_webhook_verifier(request: Request) -> WebhookVerifier:
    """FastAPI dependency — return the app-scoped :class:`WebhookVerifier`.

    Pulls the verifier off ``request.app.state.webhook_verifier`` (wired in
    the lifespan). Raises a 401 ``WebhookSignatureError`` rather than a 500 if
    the verifier was never wired, so a misconfigured deploy fails closed on
    the webhook path instead of admitting traffic.
    """
    verifier: Optional[WebhookVerifier] = getattr(
        request.app.state, "webhook_verifier", None
    )
    if verifier is None:
        logger.error(
            "Webhook rejected — app.state.webhook_verifier not wired. "
            "Check create_app lifespan."
        )
        raise WebhookSignatureError(reason="webhook verifier not configured")
    return verifier


__all__ = [
    "WebhookVerifier",
    "build_webhook_verifier",
    "get_webhook_verifier",
    "HEADER_KEY_ID",
    "HEADER_TIMESTAMP",
    "HEADER_SIGNATURE",
    "HEADER_WEBHOOK_ID",
]
