"""
Core API Client

Stub client for Helium Core service.
Phase 1: Returns mock responses. Phase 2: Real HTTP calls via httpx.

Core handles invoice processing (OCR, extraction, validation).
Relay enqueues files and optionally waits for preview results.

NOTE: Relay does NOT health-check Core. HeartBeat owns service health
monitoring. Relay discovers Core unavailability through actual request
failures, which trigger graceful degradation (status="queued").
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from uuid6 import uuid7

from .base import BaseClient
from ..errors import CoreUnavailableError

logger = logging.getLogger(__name__)


class CoreClient(BaseClient):
    """
    Client for Helium Core API.

    Endpoints (Phase 2):
        POST /api/enqueue           → Queue file for processing
        POST /api/process/preview   → Process and return preview (bulk flow)
        POST /api/process/immediate → Fire-and-forget (external API flow)
        POST /api/finalize          → Finalize with user edits

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
        self.core_api_url = core_api_url
        self.preview_timeout = preview_timeout

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

        Args:
            blob_uuid: Blob UUID from HeartBeat.
            filename: Original filename.
            file_size_bytes: File size in bytes.
            batch_id: Batch identifier.
            metadata: SDK identity/trace fields (for Core traceability).
            jwt_token: Bearer JWT for user identity verification.

        Returns:
            {"queue_id": str, "status": "queued", "batch_id": str}

        Raises:
            CoreUnavailableError: If Core is unreachable.
        """
        async def _enqueue():
            # Phase 1 stub — returns mock response
            queue_id = f"queue_{uuid7()}"
            logger.debug(
                f"Core enqueue (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "queued",
                "batch_id": batch_id,
                "blob_uuid": blob_uuid,
            }

        return await self.call_with_retries(_enqueue)

    async def process_preview(
        self,
        queue_id: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Request Core to process a file and return preview data.

        Used by Float bulk flow — waits for Core to finish (up to 5 min).

        Args:
            queue_id: Queue ID from enqueue().
            timeout: Override preview timeout (seconds).

        Returns:
            {"queue_id": str, "status": "processed", "preview_data": {...}}

        Raises:
            asyncio.TimeoutError: If Core takes too long.
            CoreUnavailableError: If Core is unreachable.
        """
        effective_timeout = timeout or self.preview_timeout

        async def _process():
            # Phase 1 stub — simulates fast processing
            await asyncio.sleep(0.01)  # Simulate processing time
            return {
                "queue_id": queue_id,
                "status": "processed",
                "preview_data": {
                    "invoice_count": 1,
                    "total_amount": 0.0,
                    "currency": "NGN",
                    "items": [],
                },
            }

        # Use wait_for with preview timeout (separate from per-request timeout)
        return await asyncio.wait_for(
            _process(),
            timeout=effective_timeout,
        )

    async def process_immediate(self, queue_id: str) -> Dict[str, Any]:
        """
        Process file immediately without preview (for external API flow).

        Core processes in background — this returns as soon as Core acknowledges.

        Args:
            queue_id: Queue ID from enqueue().

        Returns:
            {"queue_id": str, "status": "processed"}
        """
        async def _process():
            logger.debug(
                f"Core process_immediate (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "processed",
            }

        return await self.call_with_retries(_process)

    async def finalize(
        self,
        queue_id: str,
        user_edits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finalize a previewed invoice with optional user edits.

        Args:
            queue_id: Queue ID to finalize.
            user_edits: Optional dict of user corrections.

        Returns:
            {"queue_id": str, "status": "finalized", "invoices_created": int}
        """
        async def _finalize():
            logger.debug(
                f"Core finalize (stub) — queue_id={queue_id}",
                extra={"trace_id": self.trace_id},
            )
            return {
                "queue_id": queue_id,
                "status": "finalized",
                "invoices_created": 1,
            }

        return await self.call_with_retries(_finalize)

    async def finalize_by_reference(
        self,
        ref: str,
        trace_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        jwt_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Trigger Core's fiscalize lifecycle for an already-ingested doc by
        reference (#3 finalize — NO bytes). Carries the client ``trace_id``
        through so Core's lifecycle SSE echoes it (§B-Submit / §B-EventLog).

        NEEDS-CORE: Core must accept this finalize trigger and emit
        ``core.artifact.hlx_available`` + ``core.submission.terminal`` echoing
        ``trace_id`` (cross-seat). Today this is an HTTP stub (canned dict);
        the real call is ``POST {core}/api/finalize`` (or equivalent). Per the
        discretionary ARCH ruling (debt-map Open Q (c)), Relay→Core transport
        for Monday is HTTP via this client, NOT AMQP.

        Args:
            ref: doc reference — file SHA-256 / doc_ref / trace_id.
            trace_id: client-supplied UUIDv7; echoed by Core on the SSE.
            metadata: forwarded identity/trace fields.
            jwt_token: Bearer JWT (forwarded for user attribution).

        Returns:
            {"ref": str, "status": "finalized", "trace_id": str}
        """
        async def _finalize_ref():
            logger.debug(
                "Core finalize_by_reference (stub) — ref=%s trace_id=%s",
                ref[:16],
                trace_id or "(none)",
                extra={"trace_id": self.trace_id},
            )
            return {
                "ref": ref,
                "status": "finalized",
                "trace_id": trace_id,
            }

        return await self.call_with_retries(_finalize_ref)

    async def publish_lifecycle_event(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        """
        Forward a Relay-originated lifecycle event frame to Core's SSE stream.

        Relay does not host an SSE server (memory "Scout as SSE Driver" — Scout
        connects to Core). Relay's §B-EventLog obligation is to forward the
        frame (carrying the client ``trace_id``) so Core's stream echoes it.

        NEEDS-CORE: real endpoint ``POST {core}/api/lifecycle/event`` (or the
        AMQP exchange in the S3 hardening contract). HTTP stub for Monday.

        Args:
            frame: the lifecycle frame from ``LifecycleEvent.to_frame()``
                   ({family, event, source, timestamp, trace_id?, data}).

        Returns:
            {"accepted": True, "family": str, "trace_id": str}
        """
        async def _publish():
            family = str(frame.get("family") or "")
            trace = str(frame.get("trace_id") or "")
            logger.debug(
                "Core publish_lifecycle_event (stub) — family=%s trace_id=%s",
                family,
                trace or "(none)",
                extra={"trace_id": self.trace_id},
            )
            return {"accepted": True, "family": family, "trace_id": trace}

        return await self.call_with_retries(_publish)

    async def fetch_lifecycle_artifact(
        self,
        artifact_ref: str,
        artifact_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a LIFECYCLE artifact as raw JSON by reference (§B-RelayArtifactFetch).

        BACKEND CONTRACT (CLAUDE.md "Backend Debt Notes" §B-RelayArtifactFetch +
        debt-map L139-160): lifecycle artifacts (HLX, FIRS-returned artifact,
        approval-lifecycle JSON, manifest) are owned by **Core**, not HB blob
        storage. Relay's artifact-fetch route returns these as raw JSON to Scout
        only (Reader never receives the raw JSON — Scout reduces it to
        display-safe fields; ``raw_bytes_sent`` stays false to Reader).

        NEEDS-CORE: Core must expose a **POST-body** lifecycle-JSON-by-ref
        endpoint. ``artifact_ref`` is a bearer capability — it MUST NOT travel
        in a URL (cf. the existing Core ``GET /api/invoices/<id>`` shape the
        VERB_DELTA ruling flags for migration). Modelled here as
        ``POST {core}/api/artifacts/lifecycle {artifact_ref, artifact_type}``
        returning the JSON document. Phase-1 stub: returns ``None`` so the
        route's miss path (ARTIFACT_NOT_FOUND) is the default until Core wires a
        real store; route correctness is proven with this method MOCKED.

        Args:
            artifact_ref: Capability handle for the lifecycle artifact.
            artifact_type: Optional explicit kind (hlx / firs_returned_artifact
                / approval_lifecycle_json / manifest) forwarded to Core.

        Returns:
            The lifecycle JSON document (a dict) on a hit, or ``None`` on a miss
            (the route maps ``None`` → ARTIFACT_NOT_FOUND).
        """
        async def _fetch():
            # Phase 1 stub — no Core lifecycle store wired yet (NEEDS-CORE).
            # Returns None so the route resolves to ARTIFACT_NOT_FOUND; tests
            # mock this method to exercise the JSON hit path.
            logger.debug(
                "Core fetch_lifecycle_artifact (stub) — ref=%s type=%s",
                artifact_ref[:16],
                artifact_type or "(none)",
                extra={"trace_id": self.trace_id},
            )
            return None

        return await self.call_with_retries(_fetch)
