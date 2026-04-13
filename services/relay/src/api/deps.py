"""
FastAPI Dependency Injection

Dependencies for request authentication, decryption, and service access.
Auth + encryption handled here (not middleware) for cleaner header+body access.
"""

import hmac
import logging
from typing import Any, Dict

from fastapi import Depends, Header, Request

from ..config import RelayConfig
from ..core.auth import authenticate
from ..core.tenant import TenantConfig
from ..crypto.envelope import decrypt as nacl_decrypt
from ..errors import (
    AuthenticationFailedError,
    EncryptionRequiredError,
)

logger = logging.getLogger(__name__)


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


async def authenticate_request(
    request: Request,
    x_api_key: str = Header(..., description="API key for HMAC authentication"),
    x_timestamp: str = Header(..., description="ISO 8601 UTC timestamp (must be within 5 minutes)"),
    x_signature: str = Header(..., description="HMAC-SHA256 signature: sign(api_key:timestamp:sha256(body))"),
) -> TenantConfig:
    """
    Authenticate request via HMAC-SHA256 and resolve tenant.

    Returns the TenantConfig for the authenticated API key.
    """
    body = getattr(request.state, "raw_body", None)
    if body is None:
        body = await request.body()

    tenant_registry: Dict[str, TenantConfig] = request.app.state.tenant_registry
    trace_id = getattr(request.state, "trace_id", "")

    # Look up tenant by API key
    tenant = tenant_registry.get(x_api_key)
    if tenant is None:
        from ..errors import InvalidAPIKeyError
        raise InvalidAPIKeyError()

    # Build api_key_secrets for the auth function (backward compat)
    api_key_secrets = {tenant.api_key: tenant.api_secret}

    authenticate(
        api_key=x_api_key,
        timestamp=x_timestamp,
        signature=x_signature,
        body=body,
        api_key_secrets=api_key_secrets,
        trace_id=trace_id,
    )

    return tenant


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
