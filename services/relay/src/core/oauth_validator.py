"""
OAuth JWT Validator — local JWKS-backed validation for ``aud=helium.relay-ingest``.

This module validates externally-issued OAuth 2.0 client-credentials JWTs
that carry ``aud=helium.relay-ingest``.  Validation is done LOCALLY using
cached JWKS — no per-request HeartBeat round-trip.

Validation steps (§Q37 Gap #2):
1. Decode header (unverified) → extract ``kid``.
2. Fetch ``Ed25519PublicKey`` from :class:`JWKSCache` by ``kid``.
3. Verify Ed25519 signature over ``{header}.{payload}`` bytes.
4. Check: ``aud == "helium.relay-ingest"``, ``exp + CLOCK_SKEW_S > now``.
5. Check Redis jti blocklist (``jti:<jti>`` key) — fail-open if Redis down.
6. Return decoded claims dict.

This code is gated on HeartBeat O3 (the JWKS endpoint).  Until O3 ships no
real tokens exist, so this path is built but never reachable in production.

Error type
----------
On any validation failure a :class:`JWTValidationError` is raised (HTTP 401,
error_code ``"AUTHENTICATION_FAILED"``).  The caller in ``deps.py`` catches
this and lets it bubble as-is to FastAPI's error handler.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature

from ..clients.redis_client import RedisClient
from ..errors import AuthenticationFailedError
from .jwks_cache import JWKSCache

logger = logging.getLogger(__name__)


class JWTValidationError(AuthenticationFailedError):
    """
    Local JWT validation failed (wrong aud, expired, bad signature, etc.).

    Subclasses :class:`AuthenticationFailedError` so it maps to HTTP 401
    with ``error_code="AUTHENTICATION_FAILED"`` without any extra handler
    wiring.
    """

    def __init__(self, message: str = "JWT validation failed"):
        super().__init__(message=message)


# ── JWT decode helpers ────────────────────────────────────────────────────


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (no-padding required from issuer)."""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _decode_header_unverified(token: str) -> Dict[str, Any]:
    """
    Decode only the JWT header WITHOUT verifying the signature.

    Raises :class:`JWTValidationError` if the token isn't well-formed JWS.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTValidationError("Malformed JWT: expected header.payload.signature")
    try:
        header_bytes = _b64url_decode(parts[0])
        return json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:
        raise JWTValidationError(f"JWT header decode failed: {exc}") from exc


def _decode_payload_unverified(token: str) -> Dict[str, Any]:
    """
    Decode only the JWT payload WITHOUT verifying the signature.

    Raises :class:`JWTValidationError` if the payload isn't valid JSON.
    """
    parts = token.split(".")
    try:
        payload_bytes = _b64url_decode(parts[1])
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise JWTValidationError(f"JWT payload decode failed: {exc}") from exc


# ── Main validator ────────────────────────────────────────────────────────


class OAuthTokenValidator:
    """
    Validates ``aud=helium.relay-ingest`` JWTs locally using cached JWKS.

    Instantiated once at app startup (in ``create_app``), stored on
    ``app.state.oauth_validator``.  Thread-safe in the asyncio sense
    (single-threaded event loop + ``await`` on the JWKS fetch).

    Parameters
    ----------
    jwks_cache:
        Shared :class:`JWKSCache` instance pre-loaded with the JWKS URL.
    """

    REQUIRED_AUD: str = "helium.relay-ingest"
    CLOCK_SKEW_S: int = 60

    def __init__(self, jwks_cache: JWKSCache) -> None:
        self._cache = jwks_cache

    async def validate(
        self,
        token: str,
        redis_client: Optional[RedisClient] = None,
    ) -> Dict[str, Any]:
        """
        Validate the token and return decoded claims on success.

        Parameters
        ----------
        token:
            Raw compact-serialized JWT string (no ``Bearer `` prefix).
        redis_client:
            Optional :class:`RedisClient` for blocklist check.  If
            ``None`` or Redis is down, the blocklist check is skipped
            (fail-open per §Q37 Gap #2 spec).

        Returns
        -------
        dict
            Decoded claims (``sub``, ``aud``, ``exp``, ``jti``,
            ``tenant_id``, etc.) from the JWT payload.

        Raises
        ------
        JWTValidationError
            On any validation failure (signature mismatch, wrong aud,
            expired, unknown kid, blocklisted jti).
        """
        # ── Step 1: decode header (no verify) → kid ──────────────────
        header = _decode_header_unverified(token)
        kid = header.get("kid")
        if not kid:
            raise JWTValidationError("JWT header missing 'kid' claim")
        # Pin the algorithm to EdDSA and reject everything else — missing /
        # empty alg, "none", and any HS*/RS*/ES* (spec R1; the classic JWT
        # algorithm-confusion defense). Signature verification below is
        # hardcoded to Ed25519 regardless, but we refuse a token that does not
        # explicitly declare EdDSA rather than leaning on that as the only
        # backstop. No carve-out for empty alg — test tokens declare EdDSA too.
        alg = header.get("alg")
        if alg != "EdDSA":
            raise JWTValidationError(
                f"Unsupported JWT 'alg'={alg!r}; only EdDSA is accepted"
            )

        # ── Step 2: fetch public key by kid ──────────────────────────
        pub_key = await self._cache.get_key(kid)
        if pub_key is None:
            raise JWTValidationError(
                f"JWT kid={kid!r} not found in JWKS (tried refresh)"
            )

        # ── Step 3: verify Ed25519 signature ─────────────────────────
        parts = token.split(".")
        # The signed message is the ASCII bytes of "header_b64url.payload_b64url".
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        try:
            raw_sig = _b64url_decode(parts[2])
        except Exception as exc:
            raise JWTValidationError(f"JWT signature decode failed: {exc}") from exc

        try:
            pub_key.verify(raw_sig, signing_input)
        except InvalidSignature:
            raise JWTValidationError("JWT signature verification failed")
        except Exception as exc:
            raise JWTValidationError(
                f"JWT signature verification error: {exc}"
            ) from exc

        # ── Step 4: decode payload + check aud / exp ─────────────────
        claims = _decode_payload_unverified(token)

        # aud check — must equal exactly our required audience string.
        aud = claims.get("aud")
        # RFC 7519 §4.1.3: aud can be a string OR a list of strings.
        if isinstance(aud, list):
            if self.REQUIRED_AUD not in aud:
                raise JWTValidationError(
                    f"JWT aud {aud!r} does not include {self.REQUIRED_AUD!r}"
                )
        elif aud != self.REQUIRED_AUD:
            raise JWTValidationError(
                f"JWT aud={aud!r} does not match required {self.REQUIRED_AUD!r}"
            )

        # exp check (with clock skew tolerance)
        exp = claims.get("exp")
        if exp is None:
            raise JWTValidationError("JWT missing 'exp' claim")
        now = time.time()
        if now > (exp + self.CLOCK_SKEW_S):
            raise JWTValidationError(
                f"JWT expired: exp={exp}, now={now:.0f}, skew={self.CLOCK_SKEW_S}s"
            )

        # ── Step 5: Redis jti blocklist check (fail-open) ────────────
        jti = claims.get("jti")
        if jti and redis_client is not None:
            try:
                blocked = await _check_jti_blocklist(redis_client, jti)
                if blocked:
                    raise JWTValidationError(
                        f"JWT jti={jti!r} is in the revocation blocklist"
                    )
            except JWTValidationError:
                raise
            except Exception as exc:
                # Redis down or any other error → fail-open (let through).
                logger.warning(
                    "jti blocklist check failed (fail-open) — jti=%s err=%s",
                    jti,
                    exc,
                )

        logger.debug(
            "OAuth JWT validated — kid=%s tenant=%s jti=%s",
            kid,
            claims.get("tenant_id"),
            jti,
        )
        return claims


async def _check_jti_blocklist(redis_client: RedisClient, jti: str) -> bool:
    """
    Check whether ``jti`` appears in the Redis revocation blocklist.

    Key convention: ``jti:<jti>``  (any truthy value = blocked).

    Returns ``True`` if blocked, ``False`` if not blocked.
    Raises on Redis errors — caller is responsible for fail-open handling.
    """
    if not redis_client.is_available or redis_client._redis is None:
        return False
    key = f"jti:{jti}"
    val = await redis_client._redis.get(key)
    return val is not None
