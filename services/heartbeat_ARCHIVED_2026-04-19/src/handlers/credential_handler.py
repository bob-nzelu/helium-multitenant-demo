"""
Credential Management Handler

Generates, validates, rotates, and revokes API keys for inter-service auth.
Follows IntelliCore's AuthValidator pattern (bcrypt, constant-time comparison).

Key format: {2-letter-svc}_{env}_{random_hex}
Secret: secrets.token_urlsafe(48) -> ~64 chars, bcrypt-hashed (12 rounds)
"""

import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from uuid6 import uuid7

logger = logging.getLogger(__name__)

# bcrypt import with graceful fallback
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:  # pragma: no cover
    BCRYPT_AVAILABLE = False
    logger.warning("bcrypt not installed — credential management will fail")


# ── Key Generation ─────────────────────────────────────────────────────

SERVICE_PREFIXES = {
    "heartbeat": "hb",
    "relay": "rl",
    "core": "cr",
    "edge": "ed",
    "float-sdk": "fl",
}


def generate_api_key(service_name: str, environment: str = "test") -> str:
    """
    Generate a prefixed API key.

    Format: {2-letter-prefix}_{env}_{random_hex}
    Example: rl_test_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4
    """
    prefix = SERVICE_PREFIXES.get(service_name, "xx")
    random_part = secrets.token_hex(16)  # 32 hex chars
    return f"{prefix}_{environment}_{random_part}"


def generate_api_secret() -> str:
    """
    Generate a cryptographically secure API secret.

    Returns ~64-char URL-safe base64 string (48 random bytes).
    """
    return secrets.token_urlsafe(48)


def hash_secret(secret: str) -> str:
    """
    Hash an API secret using bcrypt (12 rounds).

    Returns bcrypt hash string.
    Raises RuntimeError if bcrypt is not installed.
    """
    if not BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt not installed — cannot hash secrets")
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_secret(secret: str, hashed: str) -> bool:
    """
    Verify an API secret against its bcrypt hash.

    Uses constant-time comparison to prevent timing attacks.
    Returns True if secret matches, False otherwise.
    """
    if not BCRYPT_AVAILABLE:
        raise RuntimeError("bcrypt not installed — cannot verify secrets")
    try:
        return bcrypt.checkpw(secret.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── Credential Lifecycle ───────────────────────────────────────────────

async def create_credential(
    service_name: str,
    issued_to: str,
    permissions: Optional[List[str]] = None,
    expires_at: Optional[str] = None,
    environment: str = "test",
) -> Dict[str, Any]:
    """
    Generate a new API key/secret pair and store in registry.

    Returns dict with credential_id, api_key, api_secret (plaintext).
    The secret is returned ONLY at creation time — never retrievable again.
    """
    from ..database.registry import get_registry_database

    credential_id = f"cred-{uuid7()}"
    api_key = generate_api_key(service_name, environment)
    api_secret = generate_api_secret()
    secret_hash = hash_secret(api_secret)

    db = get_registry_database()
    db.create_credential(
        credential_id=credential_id,
        api_key=api_key,
        api_secret_hash=secret_hash,
        service_name=service_name,
        issued_to=issued_to,
        permissions=permissions or [],
        expires_at=expires_at,
    )

    db.log_key_rotation(
        credential_id=credential_id,
        action="created",
        performed_by="heartbeat-api",
        reason=f"New credential for {issued_to}",
    )

    logger.info(f"Created credential {api_key[:12]}... for {issued_to} ({service_name})")

    return {
        "credential_id": credential_id,
        "api_key": api_key,
        "api_secret": api_secret,  # Plaintext — ONLY returned at creation
        "service_name": service_name,
        "issued_to": issued_to,
    }


async def rotate_credential(
    credential_id: str,
    performed_by: str = "admin",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rotate an existing credential (generate new key + secret).

    Returns dict with credential_id, new_api_key, new_api_secret.
    """
    from ..database.registry import get_registry_database

    db = get_registry_database()
    cred = db.execute_query(
        "SELECT * FROM api_credentials WHERE credential_id = ?",
        (credential_id,),
    )
    if not cred:
        raise ValueError(f"Credential not found: {credential_id}")

    old_key = cred[0]["api_key"]
    service_name = cred[0]["service_name"]

    new_api_key = generate_api_key(service_name)
    new_api_secret = generate_api_secret()
    new_hash = hash_secret(new_api_secret)

    db.rotate_credential(credential_id, new_api_key, new_hash)
    db.log_key_rotation(
        credential_id=credential_id,
        action="rotated",
        performed_by=performed_by,
        old_key_prefix=old_key[:8],
        reason=reason or "Routine rotation",
    )

    logger.info(f"Rotated credential {old_key[:8]}... -> {new_api_key[:12]}...")

    return {
        "credential_id": credential_id,
        "new_api_key": new_api_key,
        "new_api_secret": new_api_secret,
        "service_name": service_name,
    }


async def revoke_credential(
    credential_id: str,
    performed_by: str = "admin",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Revoke a credential (sets status to 'revoked')."""
    from ..database.registry import get_registry_database

    db = get_registry_database()
    cred = db.execute_query(
        "SELECT api_key, service_name, issued_to FROM api_credentials WHERE credential_id = ?",
        (credential_id,),
    )
    if not cred:
        raise ValueError(f"Credential not found: {credential_id}")

    db.update_credential_status(credential_id, "revoked")
    db.log_key_rotation(
        credential_id=credential_id,
        action="revoked",
        performed_by=performed_by,
        old_key_prefix=cred[0]["api_key"][:8],
        reason=reason or "Manual revocation",
    )

    logger.info(f"Revoked credential {cred[0]['api_key'][:8]}... ({cred[0]['issued_to']})")

    return {
        "credential_id": credential_id,
        "status": "revoked",
        "service_name": cred[0]["service_name"],
        "issued_to": cred[0]["issued_to"],
    }


async def validate_api_key(api_key: str, api_secret: str) -> Dict[str, Any]:
    """
    Validate an API key + secret.

    1. Look up credential by api_key
    2. Verify secret against bcrypt hash
    3. Check status (active only)
    4. Check expiry
    5. Update last_used_at
    6. Return credential dict

    Raises ValueError on any validation failure.
    """
    from ..database.registry import get_registry_database

    db = get_registry_database()
    cred = db.get_credential_by_key(api_key)

    if not cred:
        raise ValueError("Invalid API key")

    # Verify secret
    if not verify_secret(api_secret, cred["api_secret_hash"]):
        raise ValueError("Invalid API secret")

    # Check status
    if cred["status"] == "revoked":
        raise ValueError(f"API key has been revoked: {api_key[:8]}...")
    if cred["status"] == "inactive":
        raise ValueError(f"API key is inactive: {api_key[:8]}...")

    # Check expiry
    if cred["expires_at"]:
        expiry = datetime.fromisoformat(cred["expires_at"])
        if datetime.now(timezone.utc) > expiry:
            raise ValueError(f"API key has expired: {api_key[:8]}...")

    # Update last used
    db.update_credential_last_used(api_key)

    return {
        "credential_id": cred["credential_id"],
        "api_key": cred["api_key"],
        "service_name": cred["service_name"],
        "issued_to": cred["issued_to"],
        "permissions": cred["permissions"],
        "status": cred["status"],
    }
