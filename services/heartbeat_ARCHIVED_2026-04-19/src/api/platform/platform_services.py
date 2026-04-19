"""
Platform Services API

Serves Transforma modules and FIRS service keys to downstream services
(Relay). Relay's TransformaModuleCache calls this endpoint at startup
and every 12 hours to refresh cached Python modules.

Endpoints:
    GET /api/platform/transforma/config — Full Transforma configuration
        Returns module source code + FIRS service keys.
        Auth: verify_service_credentials (Bearer api_key:api_secret)

Data source:
    config.db config_entries table with service_name="transforma":
    - config_key="irn_generator" → JSON: {module_name, source_code, version, checksum, updated_at}
    - config_key="qr_generator"  → JSON: {module_name, source_code, version, checksum, updated_at}
    - config_key="service_keys"  → JSON: {firs_public_key_pem, csid, csid_expires_at, certificate}
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ...auth.dependencies import verify_service_credentials
from ...handlers import platform_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform", tags=["platform"])


@router.get(
    "/transforma/config",
    summary="Get Transforma modules and FIRS service keys",
    response_description="Module source code + FIRS keys for IRN/QR generation",
)
async def get_transforma_config(
    credential: Dict[str, Any] = Depends(verify_service_credentials),
):
    """
    Fetch Transforma modules and FIRS service keys.

    Called by Relay's TransformaModuleCache at startup and every 12 hours.
    Returns Python source code for IRN/QR generators and FIRS keys
    (public key PEM, CSID, certificate) needed for QR code encryption.

    Requires service credentials (Bearer api_key:api_secret).
    Relay stores these as RELAY_HEARTBEAT_API_KEY / RELAY_HEARTBEAT_API_SECRET.

    Access control: Modules are filtered by the caller's service identity
    via the access_control table in config.db. Relay only gets qr_generator
    + service_keys. Core gets everything.

    Response matches Relay HeartBeatClient.get_transforma_config() contract:
    ```json
    {
        "modules": [
            {
                "module_name": "irn_generator",
                "source_code": "def generate_irn(...)...",
                "version": "1.0.0",
                "checksum": "sha256:abc123...",
                "updated_at": "2026-03-04T14:00:00Z"
            }
        ],
        "service_keys": {
            "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----...",
            "csid": "CSID-TOKEN",
            "csid_expires_at": "2026-06-01T00:00:00Z",
            "certificate": "base64-cert-data"
        }
    }
    ```
    """
    try:
        # Extract caller service identity from credential
        caller_service = credential.get("service_name")
        result = await platform_handler.get_transforma_config(
            caller_service=caller_service,
        )
        return result
    except Exception as e:
        logger.error(f"get_transforma_config failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to load Transforma configuration", "message": str(e)},
        )
