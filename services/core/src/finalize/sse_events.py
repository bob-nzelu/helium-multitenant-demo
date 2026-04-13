"""
SSE Event Definitions for WS5 Finalize.

Events pushed to Float SDK via SSE after finalization steps complete.
Float uses these to update ReviewPage status, show success/failure,
and sync local databases.

Event naming: {entity}.{action} (e.g., invoice.finalized, hlx.finalized)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class SSEEvent:
    """Base SSE event structure."""

    event: str
    data: dict[str, Any]

    def serialize(self) -> str:
        """Serialize to SSE wire format."""
        return f"event: {self.event}\ndata: {json.dumps(self.data)}\n\n"


# ── HLX-Level Events ────────────────────────────────────────────────────


def hlx_finalize_started(batch_id: str, invoice_count: int) -> SSEEvent:
    """User clicked Finalize — validation in progress."""
    return SSEEvent(
        event="hlx.finalize_started",
        data={
            "batch_id": batch_id,
            "invoice_count": invoice_count,
            "status": "VALIDATING",
        },
    )


def hlx_validation_failed(
    batch_id: str,
    violations: list[dict[str, Any]],
) -> SSEEvent:
    """Edit validation failed — return violations to Float for display."""
    return SSEEvent(
        event="hlx.validation_failed",
        data={
            "batch_id": batch_id,
            "status": "VALIDATION_FAILED",
            "violation_count": len(violations),
            "violations": violations[:50],  # Cap to avoid huge payloads
        },
    )


def hlx_finalized(
    batch_id: str,
    invoices_created: int,
    edge_accepted: int,
) -> SSEEvent:
    """Finalization complete — invoices committed and queued to Edge."""
    return SSEEvent(
        event="hlx.finalized",
        data={
            "batch_id": batch_id,
            "status": "FINALIZED",
            "invoices_created": invoices_created,
            "edge_accepted": edge_accepted,
        },
    )


def hlx_finalize_failed(batch_id: str, error: str) -> SSEEvent:
    """Finalization failed after validation passed (DB or Edge error)."""
    return SSEEvent(
        event="hlx.finalize_failed",
        data={
            "batch_id": batch_id,
            "status": "FINALIZE_FAILED",
            "error": error,
        },
    )


# ── Entity Sync Events ──────────────────────────────────────────────────
# These tell Float SDK to update its local sync.db mirrors.


def invoice_created(invoice_id: str, invoice_data: dict[str, Any]) -> SSEEvent:
    """A new invoice record was committed to Core DB."""
    return SSEEvent(
        event="invoice.created",
        data={
            "invoice_id": invoice_id,
            "invoice_number": invoice_data.get("invoice_number"),
            "irn": invoice_data.get("irn"),
            "status": "FINALIZED",
            "total_amount": invoice_data.get("total_amount"),
            "issue_date": invoice_data.get("issue_date"),
            "direction": invoice_data.get("direction"),
            "transaction_type": invoice_data.get("transaction_type"),
        },
    )


def customer_created(customer_id: str, customer_data: dict[str, Any]) -> SSEEvent:
    """A new customer was detected and committed."""
    return SSEEvent(
        event="customer.created",
        data={
            "customer_id": customer_id,
            "tin": customer_data.get("tin"),
            "customer_name": customer_data.get("customer_name"),
            "__IS_NEW__": True,
        },
    )


def customer_updated(customer_id: str, customer_data: dict[str, Any]) -> SSEEvent:
    """An existing customer was updated from invoice data."""
    return SSEEvent(
        event="customer.updated",
        data={
            "customer_id": customer_id,
            "tin": customer_data.get("tin"),
            "customer_name": customer_data.get("customer_name"),
        },
    )


def product_created(product_id: str, product_data: dict[str, Any]) -> SSEEvent:
    """A new inventory product was detected and committed."""
    return SSEEvent(
        event="product.created",
        data={
            "product_id": product_id,
            "product_name": product_data.get("product_name"),
            "customer_sku": product_data.get("customer_sku"),
            "type": product_data.get("type"),
            "__IS_NEW__": True,
        },
    )


def product_updated(product_id: str, product_data: dict[str, Any]) -> SSEEvent:
    """An existing inventory product was updated (aggregates, classification)."""
    return SSEEvent(
        event="product.updated",
        data={
            "product_id": product_id,
            "product_name": product_data.get("product_name"),
            "total_times_invoiced": product_data.get("total_times_invoiced"),
        },
    )
