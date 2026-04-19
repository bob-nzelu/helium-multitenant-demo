"""
Auth API Router -- Login, Refresh, Logout, Introspect, Step-Up, Change Password,
                   Device Registration, App Registration, Session Management

Aligned with AUTH_SERVICE_CONTRACT.md + UNIFIED_AUTH_CONTRACT.md.

Endpoints:
    POST /api/auth/login                       -- Local credential login
    POST /api/auth/token/refresh               -- Refresh session token
    POST /api/auth/refresh                     -- Refresh alias (Reader compat)
    POST /api/auth/logout                      -- Revoke session
    POST /api/auth/introspect                  -- Service-to-service verify
    POST /api/auth/stepup                      -- Step-up re-authentication
    GET  /api/auth/operations/{op}/policy      -- Step-up policy query
    POST /api/auth/password/change             -- Change password
    POST /api/auth/register-device             -- Register a machine
    POST /api/auth/register-app                -- Register a frontend app instance
    GET  /api/auth/devices                     -- List user devices
    POST /api/auth/devices/{id}/revoke         -- Revoke a device
    GET  /api/auth/sessions                    -- List active sessions
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
    device_id: Optional[str] = Field(None, description="Device identifier")


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
    device_id: Optional[str] = None


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
    device_id: Optional[str] = None
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
        result = await login(body.email, body.password, device_id=body.device_id)
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


# -- Refresh Alias (Reader compatibility) --------------------------------

@router.post(
    "/refresh",
    response_model=RefreshResponse,
    responses={
        401: {"description": "Token expired or invalid"},
    },
)
async def refresh_alias_endpoint(
    token: str = Depends(get_current_user_token),
):
    """
    Refresh session token (alias for /token/refresh).

    Reader calls POST /api/auth/refresh, Float calls POST /api/auth/token/refresh.
    This alias ensures both paths work.
    """
    try:
        result = await refresh_token(token)
        return result
    except HeartBeatError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# -- Device Registration ------------------------------------------------

class RegisterDeviceRequest(BaseModel):
    """POST /api/auth/register-device request body."""
    device_id: str = Field(..., description="SHA256(machine_guid:mac)[:16]")
    machine_guid: str = Field(..., description="OS-specific machine identifier")
    mac_address: Optional[str] = Field(None, description="Primary MAC address")
    computer_name: Optional[str] = Field(None, description="Hostname")
    os_type: str = Field(..., description="windows | macos | linux | ios | android")
    os_version: Optional[str] = Field(None, description="OS version string")
    app_type: Optional[str] = Field(None, description="float | transforma_reader | ...")
    app_version: Optional[str] = Field(None, description="App version string")


@router.post("/register-device")
async def register_device_endpoint(
    body: RegisterDeviceRequest,
    token: str = Depends(get_current_user_token),
):
    """Register or update a device with HeartBeat."""
    from ..auth.jwt_manager import get_jwt_manager
    from ..database.pg_auth import get_pg_auth_database

    jwt_mgr = get_jwt_manager()
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"error_code": "TOKEN_INVALID", "message": "Invalid token"})

    db = get_pg_auth_database()
    device = db.register_device(
        device_id=body.device_id,
        machine_guid=body.machine_guid,
        os_type=body.os_type,
        mac_address=body.mac_address,
        computer_name=body.computer_name,
        os_version=body.os_version,
        app_type=body.app_type,
        app_version=body.app_version,
        user_id=claims.get("sub"),
    )

    return {
        "device_id": device["device_id"],
        "status": "registered",
        "registered_at": str(device["registered_at"]),
    }


# -- Device + Session Management ----------------------------------------

@router.get("/devices")
async def list_devices_endpoint(
    token: str = Depends(get_current_user_token),
):
    """List registered devices for the current user."""
    from ..auth.jwt_manager import get_jwt_manager
    from ..database.pg_auth import get_pg_auth_database

    jwt_mgr = get_jwt_manager()
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"error_code": "TOKEN_INVALID", "message": "Invalid token"})

    db = get_pg_auth_database()
    devices = db.list_user_devices(claims["sub"])

    return {
        "devices": [
            {
                "device_id": d["device_id"],
                "computer_name": d.get("computer_name"),
                "os_type": d.get("os_type"),
                "os_version": d.get("os_version"),
                "last_app_type": d.get("last_app_type"),
                "last_seen_at": str(d["last_seen_at"]) if d.get("last_seen_at") else None,
                "registered_at": str(d["registered_at"]),
                "is_revoked": d["is_revoked"],
            }
            for d in devices
        ]
    }


@router.post("/devices/{device_id}/revoke")
async def revoke_device_endpoint(
    device_id: str,
    token: str = Depends(get_current_user_token),
):
    """Revoke a device and all its sessions (admin)."""
    from ..auth.jwt_manager import get_jwt_manager
    from ..database.pg_auth import get_pg_auth_database

    jwt_mgr = get_jwt_manager()
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"error_code": "TOKEN_INVALID", "message": "Invalid token"})

    db = get_pg_auth_database()
    sessions_revoked = db.revoke_device(device_id, revoked_by=claims["sub"])

    return {
        "status": "revoked",
        "device_id": device_id,
        "sessions_revoked": sessions_revoked,
    }


@router.get("/sessions")
async def list_sessions_endpoint(
    token: str = Depends(get_current_user_token),
):
    """List active sessions for the current user."""
    from ..auth.jwt_manager import get_jwt_manager
    from ..database.pg_auth import get_pg_auth_database

    jwt_mgr = get_jwt_manager()
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"error_code": "TOKEN_INVALID", "message": "Invalid token"})

    db = get_pg_auth_database()
    sessions = db.get_active_sessions_for_user(claims["sub"])

    return {
        "sessions": [
            {
                "session_id": s["session_id"],
                "device_id": s.get("device_id"),
                "issued_at": str(s["issued_at"]),
                "expires_at": str(s["expires_at"]),
                "session_expires_at": str(s.get("session_expires_at", "")),
                "last_auth_at": str(s["last_auth_at"]),
            }
            for s in sessions
        ]
    }


# -- App Registration ---------------------------------------------------

class RegisterAppRequest(BaseModel):
    """POST /api/auth/register-app request body."""
    source_type: str = Field(..., description="float | transforma_reader | transforma_reader_mobile | monitoring")
    source_name: str = Field(..., description="{AppType}_{computer_name}")
    app_version: str = Field(..., description="App version string")
    machine_guid: Optional[str] = Field(None, description="OS machine identifier")
    mac_address: Optional[str] = Field(None, description="Primary MAC address")
    computer_name: Optional[str] = Field(None, description="Hostname")
    os_type: Optional[str] = Field(None, description="windows | macos | linux")
    os_version: Optional[str] = Field(None, description="OS version string")
    device_id: str = Field(..., description="SHA256(machine_guid:mac)[:16]")


@router.post("/register-app")
async def register_app_endpoint(
    body: RegisterAppRequest,
    token: str = Depends(get_current_user_token),
):
    """
    Register a frontend app instance with HeartBeat.

    Returns source_id + tenant config bundle (endpoints, capabilities,
    feature flags, security settings).

    Idempotent: same device_id + source_type returns existing registration.
    """
    from ..auth.jwt_manager import get_jwt_manager
    from ..handlers.registration_handler import register_app

    jwt_mgr = get_jwt_manager()
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"error_code": "TOKEN_INVALID", "message": "Invalid token"})

    result = await register_app(
        user_id=claims["sub"],
        tenant_id=claims["tenant_id"],
        source_type=body.source_type,
        source_name=body.source_name,
        device_id=body.device_id,
        app_version=body.app_version,
        machine_guid=body.machine_guid,
        mac_address=body.mac_address,
        computer_name=body.computer_name,
        os_type=body.os_type,
        os_version=body.os_version,
    )

    return result
