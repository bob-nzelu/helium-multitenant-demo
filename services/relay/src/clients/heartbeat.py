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

Auth model (post-HMAC-cutover 2026-05-09 per HMAC_S2S_MIGRATION_SPEC.md
+ RELAY_NEXT_STEPS_NOTE_2026_05_09 §1):
    - Blob write/register: Optional ``Authorization: Bearer {user_jwt}``
      (HeartBeat validates JWT in-process via Ed25519 if present).
    - Dedup check, daily limits, tenant config, transforma config: §3.3
      HMAC-SHA256 service-to-service auth. Replaces the legacy
      ``Authorization: Bearer {api_key}:{api_secret}`` form which
      HeartBeat now rejects with ``401 BEARER_S2S_REMOVED`` (locked
      decision L5, 2026-05-08). Headers built via the
      ``build_s2s_hmac_headers()`` helper.
    - Audit log: HMAC headers added proactively per RELAY_NEXT_STEPS_NOTE
      §4.2 ("wire your audit emission via the same helper now so you
      get auth-enabled-for-free later" once D-audit fix lands). HB
      currently ignores the headers; harmless.
    - Metrics report: No auth (not in §1.5 migration list).
      Follow-up chip if HB tightens auth on this endpoint.
    - Health: No auth.

Audit logging and metrics are fire-and-forget: failures are logged
locally but NEVER block the main request flow.

Body-bytes discipline (HMAC_S2S_MIGRATION_SPEC §8.1 step 3): every
HMAC-signed call serialises the JSON payload to bytes ONCE, signs
those bytes, then sends them via ``content=...`` (NOT ``json=...``)
so the wire matches what we signed. Never sign a Python dict and
serialise separately.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

import httpx

from .base import BaseClient
from ._s2s_hmac import build_s2s_hmac_headers
from ..errors import (
    HeartBeatUnavailableError,
    JWTRejectedError,
    TransientError,
)
from ..observability import counters

logger = logging.getLogger(__name__)


# CSSV1 R9.3 — alarm signal. If HB ever returns this code from a Relay
# call, a Relay code path is still sending the dead Bearer s2s form.
# Rate >0 after Phase 0 catchup (2026-05-09) is a regression.
_BEARER_REMOVED_CODE = "BEARER_S2S_REMOVED"


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
        POST /api/dedup/check        → Check for duplicate hash
        # (record_duplicate removed in CSSV1 S4 R7 — HB writes the
        #  blob.blob_deduplication row as a side effect of /api/blobs/register)

        # Limits
        POST /api/limits/daily/check → Check daily usage limit

        # Audit (immutable, append-only)
        POST /api/audit/log          → Log audit event

        # Metrics
        POST /api/metrics/report     → Report ingestion metrics

        # Tenant config
        POST /api/v1/heartbeat/config → Full tenant config bundle

        # Platform
        POST /api/platform/transforma/config → Transforma module config

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
        service_signing_key: str = "",
    ):
        super().__init__(
            max_attempts=max_attempts,
            timeout=timeout,
            trace_id=trace_id,
        )
        self.heartbeat_api_url = heartbeat_api_url.rstrip("/")
        self._service_api_key = service_api_key
        # ``service_api_secret`` retained for backwards compat / config
        # readers that still reference it. The bcrypt'd hash is no longer
        # used by HB on the request path post-HMAC cutover.
        self._service_api_secret = service_api_secret
        # ``service_signing_key`` = the per-service HMAC signing key from
        # HB's startup WARNING log (``RELAY_S2S_SIGNING_KEY`` env var).
        # 64-hex chars (32 bytes). Required for every Relay→HB call that
        # hits a ``verify_service_credentials``-protected endpoint.
        self._service_signing_key = service_signing_key

        # Shared httpx client — created lazily, closed explicitly
        self._http: Optional[httpx.AsyncClient] = None

        # Track calls for testing (preserved from stub era)
        self._calls: list = []
        # Track audit events for testing
        self._audit_events: List[Dict[str, Any]] = []

    # ── HMAC s2s helper ────────────────────────────────────────────────────

    def _s2s_headers(
        self,
        *,
        method: str,
        path: str,
        body_bytes: bytes,
        content_type: str = "application/json",
    ) -> Dict[str, str]:
        """Build the headers for an HMAC-signed Relay→HB request.

        Combines the four canonical HMAC headers (per
        ``HMAC_S2S_MIGRATION_SPEC.md`` §2) with the trace headers from
        ``BaseClient.get_trace_headers()`` and an explicit
        ``Content-Type``. The caller passes ``body_bytes`` — the EXACT
        bytes that will be sent on the wire (per §8.1 step 3
        body-bytes discipline) — and uses ``content=body_bytes`` on the
        httpx request.
        """
        headers = dict(self.get_trace_headers())
        headers.update(
            build_s2s_hmac_headers(
                method=method,
                path=path,
                body_bytes=body_bytes,
                api_key=self._service_api_key,
                signing_key=self._service_signing_key,
            )
        )
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _json_bytes(payload: Optional[Dict[str, Any]]) -> bytes:
        """Serialise a JSON payload to bytes ONCE.

        Empty/None payload → ``b""`` so the caller can sign an empty
        body the same way HB verifies it (``sha256(b"")`` per spec §2.2,
        not the SHA of the literal string ``""``).
        """
        if not payload:
            return b""
        return json.dumps(payload).encode("utf-8")

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
            self._check_bearer_removed_alarm(resp, context)
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

    def _check_bearer_removed_alarm(
        self, resp: httpx.Response, context: str
    ) -> None:
        """Per CSSV1 R9.3 — fire the alarm if HB returns BEARER_S2S_REMOVED.

        HB returns ``401`` with JSON ``{"error_code": "BEARER_S2S_REMOVED"}``
        when the caller is still sending the dead ``Authorization: Bearer
        api_key:api_secret`` form. Post-Phase-0 (2026-05-09) Relay should
        NEVER trigger this — every Relay→HB call goes through
        :func:`_s2s_headers`. A non-zero rate on
        ``relay_bearer_removed_received_total`` means a code path slipped
        back onto the legacy form; ops alarms.

        ERROR-level log + counter increment. Doesn't change the raised
        exception (still :class:`JWTRejectedError`) — this is purely a
        side-channel signal.
        """
        try:
            body = resp.json()
        except Exception:
            return  # not JSON — definitely not the signed BEARER_S2S_REMOVED shape
        if not isinstance(body, dict):
            return
        if body.get("error_code") != _BEARER_REMOVED_CODE:
            return

        counters.inc(
            "relay_bearer_removed_received_total",
            labels={"endpoint": context},
        )
        logger.error(
            "BEARER_S2S_REMOVED received from HeartBeat — Relay sent the "
            "dead Bearer s2s form on %s. This is a regression: every "
            "Relay→HB call should go through build_s2s_hmac_headers(). "
            "Find the offending caller in the trace and migrate it. "
            "(Rate alarm: relay_bearer_removed_received_total{endpoint=%r}.)",
            context,
            context,
            extra={"trace_id": self.trace_id},
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

    # ── Blob Fetch (§B-RelayArtifactFetch) ─────────────────────────────────

    async def fetch_blob(
        self,
        artifact_ref: str,
        jwt_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch raw blob bytes from HeartBeat blob storage by reference.

        BACKEND CONTRACT (CLAUDE.md "Backend Debt Notes" §B-RelayArtifactFetch +
        debt-map L159-160): HeartBeat owns blob storage; Relay's artifact-fetch
        route is a thin authenticated proxy to HB blob bytes for HARD artifacts
        (signed / fixed / original / backend PDF, QR blob, signature). Relay
        calls this with its OWN HB *service* credentials (HMAC s2s) — the
        original ERP/user only holds the Relay ingest key, not HB-service creds
        (mirrors the ``downstream_auth_header`` model in ``deps._verify_hmac``).

        NEEDS-HB: HB must expose a **POST-body** blob-fetch-by-ref endpoint.
        ``artifact_ref`` is a bearer capability — it MUST NOT travel in a URL
        (consistent with HB's own audit M-1 "content identifier must not appear
        in URLs/logs", already applied to dedup/config). Modelled here as
        ``POST /api/blobs/fetch {blob_ref}`` returning the raw bytes with a
        ``Content-Type`` header. The path/field shape is PROVISIONAL until HB
        confirms (cross-seat NEEDS-HB); the Relay route is proven correct with
        this method MOCKED in tests.

        Args:
            artifact_ref: Capability handle for the stored blob.
            jwt_token: Optional user JWT to forward for attribution.

        Returns:
            ``{"content_type": str, "data": bytes}`` on a hit, or ``None`` on a
            404 / empty miss (the route maps ``None`` → ARTIFACT_NOT_FOUND). The
            ``content_type`` is the HB-reported MIME of the stored blob; the
            route prefers the per-KIND Content-Type but falls back to this.

        Raises:
            HeartBeatUnavailableError: If HeartBeat is unreachable.
            TransientError: If HeartBeat returns 5xx.
        """
        path = "/api/blobs/fetch"
        body_bytes = self._json_bytes({"blob_ref": artifact_ref})

        async def _fetch():
            http = self._get_http()
            headers = self._s2s_headers(
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )
            # Forward the user JWT for attribution when present; HB s2s HMAC
            # still authenticates the Relay→HB hop itself.
            if jwt_token:
                headers["Authorization"] = f"Bearer {jwt_token}"

            self._calls.append(("fetch_blob", artifact_ref))

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise HeartBeatUnavailableError(
                    message=f"Cannot connect to HeartBeat for blob fetch: {e}"
                ) from e

            # A 404 from HB is a normal miss, not an error — the route maps it
            # to the ARTIFACT_NOT_FOUND contract body.
            if resp.status_code == 404:
                return None

            self._raise_for_status(resp, "fetch_blob")

            data = resp.content
            if not data:
                return None
            content_type = (
                resp.headers.get("content-type") or "application/octet-stream"
            )
            logger.debug(
                f"HeartBeat fetch_blob — ref={artifact_ref[:12]}..., "
                f"bytes={len(data)}, content_type={content_type}",
                extra={"trace_id": self.trace_id},
            )
            return {"content_type": content_type, "data": data}

        return await self.call_with_retries(_fetch)

    # ── Deduplication ──────────────────────────────────────────────────────

    async def check_duplicate(self, file_hash: str) -> Dict[str, Any]:
        """
        Check if a file hash has been seen before.

        ``POST /api/dedup/check`` — JSON: ``{file_hash}``.
        Auth: §3.3 HMAC-SHA256 (post-cutover 2026-05-08 per
        ``HMAC_S2S_MIGRATION_SPEC.md`` §2). Bearer api_key:api_secret
        was rejected with ``401 BEARER_S2S_REMOVED`` from this date.

        (HeartBeat audit M-1: SHA-256 is a content identifier and must
        not appear in URLs/logs.)

        Args:
            file_hash: SHA256 hex digest of file data.

        Returns:
            ``{is_duplicate, file_hash, original_queue_id}``
        """
        path = "/api/dedup/check"
        body_bytes = self._json_bytes({"file_hash": file_hash})

        async def _check():
            http = self._get_http()
            headers = self._s2s_headers(
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )

            self._calls.append(("check_duplicate", file_hash))

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
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

    # ── (CSSV1 S4 R7) ``record_duplicate()`` removed ───────────────────────
    #
    # The legacy ``POST /api/dedup/record`` call was deleted in CSSV1 S4.
    # HB now writes the canonical ``blob.blob_deduplication`` row as a
    # side effect of the ``POST /api/blobs/register`` handler (D2) — there
    # is no separate "record" hop from Relay. If you're tempted to add
    # one back, the right answer is "HB's register handler should be the
    # source of truth"; surface the gap as an HB chip, not a Relay
    # workaround.

    # ── Daily Limits ───────────────────────────────────────────────────────

    async def check_daily_limit(
        self,
        company_id: str,
        file_count: int = 1,
    ) -> Dict[str, Any]:
        """
        Check if company has exceeded daily upload limit.

        ``POST /api/limits/daily/check`` — JSON:
        ``{company_id, file_count}``.
        Auth: §3.3 HMAC-SHA256 (post-cutover 2026-05-08).

        (HeartBeat audit C-4: company_id must travel in body, not URL.)

        Args:
            company_id: Company identifier.
            file_count: Number of files in this request.

        Returns:
            ``{company_id, files_today, daily_limit, limit_reached, remaining}``
        """
        path = "/api/limits/daily/check"
        body_bytes = self._json_bytes(
            {"company_id": company_id, "file_count": file_count}
        )

        async def _check():
            http = self._get_http()
            headers = self._s2s_headers(
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )

            self._calls.append(("check_daily_limit", company_id, file_count))

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
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

        ``POST /api/v1/heartbeat/config`` (empty body).
        Auth: §3.3 HMAC-SHA256 (post-cutover 2026-05-08).

        (HeartBeat audit C-2: response contains SMTP credentials, FIRS
        keys, NAS config — must travel via POST.)

        Returns full config dict (tenant, firs, endpoints, tier_limits,
        etc.). Used at startup to populate ConfigCache.

        Raises:
            HeartBeatUnavailableError: If HeartBeat is unreachable.
            TransientError: If HeartBeat returns 5xx.
        """
        path = "/api/v1/heartbeat/config"
        # Empty-body POST: sign sha256(b"") per spec §2.2.
        body_bytes = b""

        async def _fetch():
            http = self._get_http()
            headers = self._s2s_headers(
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )

            self._calls.append(("fetch_config",))

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
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

            payload = {
                "service": service,
                "event_type": event_type,
                "user_id": user_id,
                "details": details or {},
                "trace_id": self.trace_id,
            }

            # Per RELAY_NEXT_STEPS_NOTE §4.2: wire HMAC headers
            # proactively so we get auth-enabled-for-free once D-audit
            # closes (today /api/audit/log has NO auth on HB; HB will
            # ignore HMAC headers when present). When the signing key
            # isn't configured yet (early-deploy / dev), degrade to the
            # legacy unauthenticated wire to keep audit fire-and-forget.
            path = "/api/audit/log"
            body_bytes = self._json_bytes(payload)
            if self._service_signing_key and self._service_api_key:
                headers = self._s2s_headers(
                    method="POST",
                    path=path,
                    body_bytes=body_bytes,
                )
            else:
                headers = dict(self.get_trace_headers())
                headers["Content-Type"] = "application/json"

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
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

        ``POST /api/platform/transforma/config`` (empty body).
        Auth: §3.3 HMAC-SHA256 (post-cutover 2026-05-08).

        (HeartBeat audit M-7: response contains FIRS keys; POST keeps it
        out of HTTP caches.)

        Called by TransformaModuleCache at startup and every 12 hours.

        Returns:
            ``{modules: [...], service_keys: {...}}``
        """
        path = "/api/platform/transforma/config"
        body_bytes = b""

        async def _get_config():
            http = self._get_http()
            headers = self._s2s_headers(
                method="POST",
                path=path,
                body_bytes=body_bytes,
            )

            self._calls.append(("get_transforma_config",))

            try:
                resp = await http.post(
                    path,
                    content=body_bytes,
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
