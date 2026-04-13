"""
Edge Client — Queues finalized invoices for FIRS submission.

Step 9 of the finalize flow: After DB committal, push invoice payloads
to Edge service for asynchronous FIRS submission.

Edge API (internal):
  POST /api/v1/submit   — Submit a batch of invoices
  GET  /api/v1/status/{batch_id}  — Check batch submission status

See: HLX_FORMAT.md v1.1 Section 9 (Finalize Flow Step 9)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default timeout for Edge API calls
EDGE_TIMEOUT = 30.0


@dataclass
class EdgeSubmission:
    """A single invoice payload for FIRS submission."""

    invoice_id: str
    irn: str
    invoice_number: str
    issue_date: str
    direction: str
    transaction_type: str
    total_amount: float
    tax_amount: float
    currency_code: str
    seller_tin: str
    seller_name: str
    buyer_tin: str | None
    buyer_name: str | None
    line_items: list[dict[str, Any]]
    qr_code_data: str | None = None
    firs_invoice_type_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "irn": self.irn,
            "invoice_number": self.invoice_number,
            "issue_date": self.issue_date,
            "direction": self.direction,
            "transaction_type": self.transaction_type,
            "total_amount": self.total_amount,
            "tax_amount": self.tax_amount,
            "currency_code": self.currency_code,
            "seller_tin": self.seller_tin,
            "seller_name": self.seller_name,
            "buyer_tin": self.buyer_tin,
            "buyer_name": self.buyer_name,
            "firs_invoice_type_code": self.firs_invoice_type_code,
            "qr_code_data": self.qr_code_data,
            "line_items": self.line_items,
        }


@dataclass
class EdgeSubmitResult:
    """Result of submitting a batch to Edge."""

    batch_id: str
    accepted: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.rejected == 0 and len(self.errors) == 0


@dataclass
class EdgeBatchStatus:
    """Status of a submitted batch from Edge."""

    batch_id: str
    status: str  # QUEUED, SUBMITTING, COMPLETED, PARTIAL_FAILURE, FAILED
    total: int = 0
    submitted: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class EdgeClient:
    """HTTP client for the Edge FIRS submission service."""

    def __init__(self, base_url: str, api_key: str | None = None):
        """Initialize Edge client.

        Args:
            base_url: Edge service base URL (e.g., http://localhost:8002).
            api_key: Optional API key for Edge auth.
        """
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Edge-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=EDGE_TIMEOUT,
        )

    async def submit_batch(
        self,
        batch_id: str,
        invoices: list[EdgeSubmission],
        company_id: str,
    ) -> EdgeSubmitResult:
        """Submit a batch of finalized invoices to Edge for FIRS submission.

        Args:
            batch_id: HLX batch identifier.
            invoices: List of invoice payloads.
            company_id: Tenant company identifier.

        Returns:
            EdgeSubmitResult with acceptance/rejection counts.
        """
        payload = {
            "batch_id": batch_id,
            "company_id": company_id,
            "invoices": [inv.to_dict() for inv in invoices],
        }

        try:
            resp = await self._client.post("/api/v1/submit", json=payload)
            resp.raise_for_status()
            data = resp.json()

            return EdgeSubmitResult(
                batch_id=batch_id,
                accepted=data.get("accepted", len(invoices)),
                rejected=data.get("rejected", 0),
                errors=data.get("errors", []),
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "Edge submit failed: %d %s", e.response.status_code, e.response.text
            )
            return EdgeSubmitResult(
                batch_id=batch_id,
                rejected=len(invoices),
                errors=[f"HTTP {e.response.status_code}: {e.response.text}"],
            )
        except httpx.RequestError as e:
            logger.error("Edge connection error: %s", e)
            return EdgeSubmitResult(
                batch_id=batch_id,
                rejected=len(invoices),
                errors=[f"Connection error: {e}"],
            )

    async def get_batch_status(self, batch_id: str) -> EdgeBatchStatus | None:
        """Check the submission status of a batch.

        Args:
            batch_id: Batch identifier.

        Returns:
            EdgeBatchStatus or None if batch not found.
        """
        try:
            resp = await self._client.get(f"/api/v1/status/{batch_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()

            return EdgeBatchStatus(
                batch_id=batch_id,
                status=data.get("status", "UNKNOWN"),
                total=data.get("total", 0),
                submitted=data.get("submitted", 0),
                failed=data.get("failed", 0),
                errors=data.get("errors", []),
            )
        except Exception as e:
            logger.error("Edge status check failed: %s", e)
            return None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    @staticmethod
    def build_submissions(
        rows: list[dict[str, Any]],
    ) -> list[EdgeSubmission]:
        """Convert finalized invoice rows to Edge submission payloads.

        Args:
            rows: Finalized invoice rows (post-IRN generation).

        Returns:
            List of EdgeSubmission objects.
        """
        submissions = []
        for row in rows:
            line_items = []
            for item in row.get("line_items", []):
                line_items.append({
                    "line_number": item.get("line_number"),
                    "description": item.get("description"),
                    "quantity": item.get("quantity"),
                    "unit_price": item.get("unit_price"),
                    "line_total": item.get("line_total"),
                    "tax_amount": item.get("tax_amount"),
                    "hsn_code": item.get("hsn_code"),
                    "service_code": item.get("service_code"),
                    "vat_treatment": item.get("vat_treatment"),
                    "unit_of_measure": item.get("unit_of_measure"),
                })

            submissions.append(
                EdgeSubmission(
                    invoice_id=row["invoice_id"],
                    irn=row["irn"],
                    invoice_number=row["invoice_number"],
                    issue_date=row.get("issue_date", ""),
                    direction=row.get("direction", "OUTBOUND"),
                    transaction_type=row.get("transaction_type", "B2B"),
                    total_amount=float(row.get("total_amount", 0)),
                    tax_amount=float(row.get("tax_amount", 0)),
                    currency_code=row.get("currency_code", "NGN"),
                    seller_tin=row.get("seller_tin", ""),
                    seller_name=row.get("seller_name", ""),
                    buyer_tin=row.get("buyer_tin"),
                    buyer_name=row.get("buyer_name"),
                    line_items=line_items,
                    qr_code_data=row.get("qr_code_data"),
                    firs_invoice_type_code=row.get("firs_invoice_type_code"),
                )
            )
        return submissions
