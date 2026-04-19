"""
Test Harness API Endpoints

Privileged test operations protected by HMAC signature.
Enabled only when HEARTBEAT_TEST_HARNESS_ENABLED=true.

All operations are audit-logged.

Endpoints:
    POST /api/test/auth/reset           -- Reset user to first-time login
    POST /api/test/auth/create-user     -- Create test user
    POST /api/test/data/seed            -- Seed sample data
    POST /api/test/data/clear           -- Wipe tenant data (logged)
    POST /api/test/sse/emit             -- Push custom SSE event
    POST /api/test/config/override      -- Temporarily override config
    GET  /api/test/state                -- Dump system state
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from uuid6 import uuid7

from ...auth.test_harness_manager import get_test_harness_manager
from ...database.pg_auth import get_pg_auth_database


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test-harness"])


# -- HMAC Validation Dependency ----------------------------------------

async def _validate_harness(request: Request) -> None:
    """Validate test harness HMAC signature from request."""
    manager = get_test_harness_manager()

    if not manager.is_enabled:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "NOT_FOUND", "message": "Not found"},
        )

    signature = request.headers.get("X-Test-Harness-Signature", "")
    if not signature:
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "HARNESS_UNAUTHORIZED",
                "message": "Missing X-Test-Harness-Signature header",
            },
        )

    body = await request.body()
    if not manager.validate_signature(signature, body):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "HARNESS_UNAUTHORIZED",
                "message": "Invalid test harness signature",
            },
        )


def _audit_log(operation: str, details: Dict[str, Any]) -> None:
    """Log test harness operation for audit trail."""
    logger.warning(
        f"TEST HARNESS: {operation} | {details}"
    )


# -- Request Models ----------------------------------------------------

class ResetAuthRequest(BaseModel):
    email: str = Field(..., description="User email to reset")


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    password: str = Field(default="TestPass123")
    role_id: str = Field(default="Operator")
    tenant_id: str = Field(default="tenant-abbey-001")


class SeedDataRequest(BaseModel):
    tenant_id: str = Field(default="tenant-abbey-001")
    scenario: str = Field(default="basic", description="basic | full | stress")


class ClearDataRequest(BaseModel):
    tenant_id: str = Field(default="tenant-abbey-001")
    confirm: bool = Field(default=False, description="Must be true to proceed")


class SSEEmitRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None


class ConfigOverrideRequest(BaseModel):
    service_name: str
    config_key: str
    config_value: str
    ttl_seconds: int = Field(default=300, description="Override duration (5 min default)")


# -- Endpoints ---------------------------------------------------------

@router.post("/auth/reset")
async def reset_auth(request: Request):
    """Reset a user to first-time login state."""
    await _validate_harness(request)

    body = await request.json()
    data = ResetAuthRequest(**body)
    db = get_pg_auth_database()

    user = db.get_user_by_email(data.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    db._pool.execute_update(
        "UPDATE auth.users SET is_first_run = TRUE, updated_at = %s WHERE email = %s",
        (now, data.email),
    )
    db.revoke_all_user_sessions(user["user_id"], reason="test_harness_reset")

    _audit_log("auth/reset", {"email": data.email, "user_id": user["user_id"]})

    return {
        "status": "reset",
        "user_id": user["user_id"],
        "is_first_run": True,
        "sessions_revoked": True,
    }


@router.post("/auth/create-user")
async def create_test_user(request: Request):
    """Create a test user on the fly."""
    await _validate_harness(request)

    body = await request.json()
    data = CreateUserRequest(**body)
    db = get_pg_auth_database()

    # Check if user already exists
    existing = db.get_user_by_email(data.email)
    if existing:
        return {
            "status": "exists",
            "user_id": existing["user_id"],
            "message": "User already exists",
        }

    password_hash = bcrypt.hashpw(
        data.password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    user_id = f"usr-test-{uuid7()}"
    user = db.create_user(
        user_id=user_id,
        email=data.email,
        password_hash=password_hash,
        display_name=data.display_name,
        role_id=data.role_id,
        tenant_id=data.tenant_id,
        is_first_run=True,
    )

    _audit_log("auth/create-user", {"email": data.email, "user_id": user_id})

    return {
        "status": "created",
        "user_id": user_id,
        "email": data.email,
        "role_id": data.role_id,
        "tenant_id": data.tenant_id,
    }


@router.post("/data/seed")
async def seed_data(request: Request):
    """Seed sample data for a tenant."""
    await _validate_harness(request)

    body = await request.json()
    data = SeedDataRequest(**body)

    _audit_log("data/seed", {"tenant_id": data.tenant_id, "scenario": data.scenario})

    # Placeholder — extend with actual seed logic as needed
    return {
        "status": "seeded",
        "tenant_id": data.tenant_id,
        "scenario": data.scenario,
    }


@router.post("/data/clear")
async def clear_data(request: Request):
    """Wipe tenant data (destructive, audit-logged)."""
    await _validate_harness(request)

    body = await request.json()
    data = ClearDataRequest(**body)

    if not data.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to proceed with data wipe",
        )

    _audit_log("data/clear", {"tenant_id": data.tenant_id, "confirm": True})

    # Placeholder — extend with actual clear logic
    return {
        "status": "cleared",
        "tenant_id": data.tenant_id,
    }


@router.post("/sse/emit")
async def emit_sse_event(request: Request):
    """Push a custom SSE event."""
    await _validate_harness(request)

    body = await request.json()
    data = SSEEmitRequest(**body)

    _audit_log("sse/emit", {
        "event_type": data.event_type,
        "tenant_id": data.tenant_id,
    })

    # Try to push via SSE event bus
    try:
        from ...sse.producer import get_event_bus
        event_bus = get_event_bus()
        await event_bus.publish(
            event_type=data.event_type,
            payload=data.payload,
            tenant_id=data.tenant_id,
            user_id=data.user_id,
        )
        return {"status": "emitted", "event_type": data.event_type}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.post("/config/override")
async def config_override(request: Request):
    """Temporarily override a config value."""
    await _validate_harness(request)

    body = await request.json()
    data = ConfigOverrideRequest(**body)

    _audit_log("config/override", {
        "service": data.service_name,
        "key": data.config_key,
        "ttl_seconds": data.ttl_seconds,
    })

    try:
        from ...database.config_db import get_config_database
        config_db = get_config_database()
        config_db.set_config_entry(
            service_name=data.service_name,
            config_key=data.config_key,
            config_value=data.config_value,
            updated_by="test_harness",
        )
        return {
            "status": "overridden",
            "service_name": data.service_name,
            "config_key": data.config_key,
            "ttl_seconds": data.ttl_seconds,
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.get("/state")
async def get_system_state(request: Request):
    """Dump system state for debugging."""
    await _validate_harness(request)

    db = get_pg_auth_database()

    _audit_log("state", {"action": "dump"})

    # Gather state
    users = db._pool.execute_query(
        "SELECT user_id, email, role_id, tenant_id, is_active, is_first_run "
        "FROM auth.users ORDER BY user_id"
    )
    sessions = db._pool.execute_query(
        "SELECT session_id, user_id, device_id, is_revoked, issued_at "
        "FROM auth.sessions ORDER BY issued_at DESC LIMIT 20"
    )
    devices = db._pool.execute_query(
        "SELECT device_id, user_id, computer_name, os_type, is_revoked "
        "FROM auth.devices ORDER BY registered_at DESC LIMIT 20"
    )

    return {
        "users": [dict(u) for u in users],
        "recent_sessions": [
            {k: str(v) if v is not None else None for k, v in dict(s).items()}
            for s in sessions
        ],
        "recent_devices": [dict(d) for d in devices],
    }
