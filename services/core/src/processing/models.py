"""
WS2 Processing Pipeline Data Models

Dataclasses for pipeline context, phase outputs, and entity resolution.
These flow WS1 ParseResult → Phase 3 → Phase 4 → Phase 5 → WS3.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


# ── Enums ──────────────────────────────────────────────────────────────────


class RedFlagSeverity(str, enum.Enum):
    ERROR = "error"       # Blocks finalization
    WARNING = "warning"   # Flagged for review
    INFO = "info"         # Informational


class MatchType(str, enum.Enum):
    TIN_EXACT = "TIN_EXACT"
    RC_EXACT = "RC_EXACT"
    FUZZY_NAME = "FUZZY_NAME"
    VARIANT_NAME = "VARIANT_NAME"
    NEW = "NEW"


class ProductMatchType(str, enum.Enum):
    HELIUM_SKU_EXACT = "HELIUM_SKU_EXACT"
    CUSTOMER_SKU_EXACT = "CUSTOMER_SKU_EXACT"
    FUZZY_NAME = "FUZZY_NAME"
    NEW = "NEW"


# ── Shared ─────────────────────────────────────────────────────────────────


@dataclass
class RedFlag:
    """Issue found during processing. Accumulated across all phases."""

    type: str
    severity: str           # RedFlagSeverity value
    message: str
    phase: str              # "parse" | "transform" | "enrich" | "resolve"
    invoice_index: int | None = None
    line_item_index: int | None = None
    field: str | None = None
    auto_resolvable: bool = False
    suggested_value: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class PipelineContext:
    """Shared context passed through all pipeline phases."""

    data_uuid: str
    company_id: str
    trace_id: str
    helium_user_id: str = ""
    float_id: str = ""
    session_id: str = ""
    immediate_processing: bool = False
    customer_config: CustomerConfig | None = None
    created_at: str = ""


@dataclass
class CustomerConfig:
    """Per-company processing configuration."""

    company_prefix: str = "HEL"
    default_due_date_days: int = 30
    risk_level: str = "LOW"
    default_currency: str = "NGN"
    average_invoice_amount: float | None = None


# ── Phase 3 Output ─────────────────────────────────────────────────────────


@dataclass
class TransformedLineItem:
    """A single line item on a transformed invoice."""

    line_number: int
    description: str
    quantity: str               # Decimal as string
    unit_price: str             # Decimal as string
    line_total: str             # Decimal as string
    unit_of_measure: str | None = None
    tax_amount: str | None = None
    tax_rate: str | None = None
    hs_code: str | None = None
    customer_sku: str | None = None
    helium_sku: str | None = None
    item_type: str | None = None  # "GOODS" | "SERVICE"


@dataclass
class TransformedInvoice:
    """A single invoice in FIRS-compliant format (Phase 3 output)."""

    # Identity
    invoice_number: str
    helium_invoice_no: str = ""

    # Classification
    direction: str = "OUTBOUND"
    document_type: str = "COMMERCIAL_INVOICE"
    transaction_type: str = "B2B"
    firs_invoice_type_code: str = "380"

    # Dates
    issue_date: str = ""
    due_date: str | None = None

    # Amounts
    currency_code: str = "NGN"
    total_amount: str = "0"
    tax_exclusive_amount: str = "0"
    total_tax_amount: str = "0"

    # Seller
    seller_business_name: str | None = None
    seller_tin: str | None = None
    seller_rc_number: str | None = None
    seller_email: str | None = None
    seller_phone: str | None = None
    seller_address: str | None = None
    seller_city: str | None = None
    seller_state: str | None = None
    seller_country: str | None = "NG"

    # Buyer
    buyer_business_name: str | None = None
    buyer_tin: str | None = None
    buyer_rc_number: str | None = None
    buyer_email: str | None = None
    buyer_phone: str | None = None
    buyer_address: str | None = None
    buyer_city: str | None = None
    buyer_state: str | None = None
    buyer_country: str | None = "NG"

    # Line items
    line_items: list[TransformedLineItem] = field(default_factory=list)

    # Metadata
    source_file_uuid: str = ""
    stream_type: str | None = None
    confidence: float = 0.0
    red_flags: list[RedFlag] = field(default_factory=list)


@dataclass
class ExtractedCustomer:
    """Customer data extracted from invoice, pre-resolution."""

    business_name: str | None = None
    tin: str | None = None
    rc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = "NG"
    role: str = "BUYER"  # "SELLER" | "BUYER"


@dataclass
class ExtractedProduct:
    """Product data extracted from line items, pre-resolution."""

    description: str = ""
    customer_sku: str | None = None
    hs_code: str | None = None
    unit_of_measure: str | None = None
    item_type: str | None = None
    source_invoice_index: int = 0
    source_line_item_index: int = 0


@dataclass
class TransformMetadata:
    """Metadata about the transformation phase."""

    script_id: str | None = None
    script_version: int | None = None
    is_default_script: bool = True
    stream_type: str | None = None
    transform_time_ms: int = 0
    invoice_count: int = 0
    line_item_count: int = 0


@dataclass
class TransformResult:
    """Output from Phase 3 (TRANSFORM). Input to Phase 4."""

    invoices: list[TransformedInvoice] = field(default_factory=list)
    customers: list[ExtractedCustomer] = field(default_factory=list)
    inventory: list[ExtractedProduct] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    metadata: TransformMetadata = field(default_factory=TransformMetadata)


# ── Phase 4 Output ─────────────────────────────────────────────────────────


@dataclass
class EnrichedLineItem(TransformedLineItem):
    """TransformedLineItem with enrichment data added."""

    hs_code_confidence: float | None = None
    hs_code_source: str = "ORIGINAL"  # "ORIGINAL" | "HIS" | "MANUAL"
    category: str | None = None
    subcategory: str | None = None
    category_confidence: float | None = None
    service_code: str | None = None
    service_code_confidence: float | None = None


@dataclass
class EnrichedInvoice(TransformedInvoice):
    """TransformedInvoice with enrichment data added."""

    seller_address_validated: bool = False
    buyer_address_validated: bool = False
    seller_lga: str | None = None
    seller_lga_code: str | None = None
    seller_state_code: str | None = None
    buyer_lga: str | None = None
    buyer_lga_code: str | None = None
    buyer_state_code: str | None = None
    enrichment_sources: dict[str, str] = field(default_factory=dict)


@dataclass
class APIStats:
    """Per-endpoint API call statistics."""

    hsn_calls: int = 0
    hsn_success: int = 0
    hsn_avg_latency_ms: float = 0.0
    category_calls: int = 0
    category_success: int = 0
    category_avg_latency_ms: float = 0.0
    address_calls: int = 0
    address_success: int = 0
    address_avg_latency_ms: float = 0.0


@dataclass
class EnrichMetadata:
    """Metadata about the enrichment phase."""

    enrich_time_ms: int = 0
    api_calls_total: int = 0
    api_calls_success: int = 0
    api_calls_failed: int = 0
    circuit_breaker_states: dict[str, str] = field(default_factory=dict)


@dataclass
class EnrichResult:
    """Output from Phase 4 (ENRICH). Input to Phase 5."""

    invoices: list[EnrichedInvoice] = field(default_factory=list)
    customers: list[ExtractedCustomer] = field(default_factory=list)
    inventory: list[ExtractedProduct] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    metadata: EnrichMetadata = field(default_factory=EnrichMetadata)
    api_stats: APIStats = field(default_factory=APIStats)


# ── Phase 5 Output ─────────────────────────────────────────────────────────


@dataclass
class ResolvedLineItem(EnrichedLineItem):
    """EnrichedLineItem with inventory resolution."""

    product_id: str | None = None
    product_match_type: str = "NEW"
    product_match_confidence: float = 0.0


@dataclass
class ResolvedInvoice(EnrichedInvoice):
    """EnrichedInvoice with entity resolution."""

    customer_id: str | None = None
    customer_match_type: str = "NEW"
    customer_match_confidence: float = 0.0
    overall_confidence: float = 0.0
    field_provenance: dict[str, str] = field(default_factory=dict)  # Populated by Transforma


@dataclass
class ResolvedCustomer:
    """Customer record after resolution."""

    customer_id: str = ""
    company_name: str = ""
    company_name_normalized: str = ""
    tin: str | None = None
    rc_number: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = "NG"
    customer_type: str = "CORPORATE"  # Populated by Transforma; fallback CORPORATE
    is_provisional: bool = False
    match_type: str = "NEW"
    match_confidence: float = 0.0
    created_source: str | None = None


@dataclass
class ResolvedProduct:
    """Inventory record after resolution."""

    product_id: str = ""
    product_name: str = ""
    product_name_normalized: str = ""
    helium_sku: str | None = None
    customer_sku: str | None = None
    hs_code: str | None = None
    service_code: str | None = None
    category: str | None = None
    item_type: str | None = None  # "GOODS" | "SERVICE"
    vat_treatment: str = "STANDARD"  # Populated by Transforma; fallback STANDARD
    company_id: str = ""
    is_provisional: bool = False
    match_type: str = "NEW"
    match_confidence: float = 0.0
    created_source: str | None = None


@dataclass
class ResolveMetadata:
    """Metadata about the resolution phase."""

    resolve_time_ms: int = 0
    customers_matched: int = 0
    customers_created: int = 0
    products_matched: int = 0
    products_created: int = 0
    avg_customer_confidence: float = 0.0
    avg_product_confidence: float = 0.0


@dataclass
class ProvisionalRecords:
    """New records created during resolution, pending finalization."""

    new_customers: list[ResolvedCustomer] = field(default_factory=list)
    new_products: list[ResolvedProduct] = field(default_factory=list)

    @property
    def total_new(self) -> int:
        return len(self.new_customers) + len(self.new_products)


@dataclass
class ResolveResult:
    """Output from Phase 5 (RESOLVE). Passed to WS3."""

    invoices: list[ResolvedInvoice] = field(default_factory=list)
    customers: list[ResolvedCustomer] = field(default_factory=list)
    inventory: list[ResolvedProduct] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    metadata: ResolveMetadata = field(default_factory=ResolveMetadata)
    provisional_records: ProvisionalRecords = field(
        default_factory=ProvisionalRecords
    )
