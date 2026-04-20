"""
HeartBeat introspect client.

Per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.1 and §4, backend services
verify user JWTs by calling POST /api/auth/introspect on HeartBeat. This
module wraps that call with the exact request/response shape, a small
cache hit (single-request only, never cross-request), and the error-code
→ HTTP status mapping defined in the spec.

No local JWT validation happens here. The raw JWT is handed to HeartBeat;
HeartBeat is the sole source of truth for identity (spec §5.1).
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from ..errors import (
    AuthenticationFailedError,
    HeartBeatUnavailableError,
    JWTRejectedError,
)

logger = logging.getLogger(__name__)


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


class IntrospectClient:
    """
    Thin async HTTP client for POST /api/auth/introspect.

    Uses the service's own api_key:api_secret to authenticate the introspect
    call itself (spec §3.1). The JWT being introspected rides in the JSON body.
    """

    def __init__(
        self,
        heartbeat_url: str,
        service_api_key: str,
        service_api_secret: str,
        timeout_s: float = 5.0,
    ):
        self._url = heartbeat_url.rstrip("/") + "/api/auth/introspect"
        self._service_api_key = service_api_key
        self._service_api_secret = service_api_secret
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
        """
        Verify a user JWT against HeartBeat.

        Returns IntrospectResult on active=true.
        Raises mapped Relay errors on active=false per spec §3.1.
        Raises HeartBeatUnavailableError if HB is unreachable or returns 5xx.
        """
        if not self._service_api_key or not self._service_api_secret:
            raise AuthenticationFailedError(
                "Relay has no HeartBeat service credentials configured — "
                "cannot introspect user JWTs. Check RELAY_HEARTBEAT_API_KEY/_SECRET."
            )

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
            raise JWTRejectedError(
                error_code=relay_code,
                message=result.message or "Token not active",
                status_code=http_status,
            )

        return result
