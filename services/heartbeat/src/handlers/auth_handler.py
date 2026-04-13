"""
Auth Handler -- Login, Refresh, Logout, Introspect, Step-Up, Change Password

Business logic for HeartBeat authentication operations.
Aligned with AUTH_SERVICE_CONTRACT.md (March 2026).

Session model:
    - JWT expires every 30 min (configurable), refreshes silently
    - Session has an 8-hour hard cap from login time
    - After 8 hours, user MUST re-authenticate (password)
    - Permission changes force re-auth (revoke all sessions for user)
    - PIN is a Float/SDK-level concept -- HeartBeat does not handle PINs

PostgreSQL-backed via pg_auth.py (replaces SQLite auth_connection.py).
"""

import hashlib
import hmac
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
from uuid6 import uuid7

from ..config import get_config
from ..database.pg_auth import get_pg_auth_database
from ..auth.jwt_manager import get_jwt_manager
from ..errors import HeartBeatError


logger = logging.getLogger(__name__)


# -- Error Helpers -----------------------------------------------------

def _auth_error(error_code: str, message: str, status_code: int, **extra):
    """Create a HeartBeatError with auth error code."""
    details = [extra] if extra else None
    raise HeartBeatError(
        error_code=error_code,
        message=message,
        status_code=status_code,
        details=details,
    )


def _now_iso() -> str:
    """UTC now as ISO string with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_dt(iso_str: str) -> datetime:
    """Parse ISO string (with Z) to timezone-aware datetime."""
    if isinstance(iso_str, datetime):
        return iso_str if iso_str.tzinfo else iso_str.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))


# -- Cipher Text Derivation -------------------------------------------

def _derive_cipher_text(master_secret: str, window_seconds: int = 540) -> str:
    """
    Derive cipher text from user's master_secret and current time window.

    cipher_text = HMAC-SHA256(master_secret, time_window_id)
    time_window_id = floor(unix_timestamp / window_seconds)

    Args:
        master_secret: Per-user secret (hex-encoded 32 bytes)
        window_seconds: Time window size (default: 540 = 9 minutes)

    Returns:
        Hex-encoded cipher text string
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    window_id = str(math.floor(now_ts / window_seconds))

    cipher_text = hmac.new(
        master_secret.encode("utf-8"),
        window_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return cipher_text


def _cipher_valid_until(window_seconds: int = 540) -> str:
    """Calculate when the current cipher text window expires."""
    now_ts = datetime.now(timezone.utc).timestamp()
    current_window = math.floor(now_ts / window_seconds)
    next_window_ts = (current_window + 1) * window_seconds
    valid_until = datetime.fromtimestamp(next_window_ts, tz=timezone.utc)
    return valid_until.isoformat().replace("+00:00", "Z")


# -- Login -------------------------------------------------------------

async def login(
    email: str,
    password: str,
) -> Dict[str, Any]:
    """
    Authenticate user with local credentials.

    AUTH_SERVICE_CONTRACT Section 4: POST /api/auth/login

    Flow:
        1. Look up user by email
        2. Verify password with bcrypt
        3. Check user is active
        4. Check concurrent session limit
        5. Detect first-run state -> issue bootstrap token
        6. Create session with 8-hour hard cap
        7. Issue short-lived Ed25519 JWT (30 min)
        8. Derive cipher_text from master_secret

    Returns:
        {access_token, token_type, cipher_text, expires_at,
         session_expires_at, user: {...}}
    """
    config = get_config()
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # 1. Look up user
    user = db.get_user_by_email(email)
    if user is None:
        _auth_error("TOKEN_INVALID", "Invalid credentials", 401)

    # 2. Check user is active
    if not user["is_active"]:
        _auth_error("TOKEN_INVALID", "Account is deactivated", 401)

    # 3. Verify password
    if user["password_hash"] is None:
        _auth_error(
            "TOKEN_INVALID",
            "Local login not available for this account",
            401,
        )

    password_valid = bcrypt.checkpw(
        password.encode("utf-8"),
        user["password_hash"].encode("utf-8"),
    )
    if not password_valid:
        _auth_error("TOKEN_INVALID", "Invalid credentials", 401)

    # 4. Check concurrent session limit
    max_sessions = db.get_tenant_max_sessions(user["tenant_id"])
    active_count = db.count_active_sessions(user["user_id"])
    if active_count >= max_sessions:
        _auth_error(
            "SESSION_LIMIT",
            f"Concurrent session limit reached ({max_sessions}). "
            "Revoke an existing session or contact your administrator.",
            409,
            max_sessions=max_sessions,
            active_sessions=active_count,
        )

    # 5. Check first-run state
    is_first_run = bool(user["is_first_run"])

    # 6. Build JWT payload
    now = datetime.now(timezone.utc)
    jwt_expires = now + timedelta(minutes=config.jwt_expiry_minutes)
    session_expires = now + timedelta(hours=config.session_hours)

    session_id = f"sess-{uuid7()}"
    jwt_jti = f"tok-{uuid7()}"
    now_iso = now.isoformat().replace("+00:00", "Z")
    jwt_expires_iso = jwt_expires.isoformat().replace("+00:00", "Z")
    session_expires_iso = session_expires.isoformat().replace("+00:00", "Z")

    # Get effective permissions
    permissions = db.get_user_permissions(
        user["user_id"], user["role_id"]
    )

    # Get permissions_version for change detection
    permissions_version = user.get("permissions_version", 1)

    payload = {
        "sub": user["user_id"],
        "tenant_id": user["tenant_id"],
        "role": user["role_id"],
        "permissions": permissions,
        "permissions_version": permissions_version,
        "last_auth_at": now_iso,
        "issued_at": now_iso,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_expires_iso,
        "jti": jwt_jti,
    }

    # First-run: restricted bootstrap token
    if is_first_run:
        payload["scope"] = "bootstrap"

    # 7. Sign JWT
    access_token = jwt_mgr.create_token(payload)

    # 8. Create session in PostgreSQL
    db.create_session(
        session_id=session_id,
        user_id=user["user_id"],
        jwt_jti=jwt_jti,
        issued_at=now_iso,
        expires_at=jwt_expires_iso,
        last_auth_at=now_iso,
        session_expires_at=session_expires_iso,
    )

    # 9. Stamp last login
    db.update_last_login(user["user_id"])

    # 10. Derive cipher_text from master_secret
    cipher_text = _derive_cipher_text(
        user["master_secret"],
        config.cipher_window_seconds,
    )

    logger.info(
        f"Login successful: user={user['user_id']} "
        f"role={user['role_id']} first_run={is_first_run} "
        f"session_cap={session_expires_iso}"
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "cipher_text": cipher_text,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_expires_iso,
        "user": {
            "user_id": user["user_id"],
            "role": user["role_id"],
            "display_name": user["display_name"],
            "tenant_id": user["tenant_id"],
            "is_first_run": is_first_run,
        },
    }


# -- Token Refresh -----------------------------------------------------

async def refresh_token(
    current_token: str,
) -> Dict[str, Any]:
    """
    Refresh session token (silent, within session hard cap).

    AUTH_SERVICE_CONTRACT Section 5: POST /api/auth/token/refresh

    Issues a new short-lived JWT. Does NOT extend the session.
    The session_expires_at is a hard cap from login time.

    Returns:
        {access_token, expires_at, session_expires_at, last_auth_at}
    """
    config = get_config()
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # 1. Verify current token
    try:
        claims = jwt_mgr.verify_token(current_token)
    except Exception:
        _auth_error(
            "TOKEN_INVALID", "Token expired or not recognised", 401
        )

    # 2. Look up session
    session = db.get_session_by_jti(claims["jti"])
    if session is None:
        _auth_error("TOKEN_INVALID", "Session not found", 401)

    if session["is_revoked"]:
        _auth_error("TOKEN_REVOKED", "Session has been revoked", 401)

    # 3. Check session hard cap
    now = datetime.now(timezone.utc)
    session_cap = session.get("session_expires_at")
    if session_cap:
        session_cap_dt = _iso_to_dt(session_cap)
        if now >= session_cap_dt:
            db.revoke_session_by_jti(claims["jti"], reason="session_expired")
            _auth_error(
                "SESSION_EXPIRED",
                "Session has expired. Please log in again.",
                401,
            )

    # 4. Look up user (ensure still active)
    user = db.get_user_by_id(session["user_id"])
    if user is None or not user["is_active"]:
        _auth_error("TOKEN_INVALID", "Account is deactivated", 401)

    # 5. Check permissions_version -- force re-auth on permission changes
    jwt_perm_version = claims.get("permissions_version", 1)
    db_perm_version = user.get("permissions_version", 1)
    if db_perm_version != jwt_perm_version:
        db.revoke_session_by_jti(claims["jti"], reason="permissions_changed")
        _auth_error(
            "PERMISSIONS_CHANGED",
            "Your permissions have changed. Please log in again.",
            401,
        )

    # 6. Issue new JWT (capped to session_expires_at)
    jwt_expires = now + timedelta(minutes=config.jwt_expiry_minutes)
    if session_cap:
        session_cap_dt = _iso_to_dt(session_cap)
        if jwt_expires > session_cap_dt:
            jwt_expires = session_cap_dt

    new_jti = f"tok-{uuid7()}"
    now_iso = now.isoformat().replace("+00:00", "Z")
    jwt_expires_iso = jwt_expires.isoformat().replace("+00:00", "Z")

    # Preserve last_auth_at from the session
    last_auth_at = session["last_auth_at"]
    if isinstance(last_auth_at, datetime):
        last_auth_at = last_auth_at.isoformat().replace("+00:00", "Z")
    else:
        last_auth_at = str(last_auth_at)

    session_cap_str = session.get("session_expires_at", "")
    if isinstance(session_cap_str, datetime):
        session_cap_str = session_cap_str.isoformat().replace("+00:00", "Z")
    else:
        session_cap_str = str(session_cap_str) if session_cap_str else ""

    # Refresh permissions from DB
    permissions = db.get_user_permissions(
        user["user_id"], user["role_id"]
    )

    payload = {
        "sub": user["user_id"],
        "tenant_id": user["tenant_id"],
        "role": user["role_id"],
        "permissions": permissions,
        "permissions_version": db_perm_version,
        "last_auth_at": last_auth_at,
        "issued_at": now_iso,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_cap_str,
        "jti": new_jti,
    }

    access_token = jwt_mgr.create_token(payload)

    # 7. Update session in PostgreSQL
    db.refresh_session(
        session_id=session["session_id"],
        new_jwt_jti=new_jti,
        new_expires_at=jwt_expires_iso,
    )

    logger.info(f"Token refreshed: user={user['user_id']}")

    return {
        "access_token": access_token,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_cap_str,
        "last_auth_at": last_auth_at,
    }


# -- Logout ------------------------------------------------------------

async def logout(current_token: str) -> Dict[str, Any]:
    """
    Revoke session.

    AUTH_SERVICE_CONTRACT Section 9: POST /api/auth/logout

    Accepts even expired tokens so sessions can always be revoked.

    Returns:
        {status: "logged_out"}
    """
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # Decode token -- even if expired, we want to revoke the session
    claims = jwt_mgr.decode_token_unsafe(current_token)
    if claims is None or "jti" not in claims:
        _auth_error(
            "TOKEN_INVALID", "Token expired or not recognised", 401
        )

    # Revoke by jti
    revoked = db.revoke_session_by_jti(claims["jti"], reason="logout")

    if revoked == 0:
        logger.warning(
            f"Logout: no active session found for jti={claims.get('jti')}"
        )
    else:
        logger.info(
            f"Logout: session revoked for user={claims.get('sub')}"
        )

    return {"status": "logged_out"}


# -- Token Introspection -----------------------------------------------

async def introspect_token(
    token: str,
    required_permission: Optional[str] = None,
    required_within_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Verify a user JWT (service-to-service).

    AUTH_SERVICE_CONTRACT Section 7: POST /api/auth/introspect

    Called by downstream services (Relay, Core) to verify a user JWT.

    Returns:
        Introspection response with active status, permissions,
        step_up_satisfied flag.
    """
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # 1. Verify JWT signature and decode
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        return {
            "active": False,
            "error_code": "TOKEN_INVALID",
            "message": "Token expired or not recognised",
        }

    # 2. Check session is not revoked + session hard cap
    jti = claims.get("jti")
    if jti:
        session = db.get_session_by_jti(jti)
        if session is None:
            return {
                "active": False,
                "error_code": "TOKEN_INVALID",
                "message": "Session not found",
            }
        if session["is_revoked"]:
            return {
                "active": False,
                "error_code": "TOKEN_REVOKED",
                "message": "Session has been explicitly revoked",
            }
        session_cap = session.get("session_expires_at")
        if session_cap:
            session_cap_dt = _iso_to_dt(session_cap)
            now = datetime.now(timezone.utc)
            if now >= session_cap_dt:
                return {
                    "active": False,
                    "error_code": "SESSION_EXPIRED",
                    "message": "Session has expired. Please log in again.",
                }

    # 3. Check user is still active
    user_id = claims.get("sub")
    user = db.get_user_by_id(user_id) if user_id else None
    if user is None or not user["is_active"]:
        return {
            "active": False,
            "error_code": "TOKEN_INVALID",
            "message": "User account is deactivated",
        }

    # 4. Check first-run state
    if user["is_first_run"]:
        return {
            "active": False,
            "error_code": "FIRST_RUN_REQUIRED",
            "message": "First-run setup not complete",
        }

    # 5. Check permissions_version
    jwt_perm_version = claims.get("permissions_version", 1)
    db_perm_version = user.get("permissions_version", 1)
    if db_perm_version != jwt_perm_version:
        return {
            "active": False,
            "error_code": "PERMISSIONS_CHANGED",
            "message": "Permissions have changed. Please log in again.",
        }

    # 6. Build base response
    permissions = db.get_user_permissions(user_id, user["role_id"])
    last_auth_at = claims.get("last_auth_at", "")

    response: Dict[str, Any] = {
        "active": True,
        "actor_type": "human",
        "user_id": user_id,
        "role": user["role_id"],
        "permissions": permissions,
        "tenant_id": user["tenant_id"],
        "last_auth_at": last_auth_at,
        "expires_at": claims.get("expires_at", ""),
        "session_expires_at": claims.get("session_expires_at", ""),
    }

    # 7. Check required permission
    if required_permission:
        has_permission = (
            "*" in permissions
            or required_permission in permissions
        )
        if not has_permission:
            return {
                "active": True,
                "actor_type": "human",
                "user_id": user_id,
                "role": user["role_id"],
                "error_code": "PERMISSION_DENIED",
                "message": f"User lacks required permission: "
                           f"{required_permission}",
            }

    # 8. Check step-up freshness
    step_up_satisfied = True
    if required_within_seconds is not None and last_auth_at:
        try:
            auth_dt = _iso_to_dt(last_auth_at)
            now = datetime.now(timezone.utc)
            elapsed = (now - auth_dt).total_seconds()
            step_up_satisfied = elapsed <= required_within_seconds
        except (ValueError, TypeError):
            step_up_satisfied = False

    response["step_up_satisfied"] = step_up_satisfied

    if not step_up_satisfied:
        response["error_code"] = "STEP_UP_REQUIRED"
        response["required_within_seconds"] = required_within_seconds

    return response


# -- Step-Up Authentication ---------------------------------------------

async def step_up_auth(
    current_token: str,
    password: str,
) -> Dict[str, Any]:
    """
    Step-up re-authentication.

    AUTH_SERVICE_CONTRACT Section 6: POST /api/auth/stepup

    Verifies the user's password, updates last_auth_at on the session,
    issues a new JWT with fresh last_auth_at, and returns cipher_text.

    Returns:
        {access_token, cipher_text, expires_at, session_expires_at, last_auth_at}
    """
    config = get_config()
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # 1. Verify current token
    try:
        claims = jwt_mgr.verify_token(current_token)
    except Exception:
        _auth_error(
            "TOKEN_INVALID", "Token expired or not recognised", 401
        )

    # 2. Look up session
    session = db.get_session_by_jti(claims["jti"])
    if session is None:
        _auth_error("TOKEN_INVALID", "Session not found", 401)

    if session["is_revoked"]:
        _auth_error("TOKEN_REVOKED", "Session has been revoked", 401)

    # 3. Check session hard cap
    now = datetime.now(timezone.utc)
    session_cap = session.get("session_expires_at")
    if session_cap:
        session_cap_dt = _iso_to_dt(session_cap)
        if now >= session_cap_dt:
            db.revoke_session_by_jti(claims["jti"], reason="session_expired")
            _auth_error(
                "SESSION_EXPIRED",
                "Session has expired. Please log in again.",
                401,
            )

    # 4. Look up user
    user = db.get_user_by_id(session["user_id"])
    if user is None or not user["is_active"]:
        _auth_error("TOKEN_INVALID", "Account is deactivated", 401)

    # 5. Verify password
    if user["password_hash"] is None:
        _auth_error(
            "TOKEN_INVALID",
            "Local login not available for this account",
            401,
        )

    password_valid = bcrypt.checkpw(
        password.encode("utf-8"),
        user["password_hash"].encode("utf-8"),
    )
    if not password_valid:
        _auth_error("TOKEN_INVALID", "Invalid password", 401)

    # 6. Issue new JWT with updated last_auth_at
    jwt_expires = now + timedelta(minutes=config.jwt_expiry_minutes)

    # Cap to session expiry
    if session_cap:
        session_cap_dt = _iso_to_dt(session_cap)
        if jwt_expires > session_cap_dt:
            jwt_expires = session_cap_dt

    new_jti = f"tok-{uuid7()}"
    now_iso = now.isoformat().replace("+00:00", "Z")
    jwt_expires_iso = jwt_expires.isoformat().replace("+00:00", "Z")

    session_cap_str = session.get("session_expires_at", "")
    if isinstance(session_cap_str, datetime):
        session_cap_str = session_cap_str.isoformat().replace("+00:00", "Z")
    else:
        session_cap_str = str(session_cap_str) if session_cap_str else ""

    # Refresh permissions from DB
    permissions = db.get_user_permissions(
        user["user_id"], user["role_id"]
    )
    db_perm_version = user.get("permissions_version", 1)

    payload = {
        "sub": user["user_id"],
        "tenant_id": user["tenant_id"],
        "role": user["role_id"],
        "permissions": permissions,
        "permissions_version": db_perm_version,
        "last_auth_at": now_iso,
        "issued_at": now_iso,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_cap_str,
        "jti": new_jti,
    }

    access_token = jwt_mgr.create_token(payload)

    # 7. Update session with new last_auth_at
    db.update_session_auth(
        session_id=session["session_id"],
        new_jwt_jti=new_jti,
        new_expires_at=jwt_expires_iso,
        new_last_auth_at=now_iso,
    )

    # 8. Derive fresh cipher_text
    cipher_text = _derive_cipher_text(
        user["master_secret"],
        config.cipher_window_seconds,
    )

    logger.info(
        f"Step-up auth successful: user={user['user_id']}"
    )

    return {
        "access_token": access_token,
        "cipher_text": cipher_text,
        "expires_at": jwt_expires_iso,
        "session_expires_at": session_cap_str,
        "last_auth_at": now_iso,
    }


# -- Step-Up Policy Query ----------------------------------------------

async def get_operation_policy(
    operation: str,
) -> Dict[str, Any]:
    """
    Get step-up policy for an operation.

    AUTH_SERVICE_CONTRACT Section 6.5: GET /api/auth/operations/{op}/policy

    Returns:
        {operation, required_within_seconds, tier}
    """
    db = get_pg_auth_database()

    policy = db.get_step_up_policy(operation)
    if policy is None:
        # Default: routine (1 hour)
        return {
            "operation": operation,
            "required_within_seconds": 3600,
            "tier": "routine",
        }

    return {
        "operation": policy["operation"],
        "required_within_seconds": policy["required_within_seconds"],
        "tier": policy["tier"],
    }


# -- Password Policy Helpers -------------------------------------------

def _check_password_strength(password: str) -> None:
    """Raise HeartBeatError(PW_WEAK) if password does not meet requirements.

    Rules:
        - Minimum 10 characters
        - At least one uppercase letter (A-Z)
        - At least one lowercase letter (a-z)
        - At least one digit (0-9)
    """
    errors = []
    if len(password) < 10:
        errors.append("at least 10 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("one lowercase letter")
    if not re.search(r"[0-9]", password):
        errors.append("one number")
    if errors:
        _auth_error(
            "PW_WEAK",
            f"Password must contain: {', '.join(errors)}",
            400,
        )


def _check_password_recycled(
    db,
    user_id: str,
    current_hash: Optional[str],
    new_password: str,
) -> None:
    """Raise HeartBeatError(PW_RECYCLED) if new password matches any recent hash."""
    encoded = new_password.encode("utf-8")

    # Current active password
    if current_hash and bcrypt.checkpw(encoded, current_hash.encode("utf-8")):
        _auth_error(
            "PW_RECYCLED",
            "New password cannot be the same as your current password",
            400,
        )

    # Last 5 history entries
    for old_hash in db.get_password_history(user_id, limit=5):
        if bcrypt.checkpw(encoded, old_hash.encode("utf-8")):
            _auth_error(
                "PW_RECYCLED",
                "New password cannot match a recently used password",
                400,
            )


# -- Change Password ---------------------------------------------------

async def change_password(
    token: str,
    new_password: str,
    current_password: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Change a user's password.

    Two modes:
        Bootstrap (token scope == "bootstrap", is_first_run user):
            No current_password required.

        Normal (full-scope token):
            current_password must be supplied and must match.

    Returns:
        {"status": "password_changed"}
    """
    db = get_pg_auth_database()
    jwt_mgr = get_jwt_manager()

    # 1. Verify token signature
    try:
        claims = jwt_mgr.verify_token(token)
    except Exception:
        _auth_error("TOKEN_INVALID", "Token expired or not recognised", 401)

    user_id = claims.get("sub")
    if not user_id:
        _auth_error("TOKEN_INVALID", "Token missing subject", 401)

    # 2. Load user record
    user = db.get_user_by_id(user_id)
    if user is None or not user["is_active"]:
        _auth_error("TOKEN_INVALID", "Account not found or deactivated", 401)

    is_bootstrap = (claims.get("scope") == "bootstrap")

    # 3. For normal change: verify current password
    if not is_bootstrap:
        if not current_password:
            _auth_error(
                "PW_WRONG_CURRENT",
                "Current password is required",
                400,
            )
        if user["password_hash"] is None:
            _auth_error(
                "TOKEN_INVALID",
                "Local login not available for this account",
                401,
            )
        if not bcrypt.checkpw(
            current_password.encode("utf-8"),
            user["password_hash"].encode("utf-8"),
        ):
            _auth_error("PW_WRONG_CURRENT", "Current password is incorrect", 400)

    # 4. Server-side strength check
    _check_password_strength(new_password)

    # 5. Recycling check
    _check_password_recycled(db, user_id, user.get("password_hash"), new_password)

    # 6. Hash the new password (bcrypt, 12 rounds)
    new_hash = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    # 7. Archive current hash before overwriting
    if user.get("password_hash"):
        db.add_password_history(user_id, user["password_hash"])
        db.trim_password_history(user_id, keep=5)

    # 8. Write new hash
    db.update_password(user_id, new_hash, clear_first_run=is_bootstrap)

    # 9. Revoke all active sessions -- force re-authentication
    db.revoke_all_user_sessions(user_id, reason="password_changed")

    logger.info(
        f"Password changed: user={user_id} bootstrap={is_bootstrap}"
    )

    return {"status": "password_changed"}


# -- Cipher Text for SSE -----------------------------------------------

async def get_cipher_text_for_user(user_id: str) -> Dict[str, Any]:
    """
    Derive cipher text for SSE delivery.

    Called by the SSE producer to push cipher_text refresh events.

    Returns:
        {cipher_text, valid_until, window_seconds}
    """
    config = get_config()
    db = get_pg_auth_database()

    master_secret = db.get_master_secret(user_id)
    if master_secret is None:
        return None

    cipher_text = _derive_cipher_text(
        master_secret,
        config.cipher_window_seconds,
    )
    valid_until = _cipher_valid_until(config.cipher_window_seconds)

    return {
        "cipher_text": cipher_text,
        "valid_until": valid_until,
        "window_seconds": config.cipher_window_seconds,
    }
