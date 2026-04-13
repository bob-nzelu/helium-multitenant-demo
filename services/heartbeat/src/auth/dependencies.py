"""
FastAPI Dependencies for Auth

Reusable Depends() functions for extracting and validating
credentials from request headers.

- verify_service_credentials: Validates service Bearer key:secret
  against registry.db credentials. Used by introspect endpoint.

- get_current_user_token: Extracts and returns the raw JWT string
  from the Authorization header. Used by refresh/logout endpoints.

- get_optional_user_token: Same as get_current_user_token but returns
  None instead of raising 401 when absent. Used by blob endpoints
  where JWT is optional (machine-to-machine uploads have no user JWT).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from ..handlers.credential_handler import validate_api_key


logger = logging.getLogger(__name__)


async def verify_service_credentials(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency: validate service-level Bearer key:secret.

    Parses the Authorization header as "Bearer {api_key}:{api_secret}",
    splits on the first colon, and calls the existing
    credential_handler.validate_api_key() against registry.db.

    Returns the validated credential dict on success.
    Raises HTTPException(401) on failure.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "Missing or invalid Authorization header. "
                           "Expected: Bearer {api_key}:{api_secret}",
            },
        )

    bearer_value = auth_header[7:]  # Strip "Bearer "

    # Split on first colon
    if ":" not in bearer_value:
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "Invalid credential format. "
                           "Expected: Bearer {api_key}:{api_secret}",
            },
        )

    api_key, api_secret = bearer_value.split(":", 1)

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "API key and secret must not be empty",
            },
        )

    try:
        credential = await validate_api_key(api_key, api_secret)
        return credential
    except ValueError as e:
        logger.warning(f"Service credential validation failed: {e}")
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "Invalid service credentials",
            },
        )


async def get_current_user_token(request: Request) -> str:
    """
    FastAPI dependency: extract raw JWT from Authorization header.

    Expects "Bearer {jwt}" (user session JWT, not key:secret).
    Returns the raw JWT string for further processing.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "Missing or invalid Authorization header. "
                           "Expected: Bearer {jwt}",
            },
        )

    token = auth_header[7:]  # Strip "Bearer "

    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "TOKEN_INVALID",
                "message": "Bearer token is empty",
            },
        )

    return token


async def get_optional_user_token(request: Request) -> Optional[str]:
    """
    FastAPI dependency: extract JWT from Authorization header (optional).

    Returns the raw JWT string if present, or None if the Authorization
    header is missing or empty. Unlike get_current_user_token, this
    NEVER raises 401 — it's for endpoints where JWT is optional
    (e.g., blob write supports both human-JWT and machine-to-machine).

    Returns:
        Raw JWT string, or None if no Bearer token provided.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Strip "Bearer "
    return token if token else None
