"""
Satellite API — Proxy Endpoints (Q6)

Endpoints exposed by HeartBeat in Satellite mode. Pure proxy — Satellite
stores nothing locally, forwards everything to Primary.

Endpoints:
    POST /satellite/blobs/write     — Forward blob write to Primary
    POST /satellite/blobs/register  — Forward blob registration to Primary
    GET  /satellite/health          — Local health + Primary connectivity
    GET  /satellite/config/{service}/{key} — Fetch config from Primary (cached)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from ...config import get_config
from ...clients.primary_client import get_primary_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/satellite", tags=["Satellite — Proxy"])


def _require_satellite():
    """Raise 403 if not in Satellite mode."""
    config = get_config()
    if not config.is_satellite:
        raise HTTPException(
            status_code=403,
            detail="This endpoint is only available in Satellite mode",
        )


def _get_client():
    """Get PrimaryClient, raising 503 if not initialized."""
    client = get_primary_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Primary client not initialized. Check HEARTBEAT_PRIMARY_URL config.",
        )
    return client


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/blobs/write")
async def proxy_blob_write(
    file: UploadFile = File(...),
    source: str = Form("satellite"),
    company_id: str = Form("default"),
):
    """
    Forward blob write to Primary.

    Reads the uploaded file and sends it to Primary's /api/blobs/write.
    """
    _require_satellite()
    client = _get_client()

    try:
        file_data = await file.read()
        result = await client.forward_blob_write(
            file_data=file_data,
            filename=file.filename or "unknown",
            content_type=file.content_type or "application/octet-stream",
            source=source,
            company_id=company_id,
        )
        return result
    except Exception as e:
        logger.error(f"Blob write proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Primary unreachable: {str(e)}")


@router.post("/blobs/register")
async def proxy_blob_register(blob_data: dict):
    """Forward blob registration to Primary."""
    _require_satellite()
    client = _get_client()

    try:
        result = await client.forward_blob_register(blob_data)
        return result
    except Exception as e:
        logger.error(f"Blob register proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Primary unreachable: {str(e)}")


@router.get("/health")
async def satellite_health():
    """
    Satellite health — local status + Primary connectivity.

    Returns:
        local: always "ok" (if this endpoint responds, Satellite is up)
        primary: result of Primary health check (or "unreachable")
        mode: "satellite"
    """
    _require_satellite()
    config = get_config()

    primary_health = {"status": "not_configured"}
    client = get_primary_client()
    if client:
        primary_health = await client.health_check()

    return {
        "mode": "satellite",
        "local": "ok",
        "primary_url": config.primary_url,
        "primary": primary_health,
    }


@router.get("/config/{service}/{key}")
async def proxy_config(service: str, key: str):
    """
    Fetch config from Primary.

    Satellite has no local config.db — all config comes from Primary.
    """
    _require_satellite()
    client = _get_client()

    try:
        result = await client.get_config(service, key)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Config not found: {service}/{key}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Config proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Primary unreachable: {str(e)}")
