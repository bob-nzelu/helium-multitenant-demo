"""
Schema API — serves canonical database schemas to Helium services.

Endpoints:
    GET  /api/schemas              List all registered schemas (name, version)
    GET  /api/schemas/{name}       Get full schema SQL + metadata
    GET  /api/schemas/{name}/sql   Get raw SQL text only (Content-Type: text/plain)
    PUT  /api/schemas/{name}       Admin hot-reload: upload new schema version
    POST /internal/reload-schemas  Trigger disk reload of all schemas

Usage by consumers:
    - Core:     GET /api/schemas/invoices/sql  → apply to invoices.db at startup
    - SDK:      GET /api/schemas/invoices      → compare version, apply if newer
    - Edge:     GET /api/schemas/invoices      → read schema for contract validation
    - Admin:    PUT /api/schemas/invoices      → push new canonical schema version
    - Ops:      POST /internal/reload-schemas  → reload all schemas from disk

Security:
    GET endpoints are unauthenticated (schema SQL is not sensitive).
    PUT endpoint should be restricted to admin access in production.
    Rate limiting applies via standard middleware.
"""

import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ...schemas import get_schema_registry
from ...schemas.notifier import get_schema_notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schemas", tags=["schemas"])


@router.get("")
async def list_schemas():
    """
    List all registered canonical schemas.

    Returns:
        {
            "schemas": [
                {"name": "invoices", "version": "2.0", "description": "..."}
            ]
        }
    """
    registry = get_schema_registry()
    schemas = registry.list_schemas()
    return {"schemas": schemas}


@router.get("/{name}")
async def get_schema(name: str):
    """
    Get a canonical schema by name.

    Returns full metadata + SQL text. Services use this to:
    - Check if their local schema version matches canonical
    - Apply the schema when creating new databases
    - Validate their local schema against the canonical definition

    Args:
        name: Schema name (e.g. "invoices")

    Returns:
        {
            "name": "invoices",
            "version": "2.0",
            "description": "...",
            "sql": "CREATE TABLE invoices (...); ...",
            "sql_length": 12345
        }
    """
    registry = get_schema_registry()
    info = registry.get_schema(name)

    if info is None:
        available = [s["name"] for s in registry.list_schemas()]
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Schema '{name}' not found",
                "available_schemas": available,
            },
        )

    return {
        "name": info.name,
        "version": info.version,
        "description": info.description,
        "sql": info.sql,
        "sql_length": len(info.sql),
    }


@router.get("/{name}/sql")
async def get_schema_sql(name: str):
    """
    Get raw SQL text for a schema (Content-Type: text/plain).

    This endpoint returns ONLY the SQL — no JSON wrapper.
    Useful for piping directly to sqlite3:

        curl http://heartbeat:9000/api/schemas/invoices/sql | sqlite3 invoices.db

    Args:
        name: Schema name (e.g. "invoices")
    """
    registry = get_schema_registry()
    sql = registry.get_schema_sql(name)

    if sql is None:
        raise HTTPException(
            status_code=404,
            detail=f"Schema '{name}' not found",
        )

    return PlainTextResponse(
        content=sql,
        media_type="text/plain; charset=utf-8",
    )


@router.put("/{name}")
async def upload_schema(name: str, request: Request):
    """
    Upload a new version of a canonical schema (admin hot-reload).

    Accepts raw SQL text as the request body. The SQL must contain a version
    header (e.g. "-- CANONICAL SCHEMA v2.1"). The new version must be strictly
    newer than the current version.

    After writing the schema to disk, notifies all registered services via
    HTTP callback and publishes an SSE event.

    Args:
        name: Schema name (e.g. "invoices")

    Returns:
        200: {name, old_version, new_version, notifications}
        400: Invalid SQL body
        409: Version not newer than current
    """
    body = (await request.body()).decode("utf-8")

    if not body or not body.strip():
        raise HTTPException(
            status_code=400,
            detail="Request body is empty. Provide SQL text.",
        )

    if not body.lstrip().startswith("--"):
        raise HTTPException(
            status_code=400,
            detail="Invalid SQL: body must start with '--' (SQL comment header).",
        )

    registry = get_schema_registry()

    try:
        old_version, new_version = registry.update_schema(name, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Notify downstream services
    notifier = get_schema_notifier()
    notification_result = await notifier.notify_schema_change(
        name, old_version, new_version
    )

    logger.info(
        f"Schema uploaded: {name} v{old_version} -> v{new_version}"
    )

    return {
        "name": name,
        "old_version": old_version,
        "new_version": new_version,
        "notifications": notification_result,
    }


@router.post("/internal/reload-schemas", include_in_schema=False)
async def reload_schemas():
    """
    Trigger a full reload of all schemas from disk.

    Re-scans the schemas directory and updates in-memory state.
    For each schema that changed, sends notifications to downstream services.

    Returns:
        {reloaded: int, changes: [{name, old_version, new_version}]}
    """
    registry = get_schema_registry()
    changes = registry.reload_from_disk()

    if not changes:
        return {"reloaded": 0, "changes": []}

    notifier = get_schema_notifier()
    for schema_name, old_version, new_version in changes:
        await notifier.notify_schema_change(schema_name, old_version, new_version)

    return {
        "reloaded": len(changes),
        "changes": [
            {
                "name": name,
                "old_version": old_ver,
                "new_version": new_ver,
            }
            for name, old_ver, new_ver in changes
        ],
    }
