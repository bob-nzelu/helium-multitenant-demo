"""
Core API Client

Real HTTP client for Helium Core service via httpx.AsyncClient.

Core handles invoice processing (enqueue → extraction → finalize → IRN/QR →
Edge submission). Relay enqueues files and triggers the finalize lifecycle by
reference; Core owns the work and emits the lifecycle SSE that Scout reduces.

Auth model (deployed Core, services/core/src/app.py):
    Core mounts NO service-to-service auth middleware on its ingestion /
    finalize routes — ``POST /api/v1/enqueue`` and ``POST /api/v1/finalize``
    read the request body directly with no ``verify_service_credentials``
    dependency. So the Relay→Core hop is a plain HTTP POST carrying only the
    trace headers (X-Trace-ID / X-Request-ID). The user JWT, when present, is
    forwarded as ``Authorization: Bearer {jwt}`` for attribution only — Core
    does not gate the call on it today. (If Core later adds s2s HMAC on these
    routes, wire ``build_s2s_hmac_headers`` here exactly as HeartBeatClient
    does, keyed off a ``RELAY_CORE_S2S_SIGNING_KEY`` env var.)

Endpoint base URL: ``RELAY_CORE_API_URL`` (e.g. ``http://core:8080`` in the
docker-compose deploy; ``http://localhost:8080`` for local dev).

NOTE: Relay does NOT health-check Core. HeartBeat owns service health
monitoring. Relay discovers Core unavailability through actual request
failures, which surface as ``CoreUnavailableError`` / ``TransientError``.

Body-bytes discipline mirrors HeartBeatClient: we send JSON via httpx's
``json=`` for unauthenticated calls (no signature to match), and read the
JSON response back verbatim so callers get Core's exact result shape
(status, irn, qr, artifacts, event_id, event_family).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from uuid6 import uuid7

import httpx

from .base import BaseClient
from ..errors import CoreUnavailableError, TransientError

logger = logging.getLogger(__name__)


class CoreClient(BaseClient):
    """
    Client for Helium Core API.

    Core endpoints (deployed — services/core/src/{ingestion,finalize}/router.py):
        POST /api/v1/enqueue                  → Queue a file for processing (201)
        POST /api/v1/finalize                 → Finalize by reference → IRN/QR/Edge
        GET  /api/v1/finalize/{batch}/status  → Finalization status
        GET  /api/v1/core_queue/status        → Queue status

    No health_check — HeartBeat owns service health monitoring.
    """

    def __init__(
        self,
        core_api_url: str = "http://localhost:8080",
        timeout: float = 30.0,
        preview_timeout: float = 300.0,
        max_attempts: int = 5,
        trace_id: Optional[str] = None,
    ):
        super().__init__(
            max_attempts=max_attempts,
            timeout=timeout,
            trace_id=trace_id,
        )
        self.core_api_url = core_api_url.rstrip("/")
        self.preview_timeout = preview_timeout

        # Shared httpx client — created lazily, closed explicitly on shutdown.
        self._http: Optional[httpx.AsyncClient] = None

        # Track calls for testing parity with HeartBeatClient.
        self._calls: list = []

    # ── HTTP plumbing ──────────────────────────────────────────────────────

    def _get_http(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.core_api_url,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._http

    async def close(self) -> None:
        """Close the httpx client. Call on shutdown."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    def _headers(self, jwt_token: Optional[str] = None) -> Dict[str, str]:
        """Trace headers (+ optional user JWT for attribution).

        Core mounts no s2s auth on these routes, so the only required headers
        are the trace headers for cross-service correlation. The user JWT, when
        present, is forwarded as a Bearer token for attribution only.
        """
        headers = dict(self.get_trace_headers())
        headers["Content-Type"] = "application/json"
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"
        return headers

    def _raise_for_status(self, resp: httpx.Response, context: str) -> None:
        """Raise the appropriate RelayError for a non-2xx Core response.

        Phase 1 surfaces real Core failures (no silent swallow): 5xx →
        retryable TransientError; other 4xx → CoreUnavailableError (permanent).
        Callers that want best-effort semantics (FinalizeService) decide
        whether to catch — the client itself no longer hides the failure.
        """
        if resp.is_success:
            return

        if resp.status_code >= 500:
            raise TransientError(
                error_code="CORE_SERVER_ERROR",
                message=f"Core {context} returned {resp.status_code}: {resp.text}",
            )

        raise CoreUnavailableError(
            message=f"Core {context} failed ({resp.status_code}): {resp.text}"
        )

    # ── Enqueue (POST /api/v1/enqueue) ─────────────────────────────────────

    async def enqueue(
        self,
        blob_uuid: str,
        filename: str,
        file_size_bytes: int,
        batch_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Enqueue a file for Core processing.

        Real call: ``POST {core}/api/v1/enqueue`` with the Core
        ``EnqueueRequest`` body (services/core/src/ingestion/models.py):
            {blob_uuid, data_uuid, original_filename, company_id,
             uploaded_by, batch_id, priority}
        Core returns ``EnqueueResponse`` {queue_id, status, data_uuid,
        created_at} with HTTP 201.

        ``data_uuid``/``company_id``/``uploaded_by`` are sourced from
        ``metadata`` when the caller supplies SDK identity fields; sensible
        fallbacks keep the call valid for the external/bulk paths that pass
        only the blob identity.

        Args:
            blob_uuid: Blob UUID from HeartBeat.
            filename: Original filename.
            file_size_bytes: File size in bytes (forwarded as metadata).
            batch_id: Batch identifier.
            metadata: SDK identity/trace fields (company_id, uploaded_by,
                data_uuid, priority) — forwarded to Core for traceability.
            jwt_token: Bearer JWT for user identity verification.

        Returns:
            Core's EnqueueResponse: {queue_id, status, data_uuid, created_at}.

        Raises:
            CoreUnavailableError: If Core is unreachable or returns 4xx.
            TransientError: If Core returns 5xx (retried by call_with_retries).
        """
        meta = metadata or {}
        payload: Dict[str, Any] = {
            "blob_uuid": blob_uuid,
            "data_uuid": str(meta.get("data_uuid") or blob_uuid),
            "original_filename": filename,
            "company_id": str(meta.get("company_id") or meta.get("tenant_id") or ""),
            "uploaded_by": str(meta.get("uploaded_by") or meta.get("user_id") or ""),
            "batch_id": batch_id,
            "priority": int(meta.get("priority") or 3),
        }

        async def _enqueue():
            http = self._get_http()
            self._calls.append(("enqueue", blob_uuid, filename))
            try:
                resp = await http.post(
                    "/api/v1/enqueue",
                    json=payload,
                    headers=self._headers(jwt_token),
                )
            except httpx.ConnectError as e:
                raise CoreUnavailableError(
                    message=f"Cannot connect to Core for enqueue: {e}"
                ) from e

            # A 409 from Core means the blob is already queued (idempotent
            # re-ingest). Treat it as a successful enqueue and return a
            # queued-shaped result so the upload flow is not blocked.
            if resp.status_code == 409:
                logger.info(
                    "Core enqueue — blob already queued (409, idempotent), "
                    "blob_uuid=%s",
                    blob_uuid,
                    extra={"trace_id": self.trace_id},
                )
                return {
                    "queue_id": f"queued_{uuid7()}",
                    "status": "queued",
                    "batch_id": batch_id,
                    "blob_uuid": blob_uuid,
                }

            self._raise_for_status(resp, "enqueue")
            result = resp.json()
            logger.debug(
                "Core enqueue — queue_id=%s blob_uuid=%s",
                result.get("queue_id"),
                blob_uuid,
                extra={"trace_id": self.trace_id},
            )
            # Carry blob_uuid through for callers that key on it.
            result.setdefault("blob_uuid", blob_uuid)
            return result

        return await self.call_with_retries(_enqueue)

    async def process_preview(
        self,
        queue_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Request a processed preview for a queued file (Float bulk flow).

        Core's deployed surface has no synchronous "process and return preview"
        endpoint — extraction runs asynchronously off the queue and the result
        is read back via the queue/preview status routes. Phase 1 acknowledges
        the queued item (no fabricated preview rows) so the bulk caller is wired
        end-to-end; the real preview is read via HeartBeat's preview store once
        Core has extracted.

        NEEDS-CORE: a synchronous preview-by-queue_id endpoint (or a documented
        "poll the queue status" contract). Until then this returns a queued
        acknowledgement rather than inventing extraction output.

        Args:
            queue_id: Queue ID from enqueue().
            timeout: Override preview timeout (seconds).

        Returns:
            {"queue_id": str, "status": "queued"}
        """
        effective_timeout = timeout or self.preview_timeout

        async def _ack():
            logger.debug(
                "Core process_preview — queue_id=%s (queued; preview read "
                "asynchronously, no Core sync-preview endpoint)",
                queue_id,
                extra={"trace_id": self.trace_id},
            )
            return {"queue_id": queue_id, "status": "queued"}

        return await asyncio.wait_for(_ack(), timeout=effective_timeout)

    async def process_immediate(self, queue_id: str) -> Dict[str, Any]:
        """
        Fire-and-forget processing trigger (external API flow).

        Core's QueueScanner drains PENDING queue entries on its own cadence, so
        a successful ``enqueue`` is itself the processing trigger — there is no
        separate "process now" endpoint on Core's deployed surface. Phase 1
        acknowledges the trigger (the work is already scheduled by the enqueue)
        without a fabricated HTTP round-trip.

        NEEDS-CORE: an explicit "process now / bump priority" endpoint if the
        external flow needs to jump the queue. Today enqueue+QueueScanner cover
        the path.

        Args:
            queue_id: Queue ID from enqueue().

        Returns:
            {"queue_id": str, "status": "processing"}
        """
        async def _process():
            logger.debug(
                "Core process_immediate — queue_id=%s (handled by Core "
                "QueueScanner off the enqueue; no separate Core endpoint)",
                queue_id,
                extra={"trace_id": self.trace_id},
            )
            return {"queue_id": queue_id, "status": "processing"}

        return await self.call_with_retries(_process)

    async def finalize(
        self,
        queue_id: str,
        user_edits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finalize a previewed invoice with optional user edits (bulk flow).

        The deployed Core finalize endpoint (``POST /api/v1/finalize``) is the
        reference/lifecycle finalize — it is keyed on a document reference, not
        a ``queue_id``. The bulk "finalize with edits" path therefore routes
        through :meth:`finalize_by_reference` using ``queue_id`` as the
        reference. ``user_edits`` is forwarded as metadata for Core's edit
        validator.

        Args:
            queue_id: Queue ID to finalize (used as the finalize reference).
            user_edits: Optional dict of user corrections (forwarded).

        Returns:
            Core's finalize result (status, irn, qr, artifacts, ...).
        """
        return await self.finalize_by_reference(
            ref=queue_id,
            trace_id="",
            metadata={"user_edits": user_edits} if user_edits else None,
        )

    async def finalize_by_reference(
        self,
        ref: str,
        trace_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Trigger Core's finalize lifecycle for an already-ingested doc by
        reference (#3 finalize — NO bytes). This is the Phase-1 submit chain:
        Core runs IRN generation + QR + ``EdgeClient.submit_batch`` and emits
        the lifecycle SSE (core.submission.* / irn_assigned / qr_stamped /
        submission terminal) echoing ``trace_id``, then returns the result.

        Real call (PINNED submit-chain contract):
            ``POST {core}/api/v1/finalize``
            body: {"ref": <doc ref / sha256 / trace_id>, "trace_id": <uuidv7>}
            (metadata/jwt forwarded as extra body fields + Bearer header for
             attribution).
        Response: Core's finalize result JSON, returned VERBATIM to the caller
        so Relay's route surfaces Core's exact shape — expected to carry
        ``status`` and, on success, ``irn`` / ``qr`` / ``artifacts`` and the
        lifecycle ``event_id`` / ``event_family``.

        Phase 1 surfaces Core failures: a non-2xx raises (TransientError on 5xx,
        CoreUnavailableError on other 4xx). FinalizeService decides best-effort
        vs. fatal — the client no longer swallows the error.

        Args:
            ref: doc reference — file SHA-256 / doc_ref / trace_id.
            trace_id: client-supplied UUIDv7; echoed by Core on the SSE.
            metadata: forwarded identity/trace fields (added to the body).
            jwt_token: Bearer JWT (forwarded for user attribution).

        Returns:
            Core's finalize result dict (status, irn, qr, artifacts,
            event_id, event_family, ...).

        Raises:
            CoreUnavailableError: If Core is unreachable or returns a 4xx.
            TransientError: If Core returns 5xx (retried by call_with_retries).
        """
        payload: Dict[str, Any] = {
            "ref": ref,
            "trace_id": trace_id,
        }
        if metadata:
            payload["metadata"] = metadata

        async def _finalize_ref():
            http = self._get_http()
            self._calls.append(("finalize_by_reference", ref, trace_id))
            logger.debug(
                "Core finalize_by_reference — ref=%s trace_id=%s",
                (ref or "")[:16],
                trace_id or "(none)",
                extra={"trace_id": self.trace_id},
            )
            try:
                resp = await http.post(
                    "/api/v1/finalize",
                    json=payload,
                    headers=self._headers(jwt_token),
                )
            except httpx.ConnectError as e:
                raise CoreUnavailableError(
                    message=f"Cannot connect to Core for finalize: {e}"
                ) from e

            self._raise_for_status(resp, "finalize_by_reference")

            result = resp.json()
            logger.info(
                "Core finalize — ref=%s trace_id=%s status=%s irn=%s",
                (ref or "")[:16],
                trace_id or "(none)",
                result.get("status"),
                str(result.get("irn") or "")[:16] or "(none)",
                extra={"trace_id": self.trace_id},
            )
            # Echo ref/trace_id back when Core omits them so callers can
            # correlate regardless of Core's response completeness.
            result.setdefault("ref", ref)
            result.setdefault("trace_id", trace_id)
            return result

        return await self.call_with_retries(_finalize_ref)

    async def get_invoice_status(
        self,
        transaction_id: Optional[str] = None,
        irn: Optional[str] = None,
        invoice_number: Optional[str] = None,
        tenant_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Get one invoice's status from Core (L30 §6 invoice-level phase).

        NEEDS-CORE: Core's deployed surface does not yet expose a single-invoice
        status-by-irn/number/txn lookup (Gap #5 Core half). Returns None until
        that endpoint ships; StatusService treats None as graceful (HB-side
        transaction record still returned, invoice fields left null).

        Args:
            transaction_id: ERP reference → invoices.external_transaction_id.
            irn: Invoice Reference Number.
            invoice_number: Tenant invoice number.
            tenant_id: Calling tenant (L16 scoped).

        Returns:
            None — until Core builds the lookup endpoint.
        """
        async def _get_status():
            logger.debug(
                "Core get_invoice_status (no Core endpoint yet) — "
                "txn=%s irn=%s inv_no=%s",
                transaction_id or "(none)",
                (irn or "(none)")[:16],
                invoice_number or "(none)",
                extra={"trace_id": self.trace_id},
            )
            return None

        return await self.call_with_retries(_get_status)

    async def get_invoices_by_batch(
        self, batch_id: str, tenant_id: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get all invoices for a batch from Core (L30 §6 batch query).

        NEEDS-CORE: no by-batch invoice lookup on Core's deployed surface yet
        (Gap #5 Core half). Returns [] so StatusService merges nothing onto the
        HB file_transactions rows.
        """
        async def _get_batch():
            logger.debug(
                "Core get_invoices_by_batch (no Core endpoint yet) — "
                "batch_id=%s",
                batch_id,
                extra={"trace_id": self.trace_id},
            )
            return []

        return await self.call_with_retries(_get_batch)

    async def fetch_lifecycle_artifact(
        self,
        artifact_ref: str,
        artifact_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a LIFECYCLE artifact as raw JSON by reference (§B-RelayArtifactFetch).

        Lifecycle artifacts (HLX, FIRS-returned artifact, approval-lifecycle
        JSON, manifest) are owned by Core, not HB blob storage. Relay's
        artifact-fetch route returns these as raw JSON to Scout only.

        NEEDS-CORE: Core's deployed surface has no POST-body lifecycle-JSON-by-ref
        endpoint yet. ``artifact_ref`` is a bearer capability — it MUST NOT
        travel in a URL. Returns None (route → ARTIFACT_NOT_FOUND) until Core
        wires a real store; route correctness is proven with this method MOCKED.

        Args:
            artifact_ref: Capability handle for the lifecycle artifact.
            artifact_type: Optional explicit kind (hlx / firs_returned_artifact
                / approval_lifecycle_json / manifest).

        Returns:
            The lifecycle JSON document (a dict) on a hit, or None on a miss.
        """
        async def _fetch():
            logger.debug(
                "Core fetch_lifecycle_artifact (no Core endpoint yet) — "
                "ref=%s type=%s",
                artifact_ref[:16],
                artifact_type or "(none)",
                extra={"trace_id": self.trace_id},
            )
            return None

        return await self.call_with_retries(_fetch)
