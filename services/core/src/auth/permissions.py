"""
RBAC Permission Checking

Per WS4 MENTAL_MODEL \u00a79: Read endpoints are open, write endpoints
require entity-specific permissions. system.admin bypasses all checks.

JWT claims structure (per WS4_RESPONSES.md Q7):
    {
        "sub": "helium_user_123",
        "company_id": "tenant_001",
        "permissions": ["invoice.update", "customer.update"],
        "exp": ...,
        "iat": ...,
    }
"""

from __future__ import annotations

from fastapi import Request

from src.errors import CoreError, CoreErrorCode


def check_permission(request: Request, required_permission: str | None) -> None:
    """
    Check that the request's JWT claims include the required permission.

    Args:
        request: FastAPI request with jwt_claims on state.
        required_permission: Permission string (e.g. 'invoice.update').
            None means the endpoint is open \u2014 no check performed.

    Raises:
        CoreError(FORBIDDEN): If permission is missing.
    """
    if required_permission is None:
        return

    claims = getattr(request.state, "jwt_claims", {})
    permissions = claims.get("permissions", [])

    if "system.admin" in permissions:
        return

    if required_permission not in permissions:
        raise CoreError(
            error_code=CoreErrorCode.FORBIDDEN,
            message=f"Missing permission: {required_permission}",
        )


def get_user_id(request: Request) -> str:
    """Extract helium_user_id from JWT claims."""
    claims = getattr(request.state, "jwt_claims", {})
    return claims.get("sub", "unknown")
