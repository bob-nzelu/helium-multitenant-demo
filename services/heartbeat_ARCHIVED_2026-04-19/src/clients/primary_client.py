"""
Primary Client — Satellite→Primary HTTP communication (Q6)

Used by HeartBeat in Satellite mode to forward requests to Primary.
All blob writes, registrations, and config lookups proxy through this.

Usage:
    client = get_primary_client()
    result = await client.forward_blob_write(file_data, metadata)
    config = await client.get_config(key)
"""

import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PrimaryClient:
    """
    HTTP client for Satellite→Primary communication.

    All methods are async. Uses httpx.AsyncClient with connection pooling.
    Timeouts: 10s connect, 60s for blob writes (large files), 10s for reads.
    """

    def __init__(self, primary_url: str, timeout_connect: float = 10.0):
        self.primary_url = primary_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.primary_url,
            timeout=httpx.Timeout(connect=timeout_connect, read=60.0, write=60.0, pool=10.0),
        )
        logger.info(f"PrimaryClient initialized → {self.primary_url}")

    async def health_check(self) -> Dict[str, Any]:
        """Check Primary health. Returns Primary's /health response."""
        try:
            resp = await self._client.get("/health")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Primary health check failed: {e}")
            return {"status": "unreachable", "error": str(e)}

    async def forward_blob_write(
        self,
        file_data: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        source: str = "satellite",
        company_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Forward a blob write to Primary's /api/blobs/write endpoint.

        Returns Primary's response (blob_uuid, object_name, etc.).
        """
        resp = await self._client.post(
            "/api/blobs/write",
            files={"file": (filename, file_data, content_type)},
            data={"source": source, "company_id": company_id},
        )
        resp.raise_for_status()
        return resp.json()

    async def forward_blob_register(
        self, blob_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Forward blob metadata registration to Primary."""
        resp = await self._client.post("/api/blobs/register", json=blob_data)
        resp.raise_for_status()
        return resp.json()

    async def get_config(self, service: str, key: str) -> Optional[Dict[str, Any]]:
        """Fetch config value from Primary's config API."""
        try:
            resp = await self._client.get(f"/api/config/{service}/{key}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Config fetch failed ({service}/{key}): {e}")
            return None

    async def send_heartbeat(
        self, satellite_id: str, status: str = "ok"
    ) -> Dict[str, Any]:
        """
        Send heartbeat ping to Primary.

        Primary uses this to track satellite liveness.
        """
        resp = await self._client.post(
            f"/primary/satellites/{satellite_id}/heartbeat",
            json={"status": status},
        )
        resp.raise_for_status()
        return resp.json()

    async def register_satellite(
        self,
        satellite_id: str,
        display_name: str,
        base_url: str,
        region: Optional[str] = None,
        version: str = "2.0.0",
    ) -> Dict[str, Any]:
        """Register this Satellite with Primary."""
        resp = await self._client.post(
            "/primary/satellites/register",
            json={
                "satellite_id": satellite_id,
                "display_name": display_name,
                "base_url": base_url,
                "region": region,
                "version": version,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()


# ── Singleton ──────────────────────────────────────────────────────────

_primary_client_instance: Optional[PrimaryClient] = None


def get_primary_client() -> Optional[PrimaryClient]:
    """Get singleton PrimaryClient (None if not initialized)."""
    return _primary_client_instance


def init_primary_client(primary_url: str) -> PrimaryClient:
    """Initialize singleton PrimaryClient."""
    global _primary_client_instance
    _primary_client_instance = PrimaryClient(primary_url)
    return _primary_client_instance


def set_primary_client(client: Optional[PrimaryClient]) -> None:
    """Override PrimaryClient singleton (for testing)."""
    global _primary_client_instance
    _primary_client_instance = client


def reset_primary_client() -> None:
    """Reset PrimaryClient singleton (for testing)."""
    global _primary_client_instance
    _primary_client_instance = None
