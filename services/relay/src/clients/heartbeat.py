"""
HeartBeat API Client

Real HTTP client for Helium HeartBeat service via httpx.AsyncClient.

HeartBeat is Relay's PRIMARY upstream — it handles:
    - Blob storage (write/read file data)
    - Deduplication (persistent hash check)
    - Daily usage limits (per-company quotas)
    - Blob registration and reconciliation
    - Audit logging (immutable, append-only event trail)
    - Metrics reporting (ingestion counts, processing times, error rates)
    - Service health monitoring (HeartBeat keeps services alive)
    - Platform services (Transforma module config)

Auth model:
    - Blob write/register: Optional Authorization: Bearer {user_jwt}
      (HeartBeat validates JWT in-process via Ed25519 if present)
    - Dedup, limits, audit, metrics: No auth (internal service calls)
    - Transforma config: Authorization: Bearer {api_key}:{api_secret}
    - Health: No auth

Audit logging and metrics are fire-and-forget: failures are logged
locally but NEVER block the main request flow.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .base import BaseClient
from ..errors import (
    HeartBeatUnavailableError,
    JWTRejectedError,
    TransientError,
)

logger = logging.getLogger(__name__)


class HeartBeatClient(BaseClient):
    """
    Client for Helium HeartBeat API.

    HeartBeat is Relay's primary upstream service. All persistent state,
    monitoring, and audit trails flow through HeartBeat.

    Endpoints:
        # Blob storage
        POST /api/blobs/write        → Write file to blob storage (multipart)
        POST /api/blobs/register     → Register blob metadata (JSON)

        # Deduplication
        GET  /api/dedup/check        → Check for duplicate hash
        POST /api/dedup/record       → Record processed hash

        # Limits
        GET  /api/limits/daily       → Check daily usage limit

        # Audit (immutable, append-only)
        POST /api/audit/log          → Log audit event

        # Metrics
        POST /api/metrics/report     → Report ingestion metrics

        # Platform
        GET  /api/platform/transforma/config → Transforma module config

        # Health
        GET  /health                 → Health check
    """

    def __init__(
        self,
        heartbeat_api_url: str = "http://localhost:9000",
        timeout: float = 30.0,
        max_attempts: int = 5,
        trace_id: Optional[str] = None,
        service_api_key: str = "",
        service_api_secret: str = "",
    ):
        super().__init__(
            max_attempts=max_attempts,
            timeout=timeout,
            trace_id=trace_id,
        )
        self.heartbeat_api_url = heartbeat_api_url.rstrip("/")
        self._service_api_key = service_api_key
        self._service_api_secret = service_api_secret

        # Shared httpx client — created lazily, closed explicitly
        self._http: Optional[httpx.AsyncClient] = None

        # Track calls for testing (preserved from stub era)
        self._calls: list = []
        # Track audit events for testing
        self._audit_events: List[Dict[str, Any]] = []

    def _get_http(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.heartbeat_api_url,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._http

    async def close(self) -> None:
        """Close the httpx client. Call on shutdown."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    def _raise_for_status(
        self, resp: httpx.Response, context: str
    ) -> None:
        """Raise appropriate RelayError for non-2xx HeartBeat responses."""
        if resp.is_success:
            return

        if resp.status_code == 401:
            raise JWTRejectedError(
                message=f"HeartBeat rejected JWT on {context}: {resp.text}"
            )

        if resp.status_code >= 500:
            raise TransientError(
                error_code="HEARTBEAT_SERVER_ERROR",
                message=f"HeartBeat {context} returned {resp.status_code}: {resp.text}",
            )

        # 4xx other than 401 — permanent error, wrap as HeartBeatUnavailable
        raise HeartBeatUnavailableError(
            message=f"HeartBeat {context} failed ({resp.status_code}): {resp.text}"
        )

    # ── Blob Storage ───────────────────────────────────────────────────────

    async def write_blob(
        self,
        blob_uuid: str,
        filename: str,
        file_data: bytes,
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Write file data to HeartBeat blob storage.

        Sends one POST /api/blobs/write per file as multipart:
            - blob_uuid (form field)
            - filename (form field)
            - file (binary upload)
            - metadata (JSON-encoded form field, optional)

        Optional Authorization: Bearer {jwt_token} header.
        HeartBeat validates JWT in-process (Ed25519) if present.

        Args:
            blob_uuid: Unique identifier for this blob.
            filename: Original filename.
            file_data: Raw file bytes.
            metadata: SDK identity/trace fields (JSON-encoded in form).
            jwt_token: Bearer JWT for user identity verification.

        Returns:
            {blob_uuid, blob_path, file_size_bytes, file_hash, status}

        Raises:
            JWTRejectedError: If HeartBeat returns 401 (bad JWT).
            TransientError: If HeartBeat returns 5xx.
            HeartBeatUnavailableError: If HeartBeat is unreachable.
        """
        async def _write():
            http = self._get_http()
            headers = self.get_trace_headers()
            if jwt_token:
                headers["Authorization"] = f"Bearer {jwt_token}"

            # Build multipart form
            form_data = {
                "blob_uuid": blob_uuid,
                "filename": filename,
            }
            if metadata:
                form_data["metadata"] = json.dumps(metadata)

            files_payload = {
                "file": (filename, file_data, "application/octet-stream"),
            }

            self._calls.append(("write_blob", blob_uuid, filename))

            try:
                resp = await http.post(
                    "/api/blobs/write",
                    data=form_data,
                    files=files_payload,
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat: {e}"
                ) from e

            self._raise_for_status(resp, "write_blob")

            logger.debug(
                f"HeartBeat write_blob — uuid={blob_uuid}, "
                f"file={filename}, size={len(file_data)}, "
                f"jwt={'yes' if jwt_token else 'no'}",
                extra={"trace_id": self.trace_id},
            )
            return resp.json()

        return await self.call_with_retries(_write)

    # ── Deduplication ──────────────────────────────────────────────────────

    async def check_duplicate(self, file_hash: str) -> Dict[str, Any]:
        """
        Check if a file hash has been seen before.

        GET /api/dedup/check?file_hash={64-char SHA256}

        Args:
            file_hash: SHA256 hex digest of file data.

        Returns:
            {is_duplicate, file_hash, original_queue_id}
        """
        async def _check():
            http = self._get_http()
            headers = self.get_trace_headers()

            self._calls.append(("check_duplicate", file_hash))

            try:
                resp = await http.get(
                    "/api/dedup/check",
                    params={"file_hash": file_hash},
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for dedup check: {e}"
                ) from e

            self._raise_for_status(resp, "check_duplicate")

            logger.debug(
                f"HeartBeat check_duplicate — hash={file_hash[:12]}...",
                extra={"trace_id": self.trace_id},
            )
            return resp.json()

        return await self.call_with_retries(_check)

    async def record_duplicate(
        self,
        file_hash: str,
        queue_id: str,
    ) -> Dict[str, Any]:
        """
        Record a file hash after successful processing.

        POST /api/dedup/record  JSON: {file_hash, queue_id}

        Args:
            file_hash: SHA256 hex digest.
            queue_id: Queue ID the file was processed under.

        Returns:
            {file_hash, queue_id, status}
        """
        async def _record():
            http = self._get_http()
            headers = self.get_trace_headers()

            self._calls.append(("record_duplicate", file_hash, queue_id))

            try:
                resp = await http.post(
                    "/api/dedup/record",
                    json={"file_hash": file_hash, "queue_id": queue_id},
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for dedup record: {e}"
                ) from e

            self._raise_for_status(resp, "record_duplicate")
            return resp.json()

        return await self.call_with_retries(_record)

    # ── Daily Limits ───────────────────────────────────────────────────────

    async def check_daily_limit(
        self,
        company_id: str,
        file_count: int = 1,
    ) -> Dict[str, Any]:
        """
        Check if company has exceeded daily upload limit.

        GET /api/limits/daily?company_id=&file_count=

        Args:
            company_id: Company identifier.
            file_count: Number of files in this request.

        Returns:
            {company_id, files_today, daily_limit, limit_reached, remaining}
        """
        async def _check():
            http = self._get_http()
            headers = self.get_trace_headers()

            self._calls.append(("check_daily_limit", company_id, file_count))

            try:
                resp = await http.get(
                    "/api/limits/daily",
                    params={
                        "company_id": company_id,
                        "file_count": file_count,
                    },
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for daily limit: {e}"
                ) from e

            self._raise_for_status(resp, "check_daily_limit")
            return resp.json()

        return await self.call_with_retries(_check)

    # ── Blob Registration ──────────────────────────────────────────────────

    async def register_blob(
        self,
        blob_uuid: str,
        filename: str,
        file_size_bytes: int,
        file_hash: str,
        api_key: str,
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register blob metadata in HeartBeat (non-critical, fire-and-forget).

        POST /api/blobs/register  JSON body.
        Failure is logged but does NOT block the upload.

        Args:
            blob_uuid: Blob UUID.
            filename: Original filename.
            file_size_bytes: File size.
            file_hash: SHA256 hash.
            api_key: API key used for upload.
            metadata: SDK identity/trace fields.
            jwt_token: Bearer JWT for user identity verification.

        Returns:
            {blob_uuid, status, tracking_id}
        """
        try:
            async def _register():
                http = self._get_http()
                headers = self.get_trace_headers()
                if jwt_token:
                    headers["Authorization"] = f"Bearer {jwt_token}"

                payload = {
                    "blob_uuid": blob_uuid,
                    "filename": filename,
                    "file_size_bytes": file_size_bytes,
                    "file_hash": file_hash,
                    "api_key": api_key,
                }
                if metadata:
                    payload["metadata"] = metadata

                self._calls.append(("register_blob", blob_uuid))

                try:
                    resp = await http.post(
                        "/api/blobs/register",
                        json=payload,
                        headers=headers,
                    )
                except httpx.ConnectError as e:
                    raise HeartBeatUnavailableError(
                        message=f"Cannot connect to HeartBeat for blob register: {e}"
                    ) from e

                self._raise_for_status(resp, "register_blob")
                return resp.json()

            return await self.call_with_retries(_register)

        except Exception as e:
            # Non-critical — log and continue
            logger.warning(
                f"HeartBeat register_blob failed (non-critical): {e}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "blob_uuid": blob_uuid,
                "status": "registration_failed",
                "error": str(e),
            }

    # ── Tenant Config ─────────────────────────────────────────────────────

    async def fetch_config(self) -> Dict[str, Any]:
        """
        Fetch full tenant config from HeartBeat.

        GET /api/v1/heartbeat/config
        Auth: Bearer {api_key}:{api_secret}

        Returns full config dict (tenant, firs, endpoints, tier_limits, etc.)
        Used at startup to populate ConfigCache.

        Raises:
            HeartBeatUnavailableError: If HeartBeat is unreachable.
            TransientError: If HeartBeat returns 5xx.
        """
        async def _fetch():
            http = self._get_http()
            headers = self.get_trace_headers()

            # Use service credentials for config endpoint
            if self._service_api_key and self._service_api_secret:
                headers["Authorization"] = (
                    f"Bearer {self._service_api_key}:{self._service_api_secret}"
                )

            self._calls.append(("fetch_config",))

            try:
                resp = await http.get(
                    "/api/v1/heartbeat/config",
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for config fetch: {e}"
                ) from e

            self._raise_for_status(resp, "fetch_config")

            logger.info(
                "HeartBeat config fetched successfully",
                extra={"trace_id": self.trace_id},
            )
            return resp.json()

        return await self.call_with_retries(_fetch)

    # ── Health ─────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check if HeartBeat API is healthy. GET /health."""
        try:
            http = self._get_http()
            resp = await asyncio.wait_for(
                http.get("/health"),
                timeout=5.0,
            )
            return resp.is_success
        except Exception:
            return False

    # ── Audit Logging (fire-and-forget) ────────────────────────────────────

    async def audit_log(
        self,
        service: str,
        event_type: str,
        user_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an immutable audit event to HeartBeat (fire-and-forget).

        POST /api/audit/log  JSON: {service, event_type, user_id, details,
                                     trace_id, ip_address}

        Failures are logged locally but NEVER block the main request flow.

        Args:
            service: Service name (e.g., "relay-api").
            event_type: Event type (e.g., "file.ingested").
            user_id: Optional user identifier.
            details: Optional event details dict.
        """
        event = {
            "service": service,
            "event_type": event_type,
            "user_id": user_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
        }

        try:
            self._audit_events.append(event)
            self._calls.append(("audit_log", event_type))

            http = self._get_http()
            headers = self.get_trace_headers()

            payload = {
                "service": service,
                "event_type": event_type,
                "user_id": user_id,
                "details": details or {},
                "trace_id": self.trace_id,
            }

            try:
                resp = await http.post(
                    "/api/audit/log",
                    json=payload,
                    headers=headers,
                )
                if not resp.is_success:
                    logger.warning(
                        f"Audit log HTTP {resp.status_code}: {resp.text}",
                        extra={"trace_id": self.trace_id},
                    )
            except httpx.ConnectError:
                logger.warning(
                    "Audit log failed — HeartBeat unreachable",
                    extra={"trace_id": self.trace_id},
                )

            logger.info(
                f"Audit: {service}/{event_type}",
                extra={
                    "trace_id": self.trace_id,
                    "event_type": event_type,
                    "user_id": user_id,
                },
            )

        except Exception as e:
            # NEVER raise — audit failures must not block the main flow
            logger.warning(
                f"Audit log failed (non-critical): {e}",
                extra={"trace_id": self.trace_id},
            )

    # ── Metrics Reporting (fire-and-forget) ────────────────────────────────

    async def report_metrics(
        self,
        metric_type: str,
        values: Dict[str, Any],
    ) -> None:
        """
        Report operational metrics to HeartBeat (fire-and-forget).

        POST /api/metrics/report  JSON: {metric_type, values, reported_by}

        Failures are logged locally but NEVER block the main request flow.

        Args:
            metric_type: Metric category (e.g., "ingestion", "error").
            values: Metric values dict.
        """
        try:
            self._calls.append(("report_metrics", metric_type))

            http = self._get_http()
            headers = self.get_trace_headers()

            payload = {
                "metric_type": metric_type,
                "values": values,
                "reported_by": "relay-api",
            }

            try:
                resp = await http.post(
                    "/api/metrics/report",
                    json=payload,
                    headers=headers,
                )
                if not resp.is_success:
                    logger.warning(
                        f"Metrics report HTTP {resp.status_code}: {resp.text}",
                        extra={"trace_id": self.trace_id},
                    )
            except httpx.ConnectError:
                logger.warning(
                    "Metrics report failed — HeartBeat unreachable",
                    extra={"trace_id": self.trace_id},
                )

            logger.debug(
                f"Metrics: {metric_type} — {values}",
                extra={"trace_id": self.trace_id},
            )

        except Exception as e:
            # NEVER raise — metrics failures must not block the main flow
            logger.warning(
                f"Metrics report failed (non-critical): {e}",
                extra={"trace_id": self.trace_id},
            )

    # ── Transforma Module Cache ────────────────────────────────────────────

    async def get_transforma_config(self) -> Dict[str, Any]:
        """
        Fetch Transforma modules and FIRS service keys from HeartBeat.

        GET /api/platform/transforma/config
        Authorization: Bearer {api_key}:{api_secret}

        Called by TransformaModuleCache at startup and every 12 hours.

        Returns:
            {modules: [...], service_keys: {...}}
        """
        async def _get_config():
            http = self._get_http()
            headers = self.get_trace_headers()

            # Service credentials for platform endpoint
            if self._service_api_key and self._service_api_secret:
                headers["Authorization"] = (
                    f"Bearer {self._service_api_key}:{self._service_api_secret}"
                )

            self._calls.append(("get_transforma_config",))

            try:
                resp = await http.get(
                    "/api/platform/transforma/config",
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for transforma config: {e}"
                ) from e

            self._raise_for_status(resp, "get_transforma_config")

            logger.debug(
                "HeartBeat get_transforma_config — success",
                extra={"trace_id": self.trace_id},
            )
            return resp.json()

        return await self.call_with_retries(_get_config)

    # ── Test Helpers ───────────────────────────────────────────────────────

    @property
    def audit_events(self) -> List[Dict[str, Any]]:
        """Get recorded audit events (for testing)."""
        return self._audit_events

    def clear_audit_events(self) -> None:
        """Clear recorded audit events (for testing)."""
        self._audit_events.clear()
