"""
Registration Handler — App Registration + Config Bundle

Handles POST /api/auth/register-app endpoint.
Returns source_id + tenant config + endpoints + capabilities + feature flags.

Config data sourced from:
    - auth.users (tenant_id) via pg_auth
    - config.db (config_entries, tier_limits, feature_flags) via ConfigDatabase
"""

import logging
from typing import Any, Dict, Optional

from ..database.pg_auth import get_pg_auth_database
from ..database.config_db import get_config_database
from ..config import get_config

logger = logging.getLogger(__name__)


async def register_app(
    user_id: str,
    tenant_id: str,
    source_type: str,
    source_name: str,
    device_id: str,
    app_version: Optional[str] = None,
    machine_guid: Optional[str] = None,
    mac_address: Optional[str] = None,
    computer_name: Optional[str] = None,
    os_type: Optional[str] = None,
    os_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Register a frontend app instance with HeartBeat.

    Idempotent: same device_id + source_type returns existing registration.
    Generates source_id: src-{source_type[:5]}-{device_id[:6]}-{sequence}

    Returns full config bundle: source_id + tenant + endpoints +
    capabilities + feature_flags + security.
    """
    db = get_pg_auth_database()

    # Register device if details provided
    if machine_guid and os_type:
        db.register_device(
            device_id=device_id,
            machine_guid=machine_guid,
            os_type=os_type,
            mac_address=mac_address,
            computer_name=computer_name,
            os_version=os_version,
            app_type=source_type,
            app_version=app_version,
            user_id=user_id,
        )

    # Check for existing registration (idempotent)
    existing = db.get_app_registration(device_id, source_type)
    if existing:
        db.update_app_registration_seen(device_id, source_type, app_version)
        source_id = existing["source_id"]
        logger.info(
            f"App re-registered: source_id={source_id} "
            f"device={device_id} type={source_type}"
        )
    else:
        # Generate source_id
        seq = db.get_next_source_sequence(source_type, device_id)
        source_id = f"src-{source_type[:5]}-{device_id[:6]}-{seq:03d}"

        db.create_app_registration(
            source_id=source_id,
            source_type=source_type,
            source_name=source_name,
            device_id=device_id,
            user_id=user_id,
            tenant_id=tenant_id,
            app_version=app_version,
        )
        logger.info(
            f"App registered: source_id={source_id} "
            f"device={device_id} type={source_type}"
        )

    # Build config bundle from config.db
    config_bundle = _build_config_bundle(tenant_id, source_type)

    return {
        "source_id": source_id,
        **config_bundle,
    }


def _build_config_bundle(
    tenant_id: str, source_type: str
) -> Dict[str, Any]:
    """
    Build the full config bundle from config.db tables.

    Merges tenant info, endpoints, capabilities, feature flags,
    and security settings.
    """
    hb_config = get_config()

    # Tenant info from auth.users
    db = get_pg_auth_database()
    # Get any user with this tenant_id to extract tenant info
    # (tenant info is the same for all users in a tenant)

    # Try to get tenant config from config.db
    tenant = _get_tenant_config(tenant_id)
    endpoints = _get_endpoints()
    capabilities = _get_capabilities(source_type)
    feature_flags = _get_feature_flags()
    security = _get_security_settings()

    return {
        "tenant": tenant,
        "endpoints": endpoints,
        "capabilities": capabilities,
        "feature_flags": feature_flags,
        "security": security,
    }


def _get_tenant_config(tenant_id: str) -> Dict[str, Any]:
    """Get tenant info from config.db config_entries."""
    try:
        config_db = get_config_database()
        company_name = config_db.get_config_value("_shared", "company_name") or ""
        tin = config_db.get_config_value("_shared", "tin") or ""
        firs_service_id = config_db.get_config_value("_shared", "firs_service_id") or ""
        invoice_prefix = config_db.get_config_value("_shared", "invoice_prefix") or ""
    except Exception:
        company_name = tin = firs_service_id = invoice_prefix = ""

    return {
        "tenant_id": tenant_id,
        "company_name": company_name,
        "tin": tin,
        "firs_service_id": firs_service_id,
        "invoice_prefix": invoice_prefix,
    }


def _get_endpoints() -> Dict[str, str]:
    """Get service endpoints from config.db or defaults."""
    try:
        config_db = get_config_database()
        heartbeat_url = config_db.get_config_value("heartbeat", "url") or ""
        relay_url = config_db.get_config_value("relay", "url") or ""
        core_url = config_db.get_config_value("core", "url") or ""
        heartbeat_sse = config_db.get_config_value("heartbeat", "sse_url") or ""
        core_sse = config_db.get_config_value("core", "sse_url") or ""
    except Exception:
        heartbeat_url = relay_url = core_url = ""
        heartbeat_sse = core_sse = ""

    hb_config = get_config()

    return {
        "heartbeat": heartbeat_url or f"http://localhost:{hb_config.port}",
        "heartbeat_sse": heartbeat_sse or f"http://localhost:{hb_config.port}/api/sse/stream",
        "relay": relay_url or "http://localhost:8082",
        "core": core_url or "http://localhost:8080",
        "core_sse": core_sse or "http://localhost:8080/api/sse/stream",
    }


def _get_capabilities(source_type: str) -> Dict[str, Any]:
    """Get capabilities from config.db tier_limits."""
    try:
        config_db = get_config_database()
        hb_config = get_config()
        tier = hb_config.tier

        limits = config_db.execute_query(
            "SELECT limit_key, limit_value, value_type FROM tier_limits WHERE tier = ?",
            (tier,),
        )

        caps = {}
        for row in limits:
            key = row["limit_key"]
            val = row["limit_value"]
            vtype = row.get("value_type", "string")
            if vtype == "integer":
                caps[key] = int(val)
            elif vtype == "boolean":
                caps[key] = val.lower() in ("true", "1", "yes")
            elif vtype == "float":
                caps[key] = float(val)
            else:
                caps[key] = val

        return caps
    except Exception:
        # Sensible defaults
        return {
            "can_upload": True,
            "can_finalize": True,
            "max_file_size_mb": 10,
            "allowed_extensions": [".pdf", ".xml", ".json"],
        }


def _get_feature_flags() -> Dict[str, bool]:
    """Get feature flags from config.db."""
    try:
        config_db = get_config_database()
        flags = config_db.execute_query(
            "SELECT flag_name, is_enabled FROM feature_flags"
        )
        return {row["flag_name"]: bool(row["is_enabled"]) for row in flags}
    except Exception:
        return {
            "sse_enabled": True,
            "bulk_upload_enabled": True,
            "inbound_review_enabled": False,
        }


def _get_security_settings() -> Dict[str, Any]:
    """Get security settings from config or defaults."""
    hb_config = get_config()
    return {
        "session_timeout_hours": hb_config.session_hours,
        "jwt_refresh_minutes": hb_config.jwt_expiry_minutes,
        "step_up_required_for": [
            "invoice.finalize",
            "invoice.approve",
            "user.create.admin",
            "user.deactivate",
            "config.write",
        ],
    }
