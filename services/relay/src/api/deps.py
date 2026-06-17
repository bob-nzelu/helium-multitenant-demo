"""
FastAPI Dependency Injection

Dependencies for request authentication, decryption, and service access.
Auth + encryption handled here (not middleware) for cleaner header+body access.

Auth dispatcher per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.5:
    - HMAC (X-API-Key + X-Signature + X-Timestamp) → ERP ingress (§3.3)
    - Bearer <jwt>                                  → user via HB introspect (§3.1)
    - Bearer <api_key>:<api_secret>                 → service-to-service (§3.2)

Produces a CallerContext that handlers consume uniformly.
"""

import base64
import hmac
import json
import logging
from typing import Any, Dict, Optional

from fastapi import Header, Request
from uuid6 import uuid7

from ..config import RelayConfig
from ..core.auth import authenticate as hmac_authenticate
from ..core.oauth_validator import JWTValidationError, OAuthTokenValidator
from ..crypto.envelope import decrypt as nacl_decrypt
from ..errors import (
    AuthenticationFailedError,
    AuthUpstreamUnavailableError,
    EncryptionRequiredError,
    HeartBeatUnavailableError,
    InvalidAPIKeyError,
    JWTRejectedError,
)
from .caller_context import CallerContext

logger = logging.getLogger(__name__)


# ── App-state accessors ──────────────────────────────────────────────────

def get_config(request: Request) -> RelayConfig:
    """Get RelayConfig from app state."""
    return request.app.state.config


def get_module_cache(request: Request) -> Any:
    """Get TransformaModuleCache from app state."""
    return request.app.state.module_cache


def get_bulk_service(request: Request) -> Any:
    """Get BulkService from app state."""
    return request.app.state.bulk_service


def get_external_service(request: Request) -> Any:
    """Get ExternalService from app state."""
    return request.app.state.external_service


# ── Auth dispatcher ──────────────────────────────────────────────────────

def _tenant_id_for_api_key(request: Request, api_key: str) -> str:
    """
    Resolve tenant_id from an api_key via the tenants.json registry.

    Falls back to "_unknown" if the key isn't in any tenant record — this
    happens for dev keys (RELAY_DEV_API_KEY) and for service-to-service
    credentials that aren't in the tenant registry.
    """
    registry = getattr(request.app.state, "tenant_registry", {}) or {}
    tenant = registry.get(api_key)
    if tenant is None:
        return "_unknown"
    # tenant is a Tenant dataclass with tenant_id (short name used as key
    # in tenants.json). We prefer the explicit tenant_id field if present.
    return getattr(tenant, "tenant_id", None) or getattr(tenant, "service_id", None) or "_unknown"


async def _verify_hmac(
    request: Request,
    x_api_key: str,
    x_timestamp: str,
    x_signature: str,
) -> CallerContext:
    """
    HMAC path (§3.3) — ERP ingress.

    Preserves the existing behaviour: timestamp window, api_key lookup,
    signature verification over the raw body cached by BodyCacheMiddleware.
    """
    body = getattr(request.state, "raw_body", None)
    if body is None:
        body = await request.body()

    api_key_secrets: Dict[str, str] = request.app.state.api_key_secrets
    trace_id = getattr(request.state, "trace_id", "") or ""

    hmac_authenticate(
        api_key=x_api_key,
        timestamp=x_timestamp,
        signature=x_signature,
        body=body,
        api_key_secrets=api_key_secrets,
        trace_id=trace_id,
    )

    tenant_id = _tenant_id_for_api_key(request, x_api_key)
    return CallerContext(
        actor_type="erp",
        tenant_id=tenant_id,
        identifier=x_api_key,
        permissions=["blob.write", "dedup.check", "audit.log", "metrics.report"],
        source_id=request.headers.get("x-source-id"),
        trace_id=trace_id,
        # Downstream HB calls on behalf of an ERP use Relay's own HB service
        # credentials (the ERP doesn't have HB-service credentials — it only
        # has the Relay ingest HMAC key).
        downstream_auth_header=_relay_service_auth_header(request),
        raw_api_key=x_api_key,
    )


def _relay_service_auth_header(request: Request) -> str:
    """Build Relay's own Bearer service-creds header for downstream HB calls."""
    cfg: RelayConfig = request.app.state.config
    if cfg.heartbeat_api_key and cfg.heartbeat_api_secret:
        return f"Bearer {cfg.heartbeat_api_key}:{cfg.heartbeat_api_secret}"
    return ""


def _peek_jwt_aud(token: str) -> Optional[str]:
    """
    Decode the JWT payload WITHOUT verifying the signature and return the
    ``aud`` claim as a string (first element if a list), or ``None``.

    Used only to route the token to the correct validator — actual
    signature + claims verification happens downstream.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(payload_bytes.decode("utf-8"))
        aud = payload.get("aud")
        if isinstance(aud, list):
            return aud[0] if aud else None
        return aud if isinstance(aud, str) else None
    except Exception:
        return None


async def _verify_oauth_jwt(
    request: Request,
    jwt_token: str,
) -> CallerContext:
    """
    OAuth path (Q37 Gap #2) — local JWKS validation for ``aud=helium.relay-ingest``.

    Validates the token locally using :class:`OAuthTokenValidator` +
    :class:`JWKSCache`, checks the jti blocklist via Redis, then builds a
    ``CallerContext`` from the ``tenant_id`` claim in the JWT payload.

    Raises :class:`JWTValidationError` (→ HTTP 401) on any failure.
    Raises :class:`AuthUpstreamUnavailableError` if the OAuth validator
    has not been configured on ``app.state`` (config guard).
    """
    oauth_validator: Optional[OAuthTokenValidator] = getattr(
        request.app.state, "oauth_validator", None
    )
    if oauth_validator is None:
        # Shouldn't reach here (caller checks jwks_url first), but guard defensively.
        raise AuthUpstreamUnavailableError(
            "OAuth validator not configured on Relay (RELAY_JWKS_URL not set)"
        )

    redis_client = getattr(request.app.state, "redis", None)
    trace_id = getattr(request.state, "trace_id", "") or ""

    claims = await oauth_validator.validate(jwt_token, redis_client=redis_client)

    tenant_id = claims.get("tenant_id") or "_unknown"
    sub = claims.get("sub") or ""

    return CallerContext(
        actor_type="erp",
        tenant_id=tenant_id,
        identifier=sub,
        permissions=["blob.write", "dedup.check", "audit.log", "metrics.report"],
        source_id=request.headers.get("x-source-id"),
        trace_id=trace_id,
        downstream_auth_header=_relay_service_auth_header(request),
        raw_api_key="",
    )


async def _verify_service_creds(
    request: Request,
    api_key: str,
    api_secret: str,
) -> CallerContext:
    """
    Service-to-service path (§3.2) — Bearer <api_key>:<api_secret>.

    Currently only the Relay→HB/Core direction is active in production;
    nothing calls INTO Relay with service creds yet. This path is ready
    for when Core or a peer service needs a Relay endpoint. It rejects
    until a service credential store is wired on the Relay side.
    """
    # Minimal dev-tier support: accept any pair that matches a key in
    # api_key_secrets, treating it like an HMAC api_key without the signature
    # (used by internal dev tooling only). Refuse to accept credentials we
    # don't recognise — no permissive fallback.
    api_key_secrets: Dict[str, str] = request.app.state.api_key_secrets
    expected = api_key_secrets.get(api_key)
    if expected is None or not hmac.compare_digest(expected, api_secret):
        logger.warning(
            f"Service credentials rejected — api_key={api_key[:8]}...",
            extra={"trace_id": getattr(request.state, "trace_id", "")},
        )
        raise InvalidAPIKeyError()

    tenant_id = _tenant_id_for_api_key(request, api_key)
    trace_id = getattr(request.state, "trace_id", "") or ""
    return CallerContext(
        actor_type="service",
        tenant_id=tenant_id,
        identifier=api_key,
        permissions=["*"],  # service creds are platform-admin by convention
        source_id=None,
        trace_id=trace_id,
        downstream_auth_header=f"Bearer {api_key}:{api_secret}",
        raw_api_key=api_key,
    )


async def _verify_user_jwt(
    request: Request,
    jwt_token: str,
    bypass_cache: bool = False,
) -> CallerContext:
    """
    User JWT path (§3.1) — introspect against HeartBeat.

    Never validates locally (§5.1). If HeartBeat is unreachable, raises
    AuthUpstreamUnavailableError → 502 (fail closed per §2.4).

    ``bypass_cache`` is plumbed from the ``X-Bypass-Auth-Cache: true``
    request header by :func:`authenticate_request` (CSSV1 S1 chip 2/2).
    The header is a hatch for callers who need fresh state on a
    sensitive operation (e.g., post-step-up flow) — not a general
    permission boundary.
    """
    introspect_client = getattr(request.app.state, "introspect_client", None)
    if introspect_client is None:
        raise AuthUpstreamUnavailableError(
            "Introspect client not configured on Relay"
        )

    trace_id = getattr(request.state, "trace_id", "") or ""
    try:
        result = await introspect_client.introspect(
            jwt_token=jwt_token,
            trace_id=trace_id,
            bypass_cache=bypass_cache,
        )
    except HeartBeatUnavailableError as e:
        raise AuthUpstreamUnavailableError(str(e)) from e
    # JWTRejectedError bubbles up — status_code + error_code already set

    # result.tenant_id may be missing on some token shapes — fall back to _unknown
    tenant_id = result.tenant_id or "_unknown"

    # For downstream HB calls on behalf of this user, forward the JWT itself.
    # HB will re-verify by signature + session state on each call; this keeps
    # audit log entries attributable to the user, not to Relay.
    return CallerContext(
        actor_type="user",
        tenant_id=tenant_id,
        identifier=result.user_id or "",
        permissions=result.permissions,
        source_id=request.headers.get("x-source-id"),
        trace_id=trace_id,
        downstream_auth_header=f"Bearer {jwt_token}",
        raw_api_key="",  # user-path downstream calls don't carry an api_key
    )


async def authenticate_request(request: Request) -> CallerContext:
    """
    Combined auth dispatcher per BACKEND_SERVICE_AUTH_AND_ABUSE_SPEC.md §3.5.

    Tries in order:
        1. HMAC headers present → ERP path (§3.3)
        2. Bearer <k>:<s>       → service-to-service (§3.2)
        3. Bearer <jwt>         → user JWT via introspect (§3.1)

    Returns CallerContext on success. Raises a mapped Relay error on failure.

    Ensures request.state.trace_id is set (generates one if needed).
    """
    # Ensure trace_id is available even if TraceIDMiddleware hasn't populated
    # it yet for some reason.
    if not getattr(request.state, "trace_id", None):
        request.state.trace_id = str(uuid7())

    headers = request.headers

    # Path A: HMAC (check all three headers present)
    x_api_key = headers.get("x-api-key")
    x_timestamp = headers.get("x-timestamp")
    x_signature = headers.get("x-signature")
    if x_api_key and x_timestamp and x_signature:
        return await _verify_hmac(request, x_api_key, x_timestamp, x_signature)

    # Paths B / C: Authorization: Bearer ...
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if not token:
            raise AuthenticationFailedError("Empty Bearer token")
        # service creds = "api_key:api_secret" (single colon, no dots like JWT)
        if ":" in token and "." not in token and len(token.split(":")) == 2:
            api_key, api_secret = token.split(":", 1)
            return await _verify_service_creds(request, api_key, api_secret)
        # JWT otherwise (compact JWS has two dots).
        # Q37 Gap #2 — peek the aud claim (no verify) to route to the
        # correct validator:
        #   aud=helium.relay-ingest → OAuthTokenValidator (local JWKS)
        #   anything else           → introspect against HeartBeat (existing path)
        cfg: RelayConfig = request.app.state.config
        if (
            cfg.jwks_url
            and _peek_jwt_aud(token) == OAuthTokenValidator.REQUIRED_AUD
        ):
            return await _verify_oauth_jwt(request, token)

        # CSSV1 S1 chip 2/2 — ``X-Bypass-Auth-Cache: true`` (case-insensitive)
        # forces the introspect call to skip the per-jti cache. Any other
        # value (absent, "false", anything else) means cache normally.
        bypass_cache = (
            headers.get("x-bypass-auth-cache", "").strip().lower() == "true"
        )
        return await _verify_user_jwt(request, token, bypass_cache=bypass_cache)

    # Nothing recognisable
    logger.info(
        "Request arrived without credentials",
        extra={"trace_id": getattr(request.state, "trace_id", "")},
    )
    raise AuthenticationFailedError(
        "No credentials presented. Expected either HMAC headers "
        "(X-API-Key/X-Signature/X-Timestamp) or Authorization: Bearer <token>."
    )


# ── Body handling (unchanged from original deps.py) ──────────────────────

async def decrypt_body_if_needed(
    request: Request,
    x_encrypted: str = Header(default="false", description="Set to 'true' if request body is NaCl-encrypted"),
) -> bytes:
    """
    Decrypt request body if X-Encrypted: true.

    For remote requests with require_encryption=true, rejects unencrypted.
    For local requests, passes through.
    """
    body = await request.body()
    config: RelayConfig = request.app.state.config
    is_encrypted = x_encrypted.lower() == "true"

    if is_encrypted:
        relay_private_key = request.app.state.envelope
        if relay_private_key is None:
            raise EncryptionRequiredError()
        return nacl_decrypt(body, relay_private_key)

    # Not encrypted — check if encryption is required
    if config.require_encryption:
        raise EncryptionRequiredError()

    return body


def verify_internal_token(
    request: Request,
    authorization: str = Header(..., description="Bearer token for internal service auth (HeartBeat -> Relay)"),
) -> None:
    """
    Verify Bearer token for /internal/ endpoints.

    HeartBeat calls /internal/refresh-cache with a pre-shared service token.
    Uses constant-time comparison to prevent timing attacks.
    """
    config: RelayConfig = request.app.state.config
    expected = config.internal_service_token

    if not expected:
        raise AuthenticationFailedError("Internal service token not configured")

    # Expect "Bearer <token>"
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationFailedError("Invalid Authorization header format")

    token = parts[1]
    if not hmac.compare_digest(token, expected):
        raise AuthenticationFailedError("Invalid service token")
