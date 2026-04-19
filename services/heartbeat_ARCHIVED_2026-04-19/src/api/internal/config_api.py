"""
Config Database API (Q5 — Demo Question)

CRUD endpoints for config entries, tier limits, feature flags,
and the database catalog.

Endpoints:
    # Config Entries
    GET    /api/config                      — List all config entries
    GET    /api/config/{service}/{key}      — Get a single config entry
    PUT    /api/config/{service}/{key}      — Set/update a config entry
    DELETE /api/config/{service}/{key}      — Delete a config entry

    # Tier Limits
    GET    /api/tiers                       — List all tier limits
    GET    /api/tiers/{tier}                — Get limits for a specific tier

    # Feature Flags
    GET    /api/flags                       — List all feature flags
    GET    /api/flags/{flag_name}           — Get a specific flag
    PUT    /api/flags/{flag_name}           — Set/update a flag
    DELETE /api/flags/{flag_name}           — Delete a flag

    # Database Catalog
    GET    /api/databases                   — List full catalog
    GET    /api/databases/{service}         — List databases for a service
    GET    /api/databases/summary           — Catalog summary stats
    POST   /api/databases/register          — Register a database
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...database.config_db import get_config_database


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Configuration"])


# ── Pydantic Models ────────────────────────────────────────────────────

class ConfigEntryBody(BaseModel):
    config_value: str
    value_type: str = "string"
    description: Optional[str] = None
    updated_by: str = "api"


class FeatureFlagBody(BaseModel):
    is_enabled: bool
    scope: str = "global"
    description: Optional[str] = None


class DatabaseRegisterBody(BaseModel):
    db_logical_name: str
    db_category: str
    tenant_id: str
    owner_service: str
    db_physical_name: str
    db_path: str
    db_engine: str = "sqlite"
    status: str = "active"
    schema_version: Optional[str] = None
    size_bytes: Optional[int] = None
    description: Optional[str] = None


# ── Config Entries ─────────────────────────────────────────────────────

@router.get("/api/config")
async def list_config_entries(
    service_name: Optional[str] = Query(None, description="Filter by service name"),
):
    """List all config entries, optionally filtered by service."""
    db = get_config_database()
    entries = db.get_all_config(service_name)
    return {"entries": entries, "count": len(entries)}


@router.get("/api/config/{service_name}/{config_key}")
async def get_config_entry(service_name: str, config_key: str):
    """Get a single config entry."""
    db = get_config_database()
    entry = db.get_config_entry(service_name, config_key)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Config key '{service_name}/{config_key}' not found")
    return entry


@router.put("/api/config/{service_name}/{config_key}")
async def set_config_entry(
    service_name: str, config_key: str, body: ConfigEntryBody
):
    """Create or update a config entry."""
    db = get_config_database()
    try:
        db.set_config_entry(
            service_name=service_name,
            config_key=config_key,
            config_value=body.config_value,
            value_type=body.value_type,
            description=body.description,
            updated_by=body.updated_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"status": "ok", "service_name": service_name, "config_key": config_key}


@router.delete("/api/config/{service_name}/{config_key}")
async def delete_config_entry(service_name: str, config_key: str):
    """Delete a config entry."""
    db = get_config_database()
    try:
        count = db.delete_config_entry(service_name, config_key)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if count == 0:
        raise HTTPException(status_code=404, detail=f"Config key '{service_name}/{config_key}' not found")
    return {"status": "deleted", "service_name": service_name, "config_key": config_key}


# ── Tier Limits ────────────────────────────────────────────────────────

@router.get("/api/tiers")
async def list_tier_limits(
    tier: Optional[str] = Query(None, description="Filter by tier (test/standard/pro/enterprise)"),
):
    """List all tier limits, optionally filtered by tier."""
    db = get_config_database()
    if tier:
        limits = db.get_all_limits_for_tier(tier)
    else:
        limits = db.get_all_tier_limits()
    return {"limits": limits, "count": len(limits)}


@router.get("/api/tiers/{tier}")
async def get_tier_limits(tier: str):
    """Get all limits for a specific tier."""
    db = get_config_database()
    limits = db.get_all_limits_for_tier(tier)
    if not limits:
        raise HTTPException(status_code=404, detail=f"No limits found for tier '{tier}'")
    return {"tier": tier, "limits": limits, "count": len(limits)}


# ── Feature Flags ──────────────────────────────────────────────────────

@router.get("/api/flags")
async def list_feature_flags(
    scope: Optional[str] = Query(None, description="Filter by scope"),
):
    """List all feature flags, optionally filtered by scope."""
    db = get_config_database()
    flags = db.get_all_feature_flags(scope)
    return {"flags": flags, "count": len(flags)}


@router.get("/api/flags/{flag_name}")
async def get_feature_flag(flag_name: str):
    """Get a specific feature flag."""
    db = get_config_database()
    flag = db.get_feature_flag(flag_name)
    if not flag:
        raise HTTPException(status_code=404, detail=f"Feature flag '{flag_name}' not found")
    return flag


@router.put("/api/flags/{flag_name}")
async def set_feature_flag(flag_name: str, body: FeatureFlagBody):
    """Create or update a feature flag."""
    db = get_config_database()
    db.set_feature_flag(
        flag_name=flag_name,
        is_enabled=body.is_enabled,
        scope=body.scope,
        description=body.description,
    )
    return {"status": "ok", "flag_name": flag_name, "is_enabled": body.is_enabled}


@router.delete("/api/flags/{flag_name}")
async def delete_feature_flag(flag_name: str):
    """Delete a feature flag."""
    db = get_config_database()
    count = db.delete_feature_flag(flag_name)
    if count == 0:
        raise HTTPException(status_code=404, detail=f"Feature flag '{flag_name}' not found")
    return {"status": "deleted", "flag_name": flag_name}


# ── Database Catalog ───────────────────────────────────────────────────

@router.get("/api/databases/summary")
async def database_catalog_summary():
    """Get summary statistics for the database catalog."""
    db = get_config_database()
    return db.get_catalog_summary()


@router.get("/api/databases")
async def list_databases(
    status: Optional[str] = Query(None, description="Filter by status"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant"),
):
    """List all databases in the catalog."""
    db = get_config_database()
    if tenant_id:
        catalog = db.get_databases_by_tenant(tenant_id)
    else:
        catalog = db.get_full_catalog(status)
    return {"databases": catalog, "count": len(catalog)}


@router.get("/api/databases/{owner_service}")
async def list_databases_for_service(owner_service: str):
    """List all databases owned by a specific service."""
    db = get_config_database()
    catalog = db.get_databases_by_service(owner_service)
    return {"owner_service": owner_service, "databases": catalog, "count": len(catalog)}


@router.post("/api/databases/register")
async def register_database(body: DatabaseRegisterBody):
    """Register or update a database in the catalog."""
    db = get_config_database()
    db.register_database(
        db_logical_name=body.db_logical_name,
        db_category=body.db_category,
        tenant_id=body.tenant_id,
        owner_service=body.owner_service,
        db_physical_name=body.db_physical_name,
        db_path=body.db_path,
        db_engine=body.db_engine,
        status=body.status,
        schema_version=body.schema_version,
        size_bytes=body.size_bytes,
        description=body.description,
    )
    return {
        "status": "registered",
        "db_logical_name": body.db_logical_name,
        "tenant_id": body.tenant_id,
    }
