"""
Field Editability Allowlists

Per WS4 MENTAL_MODEL \u00a73: Every PUT /entity/{type}/{id} request must validate
that only user-editable fields are being modified. System-only fields are
rejected with HTTP 422 (FIELD_NOT_EDITABLE).

These sets are FROZEN \u2014 any addition requires Schema Governance approval.
"""

from __future__ import annotations

from src.errors import CoreError, CoreErrorCode


INVOICE_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "payment_status",
    "reference",
    "payment_terms_note",
    "category",
    "notes_to_firs",
})

INVOICE_PAYMENT_STATUS_VALUES: frozenset[str] = frozenset({
    "UNPAID",
    "PAID",
    "PARTIAL",
    "CANCELLED",
    "DISPUTED",
})

CUSTOMER_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "company_name",
    "trading_name",
    "short_code",
    "customer_type",
    "tax_classification",
    "tin",
    "rc_number",
    "tax_id",
    "primary_identifier",
    "email",
    "phone",
    "website",
    "address",
    "city",
    "state",
    "state_code",
    "lga",
    "lga_code",
    "postal_code",
    "industry",
    "business_unit",
    "business_description",
    "default_due_date_days",
    "is_fze",
})

CUSTOMER_TYPE_VALUES: frozenset[str] = frozenset({"B2B", "B2G"})
CUSTOMER_TAX_CLASSIFICATION_VALUES: frozenset[str] = frozenset({"STANDARD", "EXEMPT"})
CUSTOMER_PRIMARY_IDENTIFIER_VALUES: frozenset[str] = frozenset({"TIN", "RC_NUMBER", "TAX_ID"})

INVENTORY_EDITABLE_FIELDS: frozenset[str] = frozenset({
    "product_name",
    "hsn_code",
    "type",
    "vat_treatment",
    "vat_rate",
    "product_category",
    "service_category",
    "description",
    "currency",
    "unit_of_measure",
    "customer_sku",
    "oem_sku",
    "service_code",
    "is_tax_exempt",
})

INVENTORY_TYPE_VALUES: frozenset[str] = frozenset({"GOODS", "SERVICE"})
INVENTORY_VAT_TREATMENT_VALUES: frozenset[str] = frozenset({"STANDARD", "EXEMPT", "ZERO_RATED"})

EDITABLE_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "invoice": INVOICE_EDITABLE_FIELDS,
    "customer": CUSTOMER_EDITABLE_FIELDS,
    "inventory": INVENTORY_EDITABLE_FIELDS,
}

ENUM_VALIDATION: dict[str, dict[str, frozenset[str]]] = {
    "invoice": {
        "payment_status": INVOICE_PAYMENT_STATUS_VALUES,
    },
    "customer": {
        "customer_type": CUSTOMER_TYPE_VALUES,
        "tax_classification": CUSTOMER_TAX_CLASSIFICATION_VALUES,
        "primary_identifier": CUSTOMER_PRIMARY_IDENTIFIER_VALUES,
    },
    "inventory": {
        "type": INVENTORY_TYPE_VALUES,
        "vat_treatment": INVENTORY_VAT_TREATMENT_VALUES,
    },
}

TEXT_MAX_LENGTHS: dict[str, dict[str, int]] = {
    "invoice": {
        "notes_to_firs": 500,
        "payment_terms_note": 500,
    },
    "customer": {
        "business_description": 500,
    },
    "inventory": {
        "description": 500,
    },
}

ENTITY_TABLE_MAP: dict[str, tuple[str, str, str]] = {
    "invoice": ("invoices", "invoices", "invoice_id"),
    "customer": ("customers", "customers", "customer_id"),
    "inventory": ("inventory", "inventory", "product_id"),
}

VALID_ENTITY_TYPES: frozenset[str] = frozenset({"invoice", "customer", "inventory"})


def validate_entity_type(entity_type: str) -> None:
    """Raise INVALID_ENTITY_TYPE if entity_type is not recognised."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise CoreError(
            error_code=CoreErrorCode.INVALID_ENTITY_TYPE,
            message=f"Entity type '{entity_type}' is not valid. Must be: invoice, customer, inventory",
        )


def validate_fields(entity_type: str, fields: dict) -> None:
    """
    Validate that all fields in the update request are user-editable
    and pass enum/length constraints.

    Raises FIELD_NOT_EDITABLE or INVALID_FIELD_VALUE on failure.
    """
    editable = EDITABLE_FIELDS_BY_TYPE[entity_type]
    enums = ENUM_VALIDATION.get(entity_type, {})
    lengths = TEXT_MAX_LENGTHS.get(entity_type, {})

    for field_name, value in fields.items():
        if field_name == "change_reason":
            continue

        if field_name not in editable:
            raise CoreError(
                error_code=CoreErrorCode.FIELD_NOT_EDITABLE,
                message=f"Field '{field_name}' is system-managed and cannot be updated via this endpoint",
                details=[{
                    "field": field_name,
                    "editable_fields": sorted(editable),
                }],
            )

        if field_name in enums and value is not None:
            allowed = enums[field_name]
            if str(value) not in allowed:
                raise CoreError(
                    error_code=CoreErrorCode.INVALID_FIELD_VALUE,
                    message=f"Invalid value for '{field_name}': must be one of {', '.join(sorted(allowed))}",
                    details=[{
                        "field": field_name,
                        "value": str(value),
                        "allowed_values": sorted(allowed),
                    }],
                )

        if field_name in lengths and value is not None:
            max_len = lengths[field_name]
            if isinstance(value, str) and len(value) > max_len:
                raise CoreError(
                    error_code=CoreErrorCode.INVALID_FIELD_VALUE,
                    message=f"Field '{field_name}' exceeds maximum length of {max_len} characters",
                    details=[{
                        "field": field_name,
                        "max_length": max_len,
                        "actual_length": len(value),
                    }],
                )
