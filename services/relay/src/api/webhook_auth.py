"""
Ed25519-signed webhook receiver verifier — L5 (LOCKED contract).

HeartBeat (producer) SIGNS each webhook with **Ed25519**, reusing the SAME
Ed25519 signing key it publishes on its OAuth JWKS. Relay (consumer) VERIFIES
that signature here against HB's published Ed25519 public key(s), fetched via
the shared :class:`~src.core.jwks_cache.JWKSCache`.

This REPLACES the earlier symmetric, shared-secret receiver (the 2-header /
``sha256=`` HMAC ``verify_webhook_request`` that shipped on
``feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook``). Per the ARCH
"Bob ratification pass" 2026-06-19 (ledger L5), the symmetric-HMAC ruling was
**reversed**: there is intentionally **no symmetric HMAC code path** in this
module — no shared secret, no ``hmac``/``hashlib`` import, no per-service
signing key. Verification is asymmetric only.

Reuse (no second JWKS/Ed25519 fetcher is written here):
    - :class:`src.core.jwks_cache.JWKSCache` — fetches Ed25519 public keys
      from HB's JWKS (1h TTL, online rotation, fail-soft).
    - The Ed25519 verify pattern is modelled on ``src.core.oauth_validator``
      (``pub_key.verify(raw_sig, signing_input)`` + ``InvalidSignature``).

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
║  LOCKED L5 WEBHOOK CONTRACT  (CONTRACT_LEDGER L5, 2026-06-20)             ║
╠══════════════════════════════════════════════════════════════════════════╣
║ ARCH locked this against HeartBeat #182's signer (ground-truthed). This   ║
║ verifier MUST accept exactly the signatures HB produces. The constants    ║
║ below are the single source of truth for the wire format.                 ║
║                                                                           ║
║ 1. SIGNING INPUT (the bytes HB feeds to Ed25519.sign):                    ║
║        f"{unix_ts}.".encode("utf-8") + raw_body_bytes                     ║
║    i.e. the ASCII of the unix-epoch-seconds timestamp, then a literal     ║
║    ``.``, then the EXACT raw request body bytes. NO webhook_id, NO        ║
║    sha256 digest, NO colons.                                             ║
║                                                                           ║
║ 2. HEADERS:                                                               ║
║        X-HeartBeat-Signature: ed25519=<standard-base64 signature>        ║
║        X-HeartBeat-Timestamp: <unix epoch seconds, ASCII int>            ║
║    The signature value carries a literal ``ed25519=`` prefix; after       ║
║    stripping it, the remainder is STANDARD base64 (``base64.b64decode``,  ║
║    matching Core #176 — NOT urlsafe / no-pad). There is NO Key-Id header  ║
║    and NO Webhook-Id header in the locked contract.                       ║
║                                                                           ║
║ 3. REPLAY WINDOW: 5 minutes (300s) absolute skew on the timestamp.        ║
║                                                                           ║
║ 4. KEY RESOLUTION: no kid travels in the headers, so the key cannot be    ║
║    looked up by a header kid. HB publishes ONE Ed25519 signing key on     ║
║    ``/.well-known/jwks.json`` (rarely 2 during rotation). Relay fetches   ║
║    the JWKS and tries verifying against EACH published Ed25519 key,       ║
║    accepting if ANY verifies (``JWKSCache.get_all_keys()``).             ║
║                                                                           ║
║ 5. (separate harmonization, NOT this task) the MESSAGE-TYPE CATALOGUE —   ║
║    what message kinds HB pushes and how Relay dispatches them. The route  ║
║    verifies + 200s only; dispatch is a documented TODO there.             ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

from cryptography.exceptions import InvalidSignature
from fastapi import Request

from ..config import RelayConfig
from ..core.jwks_cache import JWKSCache
from ..errors import WebhookSignatureError

logger = logging.getLogger(__name__)


# ── LOCKED contract constants (CONTRACT_LEDGER L5, 2026-06-20) ──────────────
#
# Single source of truth for the wire format. Locked against HB #182's signer.

#: Header carrying the Unix-epoch-seconds timestamp (ASCII int).
HEADER_TIMESTAMP = "x-heartbeat-timestamp"
#: Header carrying ``ed25519=<standard-base64 signature>``.
HEADER_SIGNATURE = "x-heartbeat-signature"
#: Literal prefix the signature header value must carry, before the base64.
SIGNATURE_SCHEME_PREFIX = "ed25519="


def _build_signing_input(timestamp: str, body_bytes: bytes) -> bytes:
    """Reconstruct the exact bytes HeartBeat signed.

    LOCKED (CONTRACT_LEDGER L5, 2026-06-20): the UTF-8 ASCII of the
    unix-epoch-seconds timestamp, a literal ``.``, then the EXACT raw request
    body bytes — ``f"{timestamp}.".encode("utf-8") + body_bytes``. No
    webhook_id, no sha256 digest, no colons. HB signs this identical
    construction over the raw body.
    """
    return f"{timestamp}.".encode("utf-8") + body_bytes


class WebhookVerifier:
    """Verifies an inbound HeartBeat webhook's **Ed25519** signature.

    The webhook public key(s) are fetched from HB's published JWKS via the
    shared :class:`JWKSCache` (no second fetcher). The locked contract carries
    no kid header, so verification is attempted against EVERY published
    Ed25519 key and succeeds if ANY one verifies. On ANY failure a
    :class:`WebhookSignatureError` (HTTP 401) is raised — the route lets it
    bubble to the global ``relay_error_handler``.

    Parameters
    ----------
    jwks_cache:
        Shared :class:`JWKSCache` pointed at HB's JWKS URL (the same JWKS that
        publishes the OAuth Ed25519 signing key).
    replay_window_s:
        Max allowed absolute skew (seconds) between the signed timestamp and
        ``now`` before the delivery is rejected as a replay. LOCKED at 300s.
    """

    def __init__(self, jwks_cache: JWKSCache, replay_window_s: int = 300) -> None:
        self._cache = jwks_cache
        self._replay_window_s = replay_window_s

    async def verify(self, request: Request) -> None:
        """Run the full Ed25519 verification chain for one webhook delivery.

        Order (fail-fast, fail-loud — every branch is a 401
        :class:`WebhookSignatureError`; the client-facing message is generic
        so a probe cannot distinguish the failure modes):

            1. ``timestamp`` header present + integer.
            2. ``signature`` header present + ``ed25519=`` prefix + base64.
            3. timestamp inside the replay window.
            4. at least one published Ed25519 key resolvable from the JWKS.
            5. Ed25519 verify of the signature over the reconstructed input
               against EACH published key — accept if ANY verifies.

        On success returns ``None`` and the handler runs. Body bytes are read
        from ``request.state.raw_body`` (populated by ``BodyCacheMiddleware``)
        so the route can still ``await request.json()`` afterwards; falls back
        to ``await request.body()`` if the cache middleware did not run.
        """
        headers = request.headers

        # ── Step 1: timestamp header present + integer ───────────────────
        timestamp_raw = headers.get(HEADER_TIMESTAMP, "")
        if not timestamp_raw or not timestamp_raw.lstrip("-").isdigit():
            raise WebhookSignatureError(reason="missing or non-integer timestamp")

        # ── Step 2: signature header present + ed25519= prefix + base64 ──
        signature_header = headers.get(HEADER_SIGNATURE, "")
        if not signature_header:
            raise WebhookSignatureError(reason="missing signature header")
        if not signature_header.startswith(SIGNATURE_SCHEME_PREFIX):
            raise WebhookSignatureError(
                reason="signature header missing ed25519= prefix"
            )
        signature_b64 = signature_header[len(SIGNATURE_SCHEME_PREFIX):]
        try:
            # Standard base64 (NOT urlsafe / no-pad) — matches Core #176.
            raw_sig = base64.b64decode(signature_b64, validate=True)
        except Exception as exc:
            raise WebhookSignatureError(reason="signature decode failed") from exc

        # ── Step 3: timestamp inside replay window ───────────────────────
        timestamp = int(timestamp_raw)
        now = int(time.time())
        if abs(now - timestamp) > self._replay_window_s:
            logger.warning(
                "Webhook rejected — timestamp %d outside %ds replay window (now=%d).",
                timestamp, self._replay_window_s, now,
            )
            raise WebhookSignatureError(reason="timestamp outside replay window")

        # ── Step 4: resolve the published Ed25519 key(s) ─────────────────
        # The locked contract sends no kid, so we cannot look a single key up.
        # Fetch ALL published Ed25519 keys (HB publishes one, rarely two during
        # rotation) and try each below.
        try:
            pub_keys = await self._cache.get_all_keys()
        except Exception as exc:
            # Cold-start JWKS fetch failure — fail closed (401), never admit
            # an unverified webhook. JWKSCache itself is fail-soft on refresh.
            logger.warning("Webhook rejected — JWKS fetch failed: %s", exc)
            raise WebhookSignatureError(reason="jwks fetch failed") from exc
        if not pub_keys:
            raise WebhookSignatureError(reason="no Ed25519 keys in webhook JWKS")

        # ── Step 5: Ed25519 verify over the reconstructed signing input ──
        # Accept if ANY published key verifies the signature.
        body_bytes = await self._read_body(request)
        signing_input = _build_signing_input(timestamp_raw, body_bytes)
        for pub_key in pub_keys:
            try:
                pub_key.verify(raw_sig, signing_input)
            except InvalidSignature:
                continue  # try the next published key
            except Exception as exc:
                # A non-signature verify error (e.g. malformed key) is unusual;
                # log and keep trying the remaining keys rather than admitting.
                logger.warning("Webhook key verify raised (non-signature): %s", exc)
                continue
            else:
                logger.debug(
                    "Webhook Ed25519 signature verified — ts=%s", timestamp_raw,
                )
                return

        logger.warning(
            "Webhook rejected — Ed25519 signature did not match any of %d "
            "published key(s) (ts=%s).",
            len(pub_keys), timestamp_raw,
        )
        raise WebhookSignatureError(reason="ed25519 signature mismatch")

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
    base ``RELAY_JWKS_URL`` (the same JWKS that publishes HB's OAuth Ed25519
    signing key — the locked contract reuses that one key). Called once at
    startup by ``create_app``'s lifespan with the shared httpx client.
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
    "HEADER_TIMESTAMP",
    "HEADER_SIGNATURE",
    "SIGNATURE_SCHEME_PREFIX",
]
