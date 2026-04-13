"""
Registry Handler — Service Registration + Discovery

Handles dynamic service registration, endpoint discovery, health reporting,
and service config retrieval.

Flow:
  1. Service starts → calls register_service() with its endpoints + API key
  2. HeartBeat validates API key, upserts instance + endpoints
  3. Returns full catalog so the registering service knows all peers
"""

import logging
from typing import Any, Dict, List, Optional

from ..database.registry import get_registry_database

logger = logging.getLogger(__name__)


async def register_service(
    instance_id: str,
    service_name: str,
    display_name: str,
    base_url: str,
    endpoints: List[Dict[str, Any]],
    health_url: Optional[str] = None,
    websocket_url: Optional[str] = None,
    version: str = "2.0.0",
    tier: str = "test",
) -> Dict[str, Any]:
    """
    Register or re-register a service instance with its endpoints.

    Returns:
        {
            "status": "registered",
            "instance": { ... },
            "endpoints_registered": int,
            "catalog": [ ... ]  # Full discovery catalog for all services
        }
    """
    db = get_registry_database()

    # Upsert the instance
    db.register_instance(
        instance_id=instance_id,
        service_name=service_name,
        display_name=display_name,
        base_url=base_url,
        health_url=health_url,
        websocket_url=websocket_url,
        version=version,
        tier=tier,
    )

    # Replace endpoints
    ep_count = db.register_endpoints(instance_id, endpoints)

    # Fetch the registered instance back
    instance = db.get_instance(instance_id)

    # Return full catalog so the service knows all peers
    catalog = db.get_full_catalog()

    logger.info(
        f"Registered {instance_id} ({service_name}) at {base_url} "
        f"with {ep_count} endpoints"
    )

    return {
        "status": "registered",
        "instance": instance,
        "endpoints_registered": ep_count,
        "catalog": catalog,
    }


async def discover_service(
    service_name: str,
    caller_service: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Discover all active instances and endpoints for a service.

    When caller_service is provided, filters endpoints by access_control
    rules. When None (admin/internal), returns full catalog.

    Returns:
        {
            "service_name": "relay",
            "instances": [ { instance_id, base_url, health_url, ... } ],
            "endpoints": [ { method, path, description, base_url, ... } ]
        }
    """
    db = get_registry_database()

    instances = db.get_instances_by_service(service_name)
    if not instances:
        return {
            "service_name": service_name,
            "instances": [],
            "endpoints": [],
        }

    endpoints = db.get_endpoint_catalog(service_name)

    # Apply access control filtering if caller is identified
    if caller_service:
        endpoints = _filter_endpoints_by_access(caller_service, endpoints)

    return {
        "service_name": service_name,
        "instances": instances,
        "endpoints": endpoints,
    }


async def discover_all(
    caller_service: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Discover all active services and their endpoints.

    When caller_service is provided, filters endpoints by access_control
    rules. When None (admin/internal), returns full catalog.

    Returns:
        {
            "services": { "relay": {...}, "core": {...}, ... },
            "catalog": [ full endpoint catalog ]
        }
    """
    db = get_registry_database()

    all_instances = db.get_all_instances()
    catalog = db.get_full_catalog()

    # Apply access control filtering if caller is identified
    if caller_service:
        catalog = _filter_endpoints_by_access(caller_service, catalog)

    # Group instances by service_name
    services = {}
    for inst in all_instances:
        svc = inst["service_name"]
        if svc not in services:
            services[svc] = {
                "service_name": svc,
                "instances": [],
            }
        services[svc]["instances"].append(inst)

    return {
        "services": services,
        "catalog": catalog,
    }


def _filter_endpoints_by_access(
    caller_service: str,
    endpoints: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Filter endpoint catalog by caller's access_control rules.

    Checks access_control table for resource_type="endpoint" entries.
    If the caller has wildcard '*' access, all endpoints are returned.
    If no rules exist for the caller, all endpoints are returned (fail open).
    """
    try:
        from ..database.config_db import get_config_database
        config_db = get_config_database()
        allowed = config_db.get_allowed_resources(
            caller_service, "endpoint"
        )
    except Exception:
        # access_control table may not exist yet — fail open
        return endpoints

    if not allowed:
        # No rules defined — return all (fail open)
        return endpoints

    if "*" in allowed:
        return endpoints

    # Filter endpoints by allowed paths
    return [
        ep for ep in endpoints
        if ep.get("path") in allowed
    ]


async def report_health(instance_id: str, status: str) -> Dict[str, Any]:
    """
    Report health status for an instance.

    Returns:
        {"instance_id": ..., "status": ..., "updated": True/False}
    """
    db = get_registry_database()
    updated = db.update_health_status(instance_id, status)

    if updated:
        logger.info(f"Health update: {instance_id} -> {status}")
    else:
        logger.warning(f"Health update failed: instance {instance_id} not found")

    return {
        "instance_id": instance_id,
        "status": status,
        "updated": updated > 0,
    }


async def get_service_config(service_name: str) -> Dict[str, Any]:
    """Get all config key-values for a service."""
    db = get_registry_database()
    config = db.get_all_config(service_name)

    return {
        "service_name": service_name,
        "config": config,
    }


async def deactivate_service(instance_id: str) -> Dict[str, Any]:
    """Mark a service instance as inactive."""
    db = get_registry_database()
    updated = db.deactivate_instance(instance_id)

    if updated:
        logger.info(f"Deactivated instance: {instance_id}")

    return {
        "instance_id": instance_id,
        "deactivated": updated > 0,
    }
