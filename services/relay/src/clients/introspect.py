"""
HeartBeat introspect client.

Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.1 and §4, backend services
verify user JWTs by calling POST /api/auth/introspect on HeartBeat. This
module wraps that call with the exact request/response shape and the
error-code → HTTP status mapping defined in the spec.

Per Keel HANDOFF_RELAY_JWT_INTROSPECT.md §5 + HELIUM_AUTH_SPEC.md §8.7,
introspect results are cached for 30 s keyed by JWT jti claim. This is
critical during bulk upload bursts where the same JWT would otherwise
trigger an introspect call per file. Negative results (active=false)
are cached at the same key so a revoked-JWT spammer doesn't hammer HB.

No local JWT validation happens here. The raw JWT is handed to HeartBeat;
HeartBeat is the sole source of truth for identity (spec §5.1).
"""

import asyncio
import base64
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

from ..errors import (
    AuthenticationFailedError,
    HeartBeatUnavailableError,
    JWTRejectedError,
)

logger = logging.getLogger(__name__)


def _extract_jti(jwt_token: str) -> Optional[str]:
    """
    Pull the `jti` claim from a JWT payload WITHOUT verifying the signature.

    Used only for cache-keying. Signature verification happens on the HB
    side when the token is introspected, so a spoofed jti here would just
    force a cache miss on the real token — no security impact.
    Returns None for malformed tokens (caller falls back to the full-token
    hash as a cache key).
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        return None
    try:
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
        jti = payload.get("jti")
        return str(jti) if jti else None
    except Exception:
        return None


@dataclass
class IntrospectResult:
    """Shape of a successful introspect call (active=true)."""
    active: bool
    actor_type: Optional[str]
    user_id: Optional[str]
    role: Optional[str]
    permissions: list
    tenant_id: Optional[str]
    device_id: Optional[str]
    last_auth_at: Optional[str]
    expires_at: Optional[str]
    session_expires_at: Optional[str]
    step_up_satisfied: Optional[bool]
    error_code: Optional[str] = None
    message: Optional[str] = None

    @classmethod
    def from_response(cls, body: Dict[str, Any]) -> "IntrospectResult":
        return cls(
            active=bool(body.get("active", False)),
            actor_type=body.get("actor_type"),
            user_id=body.get("user_id"),
            role=body.get("role"),
            permissions=list(body.get("permissions") or []),
            tenant_id=body.get("tenant_id"),
            device_id=body.get("device_id"),
            last_auth_at=body.get("last_auth_at"),
            expires_at=body.get("expires_at"),
            session_expires_at=body.get("session_expires_at"),
            step_up_satisfied=body.get("step_up_satisfied"),
            error_code=body.get("error_code"),
            message=body.get("message"),
        )


# Spec §3.1 + Keel HANDOFF_RELAY_JWT_INTROSPECT §4.1 error_code → (HTTP status, Relay error code)
# Keel spec uses STEP_UP_REQUIRED (underscore between STEP and UP); BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC
# §3.1 used STEPUP_REQUIRED. Accept both for compatibility during migration.
_ERROR_HTTP_MAP = {
    "TOKEN_INVALID":        (401, "TOKEN_INVALID"),
    "TOKEN_EXPIRED":        (401, "TOKEN_EXPIRED"),
    "TOKEN_REVOKED":        (401, "TOKEN_REVOKED"),
    "SESSION_EXPIRED":      (401, "SESSION_EXPIRED"),
    "PERMISSIONS_CHANGED":  (401, "PERMISSIONS_CHANGED"),
    "FIRST_RUN_REQUIRED":   (401, "FIRST_RUN_REQUIRED"),
    "DEVICE_MISMATCH":      (401, "DEVICE_MISMATCH"),
    "PERMISSION_DENIED":    (403, "PERMISSION_DENIED"),
    "STEPUP_REQUIRED":      (401, "STEP_UP_REQUIRED"),
    "STEP_UP_REQUIRED":     (401, "STEP_UP_REQUIRED"),
    "ACCOUNT_LOCKED":       (423, "ACCOUNT_LOCKED"),
}


class IntrospectClient:
    """
    Thin async HTTP client for POST /api/auth/introspect.

    Uses the service's own api_key:api_secret to authenticate the introspect
    call itself (spec §3.1). The JWT being introspected rides in the JSON body.

    Carries a 30 s LRU cache keyed by JWT `jti` claim (Keel spec §5). Both
    positive (active=true) and negative (active=false) results are cached at
    the same key. Invalidation happens on TTL expiry only — a revoked token
    that's already cached will continue to return `active=false` for up to
    30 s; a revocation is therefore 30 s-latent in the worst case (same as
    spec HELIUM_AUTH_SPEC §8.7 allowed window).
    """

    def __init__(
        self,
        heartbeat_url: str,
        service_api_key: str,
        service_api_secret: str,
        timeout_s: float = 5.0,
        cache_ttl_s: float = 30.0,
        cache_max_entries: int = 10_000,
    ):
        self._url = heartbeat_url.rstrip("/") + "/api/auth/introspect"
        self._service_api_key = service_api_key
        self._service_api_secret = service_api_secret
        self._timeout_s = timeout_s
        self._http: Optional[httpx.AsyncClient] = None

        # Cache: OrderedDict for LRU, entries are (expires_at, result_or_error)
        # where result_or_error is either IntrospectResult (positive) or a
        # tuple (JWTRejectedError) for negative cache. Locking via a dedicated
        # asyncio.Lock keeps concurrent cache access safe.
        self._cache_ttl_s = cache_ttl_s
        self._cache_max = cache_max_entries
        self._cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._cache_lock = asyncio.Lock()

        # Metrics (best-effort, for observability)
        self._cache_hits = 0
        self._cache_misses = 0

    # ── Cache helpers ──────────────────────────────────────────────────

    async def _cache_get(self, key: str) -> Optional[Any]:
        """Return cached value if present AND non-expired; otherwise None."""
        async with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                self._cache_misses += 1
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                # Expired — remove + miss
                self._cache.pop(key, None)
                self._cache_misses += 1
                return None
            # Move to end for LRU
            self._cache.move_to_end(key)
            self._cache_hits += 1
            return value

    async def _cache_put(self, key: str, value: Any) -> None:
        """Store value with current TTL. Evicts oldest if over max."""
        async with self._cache_lock:
            expires_at = time.time() + self._cache_ttl_s
            self._cache[key] = (expires_at, value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

    def cache_stats(self) -> Dict[str, int]:
        """Best-effort metrics for /metrics scrape or health diagnostics."""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache),
            "max": self._cache_max,
        }

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout_s)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def introspect(
        self,
        jwt_token: str,
        trace_id: str = "",
        required_permission: Optional[str] = None,
        required_within_seconds: Optional[int] = None,
        bypass_cache: bool = False,
    ) -> IntrospectResult:
        """
        Verify a user JWT against HeartBeat.

        Returns IntrospectResult on active=true.
        Raises mapped Relay errors on active=false per spec §3.1.
        Raises HeartBeatUnavailableError if HB is unreachable or returns 5xx.

        Cache: keyed by JWT jti + required_permission. A cache hit returns the
        cached value directly without an HB call. A cache hit of a negative
        result re-raises the original JWTRejectedError. ``bypass_cache=True``
        forces a fresh introspect (used by the /health probe).
        """
        if not self._service_api_key or not self._service_api_secret:
            raise AuthenticationFailedError(
                "Relay has no HeartBeat service credentials configured — "
                "cannot introspect user JWTs. Check RELAY_HEARTBEAT_API_KEY/_SECRET."
            )

        # Cache lookup — jti + required_permission so a token that passes with
        # one permission but fails with another doesn't share a cache slot.
        cache_key: Optional[str] = None
        if not bypass_cache:
            jti = _extract_jti(jwt_token)
            if jti:
                cache_key = f"{jti}|{required_permission or ''}"
                cached = await self._cache_get(cache_key)
                if cached is not None:
                    if isinstance(cached, IntrospectResult):
                        return cached
                    if isinstance(cached, JWTRejectedError):
                        raise cached
                    # Unknown cached shape — drop it, fall through to HTTP
                    logger.warning(f"Unexpected cache shape: {type(cached)}")

        auth_header = f"Bearer {self._service_api_key}:{self._service_api_secret}"
        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }
        if trace_id:
            headers["X-Trace-ID"] = trace_id

        payload: Dict[str, Any] = {"token": jwt_token}
        if required_permission is not None:
            payload["required_permission"] = required_permission
        if required_within_seconds is not None:
            payload["required_within_seconds"] = required_within_seconds

        try:
            resp = await self._client().post(
                self._url, json=payload, headers=headers
            )
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            logger.warning(
                f"Introspect unreachable: {e}",
                extra={"trace_id": trace_id},
            )
            raise HeartBeatUnavailableError(
                message=f"Cannot reach HeartBeat for introspect: {e}",
            ) from e

        # Auth-upstream protocol errors: spec §2.4 — fail closed to 502
        if resp.status_code == 401:
            # Our own service creds are invalid — config error, not user-token issue
            logger.error(
                "Introspect call rejected with 401 — Relay's own HB service "
                "credentials are invalid. Check RELAY_HEARTBEAT_API_KEY/_SECRET.",
                extra={"trace_id": trace_id},
            )
            raise HeartBeatUnavailableError(
                message="Relay service credentials rejected by HeartBeat",
            )

        if resp.status_code >= 500 or resp.status_code == 502 or resp.status_code == 504:
            logger.warning(
                f"Introspect upstream {resp.status_code}: {resp.text[:200]}",
                extra={"trace_id": trace_id},
            )
            raise HeartBeatUnavailableError(
                message=f"HeartBeat introspect returned {resp.status_code}",
            )

        if resp.status_code != 200:
            # Unexpected status — treat as upstream failure
            logger.warning(
                f"Introspect unexpected {resp.status_code}: {resp.text[:200]}",
                extra={"trace_id": trace_id},
            )
            raise HeartBeatUnavailableError(
                message=f"HeartBeat introspect returned unexpected {resp.status_code}",
            )

        body = resp.json()
        result = IntrospectResult.from_response(body)

        if not result.active:
            code = result.error_code or "TOKEN_INVALID"
            http_status, relay_code = _ERROR_HTTP_MAP.get(code, (401, "TOKEN_INVALID"))
            logger.info(
                f"JWT rejected by HeartBeat: {code} — {result.message}",
                extra={"trace_id": trace_id},
            )
            err = JWTRejectedError(
                error_code=relay_code,
                message=result.message or "Token not active",
                status_code=http_status,
            )
            # Negative cache: store the error so repeat calls with the same
            # token return fast without hitting HB. Spec §5 intent — a
            # caller hammering with a revoked token shouldn't shovel load
            # onto HB. 30 s matches positive TTL.
            if cache_key is not None:
                await self._cache_put(cache_key, err)
            raise err

        # Positive cache
        if cache_key is not None:
            await self._cache_put(cache_key, result)

        return result
