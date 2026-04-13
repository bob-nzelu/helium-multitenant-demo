"""
Per-field provenance constants and editability rules.

Provenance tracks WHERE each field's value came from. This determines
which fields a user can edit at preview time on the ReviewPage.

Rule: Source data is sacred. Only enriched, missing, or low-confidence
fields are editable.

See: HLX_FORMAT.md v1.1 Sections 10-11
See: WS_TRANSFORMA_PROVENANCE_NOTE.md
"""

from __future__ import annotations

# ── Provenance Values ────────────────────────────────────────────────────

ORIGINAL = "ORIGINAL"  # Extracted from source document — NOT editable
MISSING = "MISSING"    # Not in source, not enriched — editable
HIS = "HIS"            # Enriched by HIS / Transforma — editable
DERIVED = "DERIVED"    # Computed by Core (totals, normalized names) — NOT editable
TENANT = "TENANT"      # From tenant config.db — NOT editable (edit via config.db)
MANUAL = "MANUAL"      # Set by user in a prior edit cycle — editable

EDITABLE_PROVENANCE: frozenset[str] = frozenset({MISSING, HIS, MANUAL})
NON_EDITABLE_PROVENANCE: frozenset[str] = frozenset({ORIGINAL, DERIVED, TENANT})

# Confidence threshold: fields below this are editable even if ORIGINAL
LOW_CONFIDENCE_THRESHOLD = 0.60

# ── Invoice Editable Fields ──────────────────────────────────────────────

# Always editable regardless of provenance (user metadata)
ALWAYS_EDITABLE_INVOICE_FIELDS: frozenset[str] = frozenset({
    "reference",
    "category",
    "notes_to_firs",
    "payment_terms_note",
    "terms",
    "transaction_type",  # B2B<->B2G free swap; B2C->B2B/B2G requires buyer details
})

# Never editable regardless of provenance
NEVER_EDITABLE_INVOICE_FIELDS: frozenset[str] = frozenset({
    # Identity
    "invoice_id",
    "helium_invoice_no",
    "invoice_number",
    "irn",
    "csid",
    "csid_status",
    "invoice_trace_id",
    "user_trace_id",
    "x_trace_id",
    # Financial (amounts can NEVER be touched)
    "subtotal",
    "tax_amount",
    "total_amount",
    "wht_amount",
    "discount_amount",
    "tax_exclusive_amount",
    "total_tax_amount",
    "exchange_rate",
    # Dates (source document dates)
    "issue_date",
    "issue_time",
    "sign_date",
    "transmission_date",
    "acknowledgement_date",
    # Classification (immutable after extraction)
    "direction",
    "document_type",
    "document_currency_code",
    "tax_currency_code",
    # Status fields (system-managed)
    "workflow_status",
    "transmission_status",
    "transmission_status_error",
    "payment_status",
    "inbound_status",
    # FIRS artifacts
    "firs_confirmation",
    "firs_response_data",
    "qr_code_data",
    # Retry
    "retry_count",
    "last_retry_at",
    "next_retry_at",
    # Audit
    "created_at",
    "updated_at",
    "deleted_at",
    "deleted_by",
    "created_by",
    "finalized_at",
    "processing_started_at",
    "processing_completed_at",
    "processing_duration_ms",
    # Machine/session context
    "machine_guid",
    "mac_address",
    "computer_name",
    "float_id",
    "session_id",
    # User context
    "helium_user_id",
    "user_email",
    "user_name",
    # Queue/batch
    "queue_id",
    "batch_id",
    "file_id",
    "blob_uuid",
    "original_filename",
    # Source
    "source",
    "source_id",
    # Schema
    "config_version_id",
    "schema_version_applied",
})

# Tenant party fields — seller on outbound, buyer on inbound
SELLER_PARTY_FIELDS: frozenset[str] = frozenset({
    "seller_id",
    "seller_business_id",
    "seller_name",
    "seller_tin",
    "seller_tax_id",
    "seller_rc_number",
    "seller_email",
    "seller_phone",
    "seller_address",
    "seller_city",
    "seller_postal_code",
    "seller_lga_code",
    "seller_state_code",
    "seller_country_code",
    "company_id",
})

BUYER_PARTY_FIELDS: frozenset[str] = frozenset({
    "buyer_id",
    "buyer_business_id",
    "buyer_name",
    "buyer_tin",
    "buyer_tax_id",
    "buyer_rc_number",
    "buyer_email",
    "buyer_phone",
    "buyer_address",
    "buyer_city",
    "buyer_postal_code",
    "buyer_lga_code",
    "buyer_state_code",
    "buyer_country_code",
})

# ── Line Item Fields ─────────────────────────────────────────────────────

# Line item fields that are NEVER editable (strict audit)
NEVER_EDITABLE_LINE_ITEM_FIELDS: frozenset[str] = frozenset({
    "line_id",
    "invoice_id",
    "line_number",
    "description",       # Source data — strict audit
    "quantity",          # Source data — strict audit
    "unit_price",        # Source data — strict audit
    "line_total",        # Source data — strict audit
    "tax_rate",          # Source data — strict audit
    "tax_amount",        # Source data — strict audit
    "product_id",
    "product_code",
    "product_name",      # Source data — strict audit
    "classification_confidence",
    "classification_source",
    "created_at",
})

# Line item classification fields — editable if provenance is HIS/MISSING
LINE_ITEM_CLASSIFICATION_FIELDS: frozenset[str] = frozenset({
    "hsn_code",
    "service_code",
    "product_category",
    "service_category",
    "line_item_type",    # GOODS/SERVICE — if inferred
})

# ── Invoice References (always editable) ─────────────────────────────────

# Credit/debit note references can always be added or modified
EDITABLE_REFERENCE_TYPES: frozenset[str] = frozenset({
    "CREDIT_NOTE",
    "DEBIT_NOTE",
})

# ── Transaction Type Rules ───────────────────────────────────────────────

# B2B <-> B2G: free swap (both have full customer details)
# B2C -> B2B or B2G: requires buyer_tin + buyer_name to be non-empty
B2B_B2G_TYPES: frozenset[str] = frozenset({"B2B", "B2G"})
B2C_UPGRADE_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "buyer_tin",
    "buyer_name",
})


def get_tenant_party_fields(direction: str) -> frozenset[str]:
    """Return the set of fields that belong to the tenant (non-editable).

    For OUTBOUND invoices, tenant is the seller.
    For INBOUND invoices, tenant is the buyer.
    """
    if direction == "OUTBOUND":
        return SELLER_PARTY_FIELDS
    elif direction == "INBOUND":
        return BUYER_PARTY_FIELDS
    return frozenset()


def is_field_editable(
    field_name: str,
    provenance: str | None,
    confidence: float | None,
    direction: str,
    *,
    is_line_item: bool = False,
) -> bool:
    """Determine if a field is editable based on provenance and rules.

    Args:
        field_name: The field to check.
        provenance: Provenance value (ORIGINAL, MISSING, HIS, etc.) or None.
        confidence: Classification confidence (0.0-1.0) or None.
        direction: Invoice direction (OUTBOUND or INBOUND).
        is_line_item: True if this is a line item field.

    Returns:
        True if the field can be edited at preview time.
    """
    if is_line_item:
        if field_name in NEVER_EDITABLE_LINE_ITEM_FIELDS:
            return False
        if field_name in LINE_ITEM_CLASSIFICATION_FIELDS:
            if provenance is None:
                return True  # Graceful degradation: no provenance = editable
            if provenance in EDITABLE_PROVENANCE:
                return True
            if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
                return True
            return False
        return False  # Unknown line item field — not editable

    # Invoice-level fields
    if field_name in ALWAYS_EDITABLE_INVOICE_FIELDS:
        return True
    if field_name in NEVER_EDITABLE_INVOICE_FIELDS:
        return False

    # Tenant party fields
    tenant_fields = get_tenant_party_fields(direction)
    if field_name in tenant_fields:
        return False

    # Provenance-gated
    if provenance is None:
        return True  # Graceful degradation: no provenance = editable + warning
    if provenance in EDITABLE_PROVENANCE:
        return True
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        return True

    return False
