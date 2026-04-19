"""
Registry API Endpoints (Service Discovery + Credential Management)

POST /api/registry/register                           — Service self-registers
GET  /api/registry/discover                           — Full catalog
GET  /api/registry/discover/{service_name}            — Service-specific catalog
POST /api/registry/health/{instance_id}               — Report health
GET  /api/registry/config/{service_name}              — Get service config

POST /api/registry/credentials/generate               — Generate new API key
POST /api/registry/credentials/{credential_id}/rotate — Rotate key
POST /api/registry/credentials/{credential_id}/revoke — Revoke key
GET  /api/registry/credentials/{service_name}         — List credentials

HeartBeat is the sole gatekeeper for all registry operations.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ...handlers import registry_handler, credential_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registry", tags=["registry"])


# ── Request/Response Models ────────────────────────────────────────────

class EndpointEntry(BaseModel):
    """A single endpoint that a service exposes."""
    method: str = Field(..., description="HTTP method (GET, POST, etc.)")
    path: str = Field(..., description="URL path (e.g., /api/v1/upload)")
    description: str = Field("", description="Human-readable description")
    requires_auth: bool = Field(True, description="Whether this endpoint requires auth")


class RegisterRequest(BaseModel):
    """Service self-registration request."""
    service_instance_id: str = Field(..., min_length=1, max_length=100)
    service_name: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., min_length=1)
    health_url: Optional[str] = None
    websocket_url: Optional[str] = None
    version: str = Field("2.0.0", max_length=20)
    endpoints: List[EndpointEntry] = Field(default_factory=list)


class HealthReportRequest(BaseModel):
    """Health status report from a service instance."""
    status: str = Field(..., pattern="^(healthy|degraded|down)$")


class CredentialGenerateRequest(BaseModel):
    """Request to generate a new API credential."""
    service_name: str = Field(..., min_length=1, max_length=50)
    issued_to: str = Field(..., min_length=1, max_length=100)
    permissions: List[str] = Field(default_factory=list)
    expires_at: Optional[str] = Field(None, description="ISO-8601 expiry")


class CredentialRevokeRequest(BaseModel):
    """Request to revoke a credential."""
    reason: Optional[str] = None


# ── Registration & Discovery ───────────────────────────────────────────

@router.post(
    "/register",
    status_code=status.HTTP_200_OK,
)
async def register_service(request: RegisterRequest):
    """
    Register a service instance and its endpoints.

    Called by each service on startup. HeartBeat validates the request,
    stores the instance + endpoints, and returns the full service catalog
    so the registering service knows where all peers are.
    """
    try:
        result = await registry_handler.register_service(
            instance_id=request.service_instance_id,
            service_name=request.service_name,
            display_name=request.display_name,
            base_url=request.base_url,
            endpoints=[ep.model_dump() for ep in request.endpoints],
            health_url=request.health_url,
            websocket_url=request.websocket_url,
            version=request.version,
        )
        return result
    except Exception as e:
        logger.error(f"register_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get(
    "/discover",
    status_code=status.HTTP_200_OK,
)
async def discover_all(
    caller: Optional[str] = Query(
        None,
        description="Caller service name for access-filtered discovery",
    ),
):
    """
    Discover all active services and their endpoints.

    Returns the full catalog: every service, instance, and endpoint.
    Used by services on startup or after a failed inter-service call.

    If caller is provided, endpoints are filtered by access_control rules.
    """
    try:
        return await registry_handler.discover_all(caller_service=caller)
    except Exception as e:
        logger.error(f"discover_all failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get(
    "/discover/{service_name}",
    status_code=status.HTTP_200_OK,
)
async def discover_service(
    service_name: str,
    caller: Optional[str] = Query(
        None,
        description="Caller service name for access-filtered discovery",
    ),
):
    """
    Discover a specific service's instances and endpoints.

    Called when a service needs to find/re-find another service
    (e.g., after a failed API call for retry with fresh endpoint).

    If caller is provided, endpoints are filtered by access_control rules.
    """
    try:
        result = await registry_handler.discover_service(
            service_name, caller_service=caller,
        )
        if not result["instances"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "status": "not_found",
                    "message": f"No active instances for service: {service_name}",
                },
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"discover_service failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


# ── Health Reporting ───────────────────────────────────────────────────

@router.post(
    "/health/{instance_id}",
    status_code=status.HTTP_200_OK,
)
async def report_health(instance_id: str, request: HealthReportRequest):
    """Report health status for a service instance."""
    try:
        return await registry_handler.report_health(instance_id, request.status)
    except Exception as e:
        logger.error(f"report_health failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


# ── Service Config ─────────────────────────────────────────────────────

@router.get(
    "/config/{service_name}",
    status_code=status.HTTP_200_OK,
)
async def get_service_config(service_name: str):
    """Get configuration key-value pairs for a service."""
    try:
        return await registry_handler.get_service_config(service_name)
    except Exception as e:
        logger.error(f"get_service_config failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


# ── Credential Management (Admin) ─────────────────────────────────────

@router.post(
    "/credentials/generate",
    status_code=status.HTTP_201_CREATED,
)
async def generate_credential(request: CredentialGenerateRequest):
    """
    Generate a new API key/secret pair.

    Admin-only operation. Returns the plaintext secret ONE TIME ONLY.
    The service must store it securely — it cannot be retrieved later.
    """
    try:
        result = await credential_handler.create_credential(
            service_name=request.service_name,
            issued_to=request.issued_to,
            permissions=request.permissions,
            expires_at=request.expires_at,
        )
        return result
    except Exception as e:
        logger.error(f"generate_credential failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/credentials/{credential_id}/rotate",
    status_code=status.HTTP_200_OK,
)
async def rotate_credential(credential_id: str):
    """
    Rotate an API key (generate new key + secret, invalidate old).

    Returns new key + secret (one time only).
    """
    try:
        result = await credential_handler.rotate_credential(
            credential_id=credential_id,
            performed_by="admin-api",
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"rotate_credential failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.post(
    "/credentials/{credential_id}/revoke",
    status_code=status.HTTP_200_OK,
)
async def revoke_credential(credential_id: str, request: CredentialRevokeRequest):
    """Revoke an API credential (permanently disable)."""
    try:
        result = await credential_handler.revoke_credential(
            credential_id=credential_id,
            performed_by="admin-api",
            reason=request.reason,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"revoke_credential failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )


@router.get(
    "/credentials/{service_name}",
    status_code=status.HTTP_200_OK,
)
async def list_credentials(service_name: str):
    """
    List credentials for a service (WITHOUT secret hashes).

    Admin-only. Returns key IDs, statuses, permissions, and timestamps.
    """
    try:
        from ...database.registry import get_registry_database
        db = get_registry_database()
        creds = db.get_credentials_for_service(service_name)
        return {
            "service_name": service_name,
            "credentials": creds,
        }
    except Exception as e:
        logger.error(f"list_credentials failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": str(e)},
        )
