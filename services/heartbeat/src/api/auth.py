"""
Auth API Router -- Login, Refresh, Logout, Introspect, Step-Up, Change Password

Aligned with AUTH_SERVICE_CONTRACT.md (March 2026).

Endpoints:
    POST /api/auth/login                       -- Local credential login
    POST /api/auth/token/refresh               -- Refresh session token
    POST /api/auth/logout                      -- Revoke session
    POST /api/auth/introspect                  -- Service-to-service verify
    POST /api/auth/stepup                      -- Step-up re-authentication
    GET  /api/auth/operations/{op}/policy      -- Step-up policy query
    POST /api/auth/password/change             -- Change password
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.dependencies import (
    get_current_user_token,
    verify_service_credentials,
)
from ..errors import HeartBeatError
from ..handlers.auth_handler import (
    change_password,
    get_operation_policy,
    introspect_token,
    login,
    logout,
    refresh_token,
    step_up_auth,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# -- Request / Response Models -----------------------------------------


class LoginRequest(BaseModel):
    """POST /api/auth/login request body."""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="Plaintext password")


class UserInfo(BaseModel):
    """User info nested in login response."""
    user_id: str
    role: str
    display_name: str
    tenant_id: str
    is_first_run: bool


class LoginResponse(BaseModel):
    """POST /api/auth/login response body."""
    access_token: str
    token_type: str = "bearer"
    cipher_text: str = Field(
        ..., description="Hex-encoded cipher text for SQLCipher key derivation"
    )
    expires_at: str
    session_expires_at: str
    user: UserInfo


class RefreshResponse(BaseModel):
    """POST /api/auth/token/refresh response body."""
    access_token: str
    expires_at: str
    session_expires_at: str
    last_auth_at: str


class LogoutResponse(BaseModel):
    """POST /api/auth/logout response body."""
    status: str


class IntrospectRequest(BaseModel):
    """POST /api/auth/introspect request body."""
    token: str = Field(..., description="User JWT to introspect")
    required_permission: Optional[str] = Field(
        None, description="Optional permission to check"
    )
    required_within_seconds: Optional[int] = Field(
        None, description="Optional step-up freshness window (seconds)"
    )


class IntrospectResponse(BaseModel):
    """POST /api/auth/introspect response body."""
    active: bool
    actor_type: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    permissions: Optional[List[str]] = None
    tenant_id: Optional[str] = None
    last_auth_at: Optional[str] = None
    expires_at: Optional[str] = None
    session_expires_at: Optional[str] = None
    step_up_satisfied: Optional[bool] = None
    required_within_seconds: Optional[int] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


class StepUpRequest(BaseModel):
    """POST /api/auth/stepup request body."""
    password: str = Field(..., description="User password for re-authentication")


class StepUpResponse(BaseModel):
    """POST /api/auth/stepup response body."""
    access_token: str
    cipher_text: str = Field(
        ..., description="Fresh cipher text after re-auth"
    )
    expires_at: str
    session_expires_at: str
    last_auth_at: str


class OperationPolicyResponse(BaseModel):
    """GET /api/auth/operations/{op}/policy response body."""
    operation: str
    required_within_seconds: int
    tier: str


class ChangePasswordRequest(BaseModel):
    """POST /api/auth/password/change request body."""
    new_password: str = Field(..., description="The new password to set")
    current_password: Optional[str] = Field(
        None,
        description="Current password (required for non-bootstrap changes)",
    )


class ChangePasswordResponse(BaseModel):
    """POST /api/auth/password/change response body."""
    status: str


# -- Endpoints ---------------------------------------------------------


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"description": "Invalid credentials"},
        409: {"description": "Concurrent session limit exceeded"},
    },
)
async def login_endpoint(body: LoginRequest):
    """
    Authenticate user with local credentials.

    AUTH_SERVICE_CONTRACT Section 4: POST /api/auth/login

    Verifies email + bcrypt password, issues Ed25519 JWT.
    Returns cipher_text for SQLCipher key derivation (zero-latency access).
    First-run users get a restricted bootstrap token.
    Session has an 8-hour hard cap.
    Enforces concurrent session limits per tenant.
    """
    try:
        result = await login(body.email, body.password)
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/token/refresh",
    response_model=RefreshResponse,
    responses={
        401: {"description": "Token expired, session cap reached, or permissions changed"},
    },
)
async def refresh_endpoint(
    token: str = Depends(get_current_user_token),
):
    """
    Refresh session token.

    AUTH_SERVICE_CONTRACT Section 5: POST /api/auth/token/refresh

    Issues a new short-lived JWT (30 min); old JWT becomes invalid.
    Does NOT extend the session (session_expires_at is immutable).
    Returns SESSION_EXPIRED if the 8-hour cap has been reached.
    Returns PERMISSIONS_CHANGED if user permissions were modified.
    """
    try:
        result = await refresh_token(token)
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={
        401: {"description": "Token invalid"},
    },
)
async def logout_endpoint(
    token: str = Depends(get_current_user_token),
):
    """
    Revoke current session.

    AUTH_SERVICE_CONTRACT Section 9: POST /api/auth/logout

    Marks the session as revoked. Even expired tokens can be
    used to identify and revoke the session.
    """
    try:
        result = await logout(token)
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/introspect",
    response_model=IntrospectResponse,
    responses={
        401: {"description": "Invalid service credentials"},
    },
)
async def introspect_endpoint(
    body: IntrospectRequest,
    service_cred: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Verify a user JWT (service-to-service).

    AUTH_SERVICE_CONTRACT Section 7: POST /api/auth/introspect

    Called by downstream services (Relay, Core) to verify user JWTs.
    Requires service-level Bearer api_key:api_secret credentials.
    Includes step-up freshness check when required_within_seconds
    is provided.
    """
    result = await introspect_token(
        token=body.token,
        required_permission=body.required_permission,
        required_within_seconds=body.required_within_seconds,
    )
    return result


@router.post(
    "/stepup",
    response_model=StepUpResponse,
    responses={
        401: {"description": "Invalid token or wrong password"},
    },
)
async def stepup_endpoint(
    body: StepUpRequest,
    token: str = Depends(get_current_user_token),
):
    """
    Step-up re-authentication.

    AUTH_SERVICE_CONTRACT Section 6: POST /api/auth/stepup

    User provides their password to satisfy a step-up requirement.
    Returns a new JWT with fresh last_auth_at and cipher_text.
    Re-auth naturally resets both last_auth_at and last_PIN_at.
    """
    try:
        result = await step_up_auth(token, body.password)
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get(
    "/operations/{operation}/policy",
    response_model=OperationPolicyResponse,
    responses={
        401: {"description": "Invalid service credentials"},
    },
)
async def operation_policy_endpoint(
    operation: str,
    service_cred: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Get step-up policy for an operation.

    AUTH_SERVICE_CONTRACT Section 6.5: GET /api/auth/operations/{op}/policy

    Services (Core, Relay) call this to learn what step-up tier
    an operation requires. Cache for 5 minutes.
    """
    result = await get_operation_policy(operation)
    return result


@router.post(
    "/password/change",
    response_model=ChangePasswordResponse,
    responses={
        400: {"description": "Weak password, recycled password, or wrong current password"},
        401: {"description": "Token invalid or expired"},
    },
)
async def change_password_endpoint(
    body: ChangePasswordRequest,
    token: str = Depends(get_current_user_token),
):
    """
    Change the authenticated user's password.

    AUTH_SERVICE_CONTRACT Section 10: POST /api/auth/password/change

    Bootstrap mode (first-run): current_password not required.
    Normal mode: current_password must match.
    Revokes all sessions after successful change.
    """
    try:
        result = await change_password(
            token=token,
            new_password=body.new_password,
            current_password=body.current_password,
        )
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
