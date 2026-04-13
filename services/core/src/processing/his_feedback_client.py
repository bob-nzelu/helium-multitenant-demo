"""
HIS Intelligence Feedback Client

Submits human corrections from HLX review back to HIS so it can improve
future enrichment (HSN codes, addresses, classifications).

Per Bob (2026-03-29): HLX-reviewed corrections carry higher confidence
than API auto-finalized data. SDK explicitly sends corrections array.

Non-fatal: failures are logged but never block finalization.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

logger = structlog.get_logger()

_MAX_RETRIES = 3
_RETRYABLE_STATUS = {500, 502, 503}


class HISFeedbackClient:
    """Submit human corrections to HIS for intelligence updates."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )
        self._api_key = api_key

    async def submit_corrections(
        self,
        corrections: list[dict],
        company_id: str,
        source: str,
        confidence_weight: float,
        context: dict | None = None,
    ) -> dict | None:
        """
        POST /api/v1/his/intelligence/update

        Submits human-corrected fields to HIS for learning.
        Retries 3x with exponential backoff on 5xx.
        Non-fatal: returns None on final failure.

        Args:
            corrections: List of {entity_type, entity_id, field, old_value, new_value}.
            company_id: Owning company (per-tenant isolation).
            source: "hlx_review" | "api_finalize" | "hlx_no_change".
            confidence_weight: 0.95 (human), 0.70 (implicit), 0.40 (auto).
            context: Optional extra context (invoice_id, product_name, etc.).
        """
        if not corrections:
            return None

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "company_id": company_id,
            "source": source,
            "confidence_weight": confidence_weight,
            "corrections": corrections,
        }
        if context:
            payload["context"] = context

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._client.post(
                    "/api/v1/his/intelligence/update",
                    json=payload,
                    headers=headers,
                )

                if resp.status_code in (200, 201, 204):
                    logger.info(
                        "his_feedback_submitted",
                        company_id=company_id,
                        source=source,
                        correction_count=len(corrections),
                        confidence_weight=confidence_weight,
                    )
                    return resp.json() if resp.content else {}

                if resp.status_code not in _RETRYABLE_STATUS:
                    logger.warning(
                        "his_feedback_rejected",
                        status_code=resp.status_code,
                        company_id=company_id,
                    )
                    return None

                last_error = Exception(f"HIS returned {resp.status_code}")
                await asyncio.sleep(2 ** attempt)

            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)

        logger.warning(
            "his_feedback_failed",
            company_id=company_id,
            source=source,
            correction_count=len(corrections),
            error=str(last_error),
        )
        return None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
