"""
HeartBeat introspect client.

Per ``BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md`` §3.1 and §4, backend
services verify user JWTs by calling ``POST /api/auth/introspect`` on
HeartBeat. This module wraps that call with the exact request/response
shape, a small cache hit (single-request only, never cross-request),
and the error-code → HTTP status mapping defined in the spec.

Auth (post-HMAC-cutover 2026-05-08 per ``HMAC_S2S_MIGRATION_SPEC.md``
+ ``RELAY_NEXT_STEPS_NOTE_2026_05_09`` §1.5): §3.3 HMAC-SHA256 service
auth on the introspect call itself, with the four canonical headers
built via :func:`build_s2s_hmac_headers`. The legacy
``Authorization: Bearer api_key:api_secret`` form is rejected with
``401 BEARER_S2S_REMOVED``. The user JWT being introspected rides in
the JSON body, unchanged.

No local JWT validation happens here. The raw JWT is handed to
HeartBeat; HeartBeat is the sole source of truth for identity
(spec §5.1).

Body-bytes discipline (``HMAC_S2S_MIGRATION_SPEC`` §8.1 step 3): the
JSON payload is serialised to bytes ONCE, signed, and sent via
``content=...`` so the wire matches what we signed.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from ._s2s_hmac import build_s2s_hmac_headers
from ..errors import (
    AuthenticationFailedError,
    HeartBeatUnavailableError,
    JWTRejectedError,
)
from ..observability import counters

logger = logging.getLogger(__name__)


# CSSV1 R9.3 — see ``clients.heartbeat`` for full rationale. Mirrors the
# alarm path on the introspect-only HTTP transport (which doesn't share
# ``HeartBeatClient._raise_for_status``).
_BEARER_REMOVED_CODE = "BEARER_S2S_REMOVED"


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


# Spec §3.1 error_code → (HTTP status, Relay error class)
_ERROR_HTTP_MAP = {
    "TOKEN_INVALID":   (401, "TOKEN_INVALID"),
    "TOKEN_EXPIRED":   (401, "TOKEN_EXPIRED"),
    "SESSION_EXPIRED": (401, "SESSION_EXPIRED"),
    "DEVICE_MISMATCH": (401, "DEVICE_MISMATCH"),
    "PERMISSION_DENIED": (403, "PERMISSION_DENIED"),
    "STEPUP_REQUIRED": (403, "STEPUP_REQUIRED"),
    "ACCOUNT_LOCKED":  (423, "ACCOUNT_LOCKED"),
}


_INTROSPECT_PATH = "/api/auth/introspect"


class IntrospectClient:
    """Thin async HTTP client for ``POST /api/auth/introspect``.

    Authenticates the introspect call itself with §3.3 HMAC-SHA256
    headers built from the service's ``api_key`` plus the per-service
    signing key from HB's startup log
    (``RELAY_S2S_SIGNING_KEY``). The user JWT being introspected rides
    in the JSON body.
    """

    def __init__(
        self,
        heartbeat_url: str,
        service_api_key: str,
        service_api_secret: str = "",
        service_signing_key: str = "",
        timeout_s: float = 5.0,
    ):
        # ``heartbeat_url`` is kept whole so we can derive the
        # ``path`` (used in the HMAC signing input) at request time
        # without re-parsing.
        self._heartbeat_url = heartbeat_url.rstrip("/")
        self._url = self._heartbeat_url + _INTROSPECT_PATH
        self._service_api_key = service_api_key
        # Retained for backwards compatibility / config readers; HB no
        # longer accepts the bcrypt-hashed secret on the request path
        # post-HMAC cutover.
        self._service_api_secret = service_api_secret
        # Required for HMAC. 64-hex chars from HB's startup WARNING log.
        self._service_signing_key = service_signing_key
        self._timeout_s = timeout_s
        self._http: Optional[httpx.AsyncClient] = None

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
    ) -> IntrospectResult:
        """Verify a user JWT against HeartBeat.

        Returns ``IntrospectResult`` on ``active=true``.
        Raises mapped Relay errors on ``active=false`` per spec §3.1.
        Raises ``HeartBeatUnavailableError`` if HB is unreachable or
        returns 5xx.
        """
        if not self._service_api_key:
            raise AuthenticationFailedError(
                "Relay has no HeartBeat service api_key configured — "
                "cannot introspect user JWTs. Check RELAY_HEARTBEAT_API_KEY."
            )
        if not self._service_signing_key:
            raise AuthenticationFailedError(
                "Relay has no HeartBeat s2s signing key configured — "
                "cannot introspect user JWTs. Pull RELAY_S2S_SIGNING_KEY "
                "from HB's startup WARNING log per "
                "RELAY_NEXT_STEPS_NOTE_2026_05_09 §1.3."
            )

        # Build the request body ONCE (per spec §8.1 step 3 body-bytes
        # discipline) so the bytes we sign equal the bytes httpx puts on
        # the wire via ``content=``.
        payload: Dict[str, Any] = {"token": jwt_token}
        if required_permission is not None:
            payload["required_permission"] = required_permission
        if required_within_seconds is not None:
            payload["required_within_seconds"] = required_within_seconds
        body_bytes = json.dumps(payload).encode("utf-8")

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        headers.update(
            build_s2s_hmac_headers(
                method="POST",
                path=_INTROSPECT_PATH,
                body_bytes=body_bytes,
                api_key=self._service_api_key,
                signing_key=self._service_signing_key,
            )
        )
        if trace_id:
            headers["X-Trace-ID"] = trace_id

        try:
            resp = await self._client().post(
                self._url, content=body_bytes, headers=headers
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
            # CSSV1 R9.3 — alarm if HB returned the BEARER_S2S_REMOVED code.
            # Means the introspect call shipped Bearer api_key:api_secret
            # somehow (header builder regression / config drift).
            try:
                body = resp.json()
            except Exception:
                body = None
            if isinstance(body, dict) and body.get("error_code") == _BEARER_REMOVED_CODE:
                counters.inc(
                    "relay_bearer_removed_received_total",
                    labels={"endpoint": "introspect"},
                )
                logger.error(
                    "BEARER_S2S_REMOVED received on introspect — Relay sent "
                    "the dead Bearer s2s form. This is a regression: every "
                    "introspect call should go through "
                    "build_s2s_hmac_headers(). (Rate alarm: "
                    "relay_bearer_removed_received_total{endpoint=\"introspect\"}.)",
                    extra={"trace_id": trace_id},
                )
            # Our own service creds are invalid — config error, not user-token issue
            logger.error(
                "Introspect call rejected with 401 — Relay's own HB s2s "
                "credentials are invalid. Check RELAY_HEARTBEAT_API_KEY + "
                "RELAY_S2S_SIGNING_KEY (and confirm Relay's clock is NTP-"
                "synced; skew >300s causes HMAC_TIMESTAMP_SKEW 401).",
                extra={"trace_id": trace_id},
            )
            raise HeartBeatUnavailableError(
                message="Relay s2s credentials rejected by HeartBeat",
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
            raise JWTRejectedError(
                error_code=relay_code,
                message=result.message or "Token not active",
                status_code=http_status,
            )

        return result
