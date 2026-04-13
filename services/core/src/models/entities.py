"""
Entity Pydantic Models (WS4)

Response models, list items, pagination envelope, update/delete requests.
Per WS4 DELIVERABLES.md: All entity response shapes.
Per WS4 API_CONTRACTS.md: Field subsets for list vs single endpoints.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field


class PaginatedEnvelope(BaseModel):
    """Standard pagination wrapper per MENTAL_MODEL \u00a75."""

    total_count: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_previous: bool
    items: list[dict[str, Any]]

    @classmethod
    def build(
        cls,
        items: list[dict[str, Any]],
        total_count: int,
        page: int,
        per_page: int,
    ) -> PaginatedEnvelope:
        total_pages = max(1, math.ceil(total_count / per_page))
        return cls(
            total_count=total_count,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
            items=items,
        )


INVOICE_LIST_FIELDS: list[str] = [
    "invoice_id",
    "helium_invoice_no",
    "invoice_number",
    "direction",
    "document_type",
    "transaction_type",
    "issue_date",
    "due_date",
    "workflow_status",
    "transmission_status",
    "payment_status",
    "seller_name",
    "buyer_name",
    "subtotal",
    "tax_amount",
    "total_amount",
    "wht_amount",
    "discount_amount",
    "product_summary",
    "line_items_count",
    "category",
    "reference",
    "attachment_count",
    "created_at",
    "updated_at",
]

CUSTOMER_LIST_FIELDS: list[str] = [
    "customer_id",
    "company_name",
    "customer_code",
    "tin",
    "rc_number",
    "trading_name",
    "short_code",
    "customer_type",
    "tax_classification",
    "is_mbs_registered",
    "is_fze",
    "state",
    "city",
    "total_invoices",
    "compliance_score",
    "last_active_date",
    "total_lifetime_value",
    "created_at",
]

INVENTORY_LIST_FIELDS: list[str] = [
    "product_id",
    "product_name",
    "hsn_code",
    "vat_treatment",
    "product_category",
    "service_category",
    "avg_unit_price",
    "currency",
    "vat_rate",
    "type",
    "helium_sku",
    "description",
    "created_at",
]


class EntityUpdateRequest(BaseModel):
    """PUT /entity/{type}/{id} request body."""

    recover: bool | None = Field(default=None, description="Set true to recover a soft-deleted entity")
    change_reason: str | None = Field(default=None, description="Audit reason for all field changes in this request")

    model_config = {"extra": "allow"}

    def get_field_updates(self) -> dict[str, Any]:
        """Return only the entity field updates (exclude meta-fields)."""
        extras = self.model_extra or {}
        return {k: v for k, v in extras.items() if k not in ("recover", "change_reason")}


class EntityDeleteResponse(BaseModel):
    """DELETE /entity/{type}/{id} response."""

    deleted: bool = True
    entity_type: str
    entity_id: str
    deleted_at: str
    recovery_until: str
