"""
HeartBeat Blob Client

Pure HTTP client for fetching blob file bytes from HeartBeat.
No filesystem knowledge — all access via HTTP.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from src.errors import ExternalServiceError, NotFoundError, TimeoutError
from src.ingestion.models import BlobResponse

logger = structlog.get_logger()

# Retry constants
_MAX_RETRIES = 3
_RETRYABLE_STATUS = {500, 502, 503}


class HeartBeatBlobClient:
    """Fetch blob file bytes from HeartBeat over HTTP."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_connections=20),
        )
        self._api_key = api_key

    async def upload_blob(
        self,
        blob_uuid: str,
        filename: str,
        data: bytes,
        content_type: str = "application/x-helium-exchange",
        company_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Upload file bytes to HeartBeat blob store via POST /api/blobs/write.

        Args:
            blob_uuid: UUID for the blob.
            filename: Original filename.
            data: Raw file bytes to upload.
            content_type: MIME type.
            company_id: Owning tenant.
            metadata: Optional extra metadata dict.

        Returns:
            blob_uuid on success.

        Raises:
            ExternalServiceError: If HeartBeat is unreachable or returns error.
        """
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        files = {"file": (filename, data, content_type)}
        form_data: dict[str, str] = {"blob_uuid": blob_uuid}
        if company_id:
            form_data["company_id"] = company_id
        if metadata:
            import json
            form_data["metadata"] = json.dumps(metadata)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.post(
                    "/api/blobs/write",
                    files=files,
                    data=form_data,
                    headers=headers,
                )
                if response.status_code in (200, 201):
                    logger.info(
                        "blob_uploaded",
                        blob_uuid=blob_uuid,
                        filename=filename,
                        size=len(data),
                    )
                    return blob_uuid
                if response.status_code not in _RETRYABLE_STATUS:
                    raise ExternalServiceError(
                        message=f"HeartBeat upload rejected: {response.status_code}",
                        details=[{"blob_uuid": blob_uuid, "status": response.status_code}],
                    )
                last_error = ExternalServiceError(
                    message=f"HeartBeat returned {response.status_code}",
                    details=[{"blob_uuid": blob_uuid}],
                )
                await asyncio.sleep(2 ** attempt)
            except ExternalServiceError:
                raise
            except httpx.TimeoutException:
                last_error = ExternalServiceError(
                    message=f"HeartBeat upload timed out (attempt {attempt + 1})",
                    details=[{"blob_uuid": blob_uuid}],
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
            except httpx.HTTPError as exc:
                last_error = ExternalServiceError(
                    message=f"HeartBeat upload failed: {exc}",
                    details=[{"blob_uuid": blob_uuid}],
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        raise last_error or ExternalServiceError(
            message="HeartBeat blob upload failed",
            details=[{"blob_uuid": blob_uuid}],
        )

    async def fetch_blob(self, blob_uuid: str) -> BlobResponse:
        """
        Download file bytes from HeartBeat.

        GET /api/blobs/{blob_uuid}/download

        Retries 3x with exponential backoff on 500/502/503/timeout.
        Raises NotFoundError on 404/410 (no retry).
        Raises ExternalServiceError after exhausting retries.
        """
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.get(
                    f"/api/blobs/{blob_uuid}/download",
                    headers=headers,
                )

                if resp.status_code == 404:
                    raise NotFoundError(f"Blob {blob_uuid} not found in HeartBeat")
                if resp.status_code == 410:
                    raise NotFoundError(f"Blob {blob_uuid} is in error state")

                if resp.status_code in _RETRYABLE_STATUS:
                    last_error = ExternalServiceError(
                        f"HeartBeat returned {resp.status_code}"
                    )
                    await asyncio.sleep(2**attempt)
                    continue

                resp.raise_for_status()

                # Extract metadata from response headers
                content_type = resp.headers.get("content-type", "application/octet-stream")
                filename = ""
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    filename = cd.split("filename=")[-1].strip('"')

                return BlobResponse(
                    content=resp.content,
                    content_type=content_type,
                    filename=filename,
                    size=len(resp.content),
                    blob_hash=resp.headers.get("x-blob-hash", ""),
                )

            except (NotFoundError, ExternalServiceError):
                raise
            except httpx.TimeoutException:
                last_error = TimeoutError(f"HeartBeat blob fetch timed out (attempt {attempt + 1})")
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)
            except httpx.ConnectError as e:
                last_error = ExternalServiceError(
                    f"Cannot connect to HeartBeat: {e}"
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2**attempt)

        raise last_error or ExternalServiceError("HeartBeat blob fetch failed")

    async def fetch_config(self) -> dict:
        """
        Fetch full tenant config from HeartBeat config.db.

        GET /api/v1/heartbeat/config

        Retries 3x with exponential backoff on 5xx.
        Raises ExternalServiceError on final failure.
        """
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.get(
                    "/api/v1/heartbeat/config",
                    headers=headers,
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in _RETRYABLE_STATUS:
                    last_error = ExternalServiceError(
                        f"HeartBeat config returned {resp.status_code}"
                    )
                    await asyncio.sleep(2 ** attempt)
                    continue

                raise ExternalServiceError(
                    f"HeartBeat config fetch failed: {resp.status_code}"
                )

            except ExternalServiceError:
                raise
            except httpx.TimeoutException:
                last_error = ExternalServiceError(
                    f"HeartBeat config fetch timed out (attempt {attempt + 1})"
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
            except httpx.HTTPError as exc:
                last_error = ExternalServiceError(
                    f"HeartBeat config fetch failed: {exc}"
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        raise last_error or ExternalServiceError("HeartBeat config fetch failed")

    async def update_blob_status(
        self,
        blob_uuid: str,
        status: str,
        processing_stage: str | None = None,
        error_message: str | None = None,
        processing_stats: dict | None = None,
    ) -> dict | None:
        """
        Update blob processing status on HeartBeat.

        POST /api/v1/heartbeat/blob/{blob_uuid}/status

        Retries 3x with exponential backoff on 5xx.
        Non-fatal: returns None and logs warning on final failure.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict = {"status": status}
        if processing_stage:
            payload["processing_stage"] = processing_stage
        if error_message:
            payload["error_message"] = error_message
        if processing_stats:
            payload.update(processing_stats)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.post(
                    f"/api/v1/heartbeat/blob/{blob_uuid}/status",
                    json=payload,
                    headers=headers,
                )

                if resp.status_code in (200, 201, 204):
                    logger.info(
                        "heartbeat_status_updated",
                        blob_uuid=blob_uuid,
                        status=status,
                        processing_stage=processing_stage,
                    )
                    return resp.json() if resp.content else {}

                if resp.status_code not in _RETRYABLE_STATUS:
                    logger.warning(
                        "heartbeat_status_rejected",
                        blob_uuid=blob_uuid,
                        status_code=resp.status_code,
                    )
                    return None

                last_error = ExternalServiceError(
                    f"HeartBeat status returned {resp.status_code}"
                )
                await asyncio.sleep(2 ** attempt)

            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        logger.warning(
            "heartbeat_status_failed",
            blob_uuid=blob_uuid,
            status=status,
            error=str(last_error),
        )
        return None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
