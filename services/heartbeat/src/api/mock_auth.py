"""
Mock Auth Router — Demo-only authentication endpoints.

Activated by HEARTBEAT_MOCK_AUTH=true environment variable.
Provides hardcoded auth responses for Abbey Mortgage demo.

User: Charles Omoakin (Owner)
Email: Charles.Omoakin@abbeymortgagebank.com
First-time password: 123456
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth-mock"])

# ── Mock User State ──────────────────────────────────────────────────────────

CHARLES = {
    "user_id": "usr-abbey-owner-001",
    "email": "Charles.Omoakin@abbeymortgagebank.com",
    "display_name": "Charles Omoakin",
    "role": "Owner",
    "tenant_id": "tenant-abbey-001",
    "permissions": ["*"],
    "permissions_version": 1,
}

# Mutable state (in-memory, resets on restart)
_state = {
    "password": "123456",          # First-time password
    "is_first_run": True,          # Forces password change on first login
    "master_secret": secrets.token_hex(32),
    "session_id": None,
    "jwt_jti": None,
}


# ── Request/Response Models ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="Plaintext password")


class UserInfo(BaseModel):
    user_id: str
    role: str
    display_name: str
    tenant_id: str
    is_first_run: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    cipher_text: str
    expires_at: str
    session_expires_at: str
    user: UserInfo


class ChangePasswordRequest(BaseModel):
    new_password: str
    current_password: Optional[str] = None


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    cipher_text: str
    expires_at: str
    session_expires_at: str


class IntrospectRequest(BaseModel):
    token: str
    required_permission: Optional[str] = None
    required_within_seconds: Optional[int] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_jwt(scope: Optional[str] = None) -> str:
    """Generate a mock JWT-like token (not cryptographically signed)."""
    jti = f"tok-{uuid.uuid4().hex[:12]}"
    _state["jwt_jti"] = jti
    now = datetime.now(timezone.utc)
    # Return a base64-ish looking string that Float can store
    import base64, json
    header = base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload_data = {
        "sub": CHARLES["user_id"],
        "tenant_id": CHARLES["tenant_id"],
        "role": CHARLES["role"],
        "permissions": CHARLES["permissions"],
        "permissions_version": CHARLES["permissions_version"],
        "last_auth_at": now.isoformat(),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "session_expires_at": (now + timedelta(hours=8)).isoformat(),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
        "iss": "helium-heartbeat",
    }
    if scope:
        payload_data["scope"] = scope
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return f"{header}.{payload}.{sig}"


def _derive_cipher_text() -> str:
    """Derive cipher_text from master_secret (mock — time-windowed HMAC)."""
    window = str(int(time.time()) // 540)
    return hmac.new(
        _state["master_secret"].encode(),
        window.encode(),
        hashlib.sha256
    ).hexdigest()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def mock_login(body: LoginRequest):
    """
    Mock login for Abbey demo.

    Accepts Charles.Omoakin@abbeymortgagebank.com with current password.
    If is_first_run=true, returns a bootstrap-scoped token that only allows
    password change.
    """
    if body.email.lower() != CHARLES["email"].lower():
        raise HTTPException(
            status_code=401,
            detail={"error_code": "TOKEN_INVALID", "message": f"Unknown user: {body.email}"}
        )

    if body.password != _state["password"]:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "TOKEN_INVALID", "message": "Invalid password"}
        )

    now = datetime.now(timezone.utc)
    _state["session_id"] = f"sess-{uuid.uuid4().hex[:12]}"

    scope = "bootstrap" if _state["is_first_run"] else None
    token = _mock_jwt(scope=scope)
    cipher_text = _derive_cipher_text()

    logger.info(
        f"Mock login: {CHARLES['display_name']} "
        f"(first_run={_state['is_first_run']}, scope={scope})"
    )

    return LoginResponse(
        access_token=token,
        cipher_text=cipher_text,
        expires_at=(now + timedelta(minutes=30)).isoformat(),
        session_expires_at=(now + timedelta(hours=8)).isoformat(),
        user=UserInfo(
            user_id=CHARLES["user_id"],
            role=CHARLES["role"],
            display_name=CHARLES["display_name"],
            tenant_id=CHARLES["tenant_id"],
            is_first_run=_state["is_first_run"],
        ),
    )


@router.post("/password/change")
async def mock_change_password(body: ChangePasswordRequest):
    """
    Mock password change.

    On first run (bootstrap): only new_password required.
    Normal mode: current_password must match.
    """
    if not _state["is_first_run"]:
        if not body.current_password or body.current_password != _state["password"]:
            raise HTTPException(
                status_code=401,
                detail={"error_code": "TOKEN_INVALID", "message": "Current password is incorrect"}
            )

    if len(body.new_password) < 6:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "WEAK_PASSWORD", "message": "Password must be at least 6 characters"}
        )

    _state["password"] = body.new_password
    _state["is_first_run"] = False

    logger.info(f"Mock password changed for {CHARLES['display_name']} (first_run cleared)")

    return {
        "status": "password_changed",
        "message": "Password updated successfully. Please log in again.",
        "is_first_run": False,
    }


@router.post("/token/refresh", response_model=RefreshResponse)
async def mock_refresh():
    """Mock token refresh — always succeeds, returns new token."""
    now = datetime.now(timezone.utc)
    token = _mock_jwt()
    cipher_text = _derive_cipher_text()

    return RefreshResponse(
        access_token=token,
        cipher_text=cipher_text,
        expires_at=(now + timedelta(minutes=30)).isoformat(),
        session_expires_at=(now + timedelta(hours=8)).isoformat(),
    )


@router.post("/logout")
async def mock_logout():
    """Mock logout — always succeeds."""
    _state["session_id"] = None
    _state["jwt_jti"] = None
    return {"status": "logged_out"}


@router.post("/introspect")
async def mock_introspect(body: IntrospectRequest):
    """
    Mock token introspection for service-to-service verification.
    Always returns active=true with Charles's permissions.
    """
    now = datetime.now(timezone.utc)
    return {
        "active": True,
        "actor_type": "human",
        "user_id": CHARLES["user_id"],
        "role": CHARLES["role"],
        "permissions": CHARLES["permissions"],
        "tenant_id": CHARLES["tenant_id"],
        "last_auth_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "session_expires_at": (now + timedelta(hours=8)).isoformat(),
        "step_up_satisfied": True,
    }


@router.post("/stepup")
async def mock_stepup():
    """Mock step-up auth — always succeeds."""
    now = datetime.now(timezone.utc)
    token = _mock_jwt()
    cipher_text = _derive_cipher_text()

    return {
        "access_token": token,
        "token_type": "bearer",
        "cipher_text": cipher_text,
        "last_auth_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
        "session_expires_at": (now + timedelta(hours=8)).isoformat(),
    }


@router.get("/operations/{operation}/policy")
async def mock_policy(operation: str):
    """Mock step-up policy — always returns 300s (5 min) freshness."""
    return {
        "operation": operation,
        "required_within_seconds": 300,
        "tier": "standard",
    }
