"""
JWKS Cache — local key-material cache for HeartBeat's /.well-known/jwks.json.

Fetches the JWKS on first use and refreshes every TTL_SECONDS (1 hour by
default) or when a ``kid`` is requested that isn't in the current cache
(online key rotation).

The cache is fail-soft: if an HTTP refresh fails, the old keys stay live
so in-flight requests with still-valid tokens are not disrupted.  A cold
start failure (empty cache, HTTP down) propagates the error upward —
``OAuthTokenValidator`` translates that into a 401 rather than allowing
unauthenticated requests through.

Ed25519 JWK wire shape expected from HeartBeat O3:
    { "kty": "OKP", "crv": "Ed25519", "x": "<base64url>", "kid": "<str>" }

RSA / EC keys are ignored (not used by Helium's JWT issuer).
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

# Default TTL before the key-set is considered stale and should be re-fetched.
_DEFAULT_TTL_S: float = 3600.0


def _decode_ed25519_jwk(jwk: Dict[str, Any]) -> Optional[Ed25519PublicKey]:
    """
    Decode an OKP/Ed25519 JWK into a :class:`Ed25519PublicKey`.

    Returns ``None`` (logs a warning) for unsupported key types or
    malformed ``x`` values — the key is simply skipped during import.
    """
    kty = jwk.get("kty", "")
    crv = jwk.get("crv", "")
    if kty != "OKP" or crv != "Ed25519":
        # RSA / EC keys are silently skipped — Helium only issues Ed25519 JWTs.
        return None
    x_b64 = jwk.get("x", "")
    if not x_b64:
        logger.warning("JWKS: OKP/Ed25519 JWK missing 'x' field — skipping")
        return None
    try:
        # RFC 7517 §3 — base64url, no padding required from issuer but
        # Python's urlsafe_b64decode needs it.
        padded = x_b64 + "=" * (-len(x_b64) % 4)
        raw_public = base64.urlsafe_b64decode(padded.encode("ascii"))
        return Ed25519PublicKey.from_public_bytes(raw_public)
    except Exception as exc:
        logger.warning("JWKS: failed to decode Ed25519 key — %s", exc)
        return None


class JWKSCache:
    """
    Fetches and caches JWKS from HeartBeat ``/.well-known/jwks.json``.

    Cache TTL: 1 hour. Refreshes automatically when:
    - The cache has passed its TTL, OR
    - A requested ``kid`` is not present (key rotation).

    The cache is fail-soft on refresh: if the HTTP call fails, the old
    keys survive.  If the cache is empty (cold start) and the HTTP call
    fails, the ``KeyError`` from ``_refresh`` propagates to the caller.
    """

    def __init__(
        self,
        jwks_url: str,
        http_client: httpx.AsyncClient,
        ttl_seconds: float = _DEFAULT_TTL_S,
    ) -> None:
        self._url = jwks_url
        self._client = http_client
        # kid → Ed25519PublicKey
        self._keys: Dict[str, Ed25519PublicKey] = {}
        self._raw_jwks: Dict[str, Dict[str, Any]] = {}  # kid → raw JWK dict
        self._fetched_at: float = 0.0
        self._ttl_seconds = ttl_seconds

    # ── Public API ───────────────────────────────────────────────────────

    async def get_key(self, kid: str) -> Optional[Ed25519PublicKey]:
        """
        Return the ``Ed25519PublicKey`` for this ``kid``.

        Refreshes the JWKS if the cache is stale OR if ``kid`` is
        unknown (online rotation).

        Returns ``None`` if the ``kid`` is not found after a refresh.
        Raises ``httpx.HTTPError`` only on a cold start with no cached
        keys and a failed HTTP fetch.
        """
        if self._is_stale() or kid not in self._keys:
            await self._refresh()
        return self._keys.get(kid)

    async def get_raw_jwk(self, kid: str) -> Optional[Dict[str, Any]]:
        """Return the raw JWK dict for this ``kid`` (for debugging / tests)."""
        if self._is_stale() or kid not in self._raw_jwks:
            await self._refresh()
        return self._raw_jwks.get(kid)

    @property
    def cached_key_ids(self) -> list:
        """Currently cached key IDs (for health endpoints / debug)."""
        return list(self._keys.keys())

    # ── Internal ─────────────────────────────────────────────────────────

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) >= self._ttl_seconds

    async def _refresh(self) -> None:
        """
        Fetch JWKS from HeartBeat.

        On HTTP error: log a warning and keep the old cache intact.
        On a truly empty cache + error, the caller will find nothing
        in ``self._keys`` and return ``None`` / 401.
        """
        try:
            resp = await self._client.get(self._url, timeout=10.0)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            # Fail-soft: old keys survive; cold-start callers get None.
            logger.warning(
                "JWKS refresh failed (old cache preserved) — url=%s err=%s",
                self._url,
                exc,
            )
            return

        keys_list = body.get("keys", [])
        if not isinstance(keys_list, list):
            logger.warning("JWKS response missing 'keys' array — ignoring")
            return

        new_keys: Dict[str, Ed25519PublicKey] = {}
        new_raw: Dict[str, Dict[str, Any]] = {}
        for jwk in keys_list:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if not kid:
                continue
            pub_key = _decode_ed25519_jwk(jwk)
            if pub_key is not None:
                new_keys[kid] = pub_key
                new_raw[kid] = jwk

        self._keys = new_keys
        self._raw_jwks = new_raw
        self._fetched_at = time.monotonic()
        logger.info(
            "JWKS refreshed — %d Ed25519 key(s) loaded (kids=%s)",
            len(new_keys),
            list(new_keys.keys()),
        )
