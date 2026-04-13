"""
JWT Validation for Core Service

Validates EdDSA (Ed25519) JWTs issued by HeartBeat.
Follows the same algorithm as Float's auth_provider.py.

Per SSE_SPEC Section 2.2:
- Validate JWT signature and expiry.
- Extract sub (user ID), role, permissions, and company_id from claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
import structlog

logger = structlog.get_logger()


class JWTError(Exception):
    """JWT validation failed."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class JWTClaims:
    """Validated JWT claims extracted from a token."""

    sub: str
    company_id: str
    role: str
    permissions: list[str]
    exp: int
    raw: dict[str, Any]


def validate_jwt(
    token: str,
    public_key: str,
    algorithm: str = "EdDSA",
) -> JWTClaims:
    """
    Validate a JWT and extract claims.

    Args:
        token: Raw JWT string (without "Bearer " prefix).
        public_key: Ed25519 PEM public key or HS256 secret.
        algorithm: JWT algorithm (EdDSA or HS256).

    Returns:
        JWTClaims with validated fields.

    Raises:
        JWTError: TOKEN_INVALID for bad signature/format,
                  TOKEN_EXPIRED for expired tokens.
    """
    if not token:
        raise JWTError("TOKEN_INVALID", "No token provided")

    if not public_key:
        raise JWTError("TOKEN_INVALID", "JWT validation not configured")

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise JWTError("TOKEN_EXPIRED", "Token has expired")
    except jwt.InvalidTokenError as e:
        # Log detail server-side, return generic message to caller
        logger.warning("jwt_validation_failed", error=str(e))
        raise JWTError("TOKEN_INVALID", "Token validation failed")

    # Extract required claims
    sub = payload.get("sub", "")
    company_id = payload.get("company_id") or payload.get("tenant_id", "")
    role = payload.get("role", "")
    permissions = payload.get("permissions") or payload.get("scopes", [])
    exp = payload.get("exp", 0)

    if not company_id:
        raise JWTError("TOKEN_INVALID", "Token missing company_id claim")

    return JWTClaims(
        sub=sub,
        company_id=company_id,
        role=role,
        permissions=permissions,
        exp=exp,
        raw=payload,
    )


def extract_bearer_token(authorization: str | None) -> str:
    """
    Extract token from Authorization header value.

    Args:
        authorization: Full header value (e.g., "Bearer eyJ...")

    Returns:
        The raw JWT string.

    Raises:
        JWTError: If header is missing or malformed.
    """
    if not authorization:
        raise JWTError("TOKEN_INVALID", "Missing Authorization header")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise JWTError("TOKEN_INVALID", "Authorization header must be: Bearer <token>")

    return parts[1]
