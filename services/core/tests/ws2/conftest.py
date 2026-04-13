"""WS2 test fixtures."""

import pytest

from src.config import CoreConfig
from src.ingestion.models import ParseMetadata, ParseResult, RedFlag as WS1RedFlag
from src.processing.models import (
    CustomerConfig,
    EnrichedInvoice,
    EnrichedLineItem,
    EnrichResult,
    ExtractedCustomer,
    ExtractedProduct,
    PipelineContext,
    TransformedInvoice,
    TransformedLineItem,
    TransformResult,
)


@pytest.fixture
def config():
    return CoreConfig()


@pytest.fixture
def pipeline_context():
    return PipelineContext(
        data_uuid="01234567-abcd-7000-8000-000000000001",
        company_id="COMP-001",
        trace_id="trace-ws2-test",
        helium_user_id="user-test",
        float_id="float-test",
        session_id="sess-test",
        customer_config=CustomerConfig(
            company_prefix="TST",
            default_due_date_days=30,
            risk_level="LOW",
            default_currency="NGN",
            average_invoice_amount=50000.0,
        ),
    )


@pytest.fixture
def sample_parse_result():
    """A WS1 ParseResult with typical invoice data."""
    return ParseResult(
        file_type="excel",
        raw_data=[
            {
                "invoice_number": "INV-2026-001",
                "invoice_date": "2026-03-15",
                "total_amount": "150000.00",
                "subtotal": "139534.88",
                "tax_amount": "10465.12",
                "currency": "NGN",
                "buyer_name": "Dangote Cement PLC",
                "buyer_tin": "12345678-001",
                "buyer_address": "1 Alfred Rewane Road",
                "buyer_city": "Lagos",
                "buyer_state": "Lagos",
                "seller_name": "Test Supplier Ltd",
                "seller_tin": "98765432-001",
                "direction": "OUTBOUND",
                "transaction_type": "B2B",
                "line_items": [
                    {
                        "description": "Portland Cement Type I",
                        "quantity": "100",
                        "unit_price": "1395.35",
                        "amount": "139534.88",
                        "uom": "BAG",
                        "sku": "CEM-001",
                    },
                ],
            },
            {
                "invoice_number": "INV-2026-002",
                "invoice_date": "2026-03-16",
                "total_amount": "75000.00",
                "subtotal": "69767.44",
                "tax_amount": "5232.56",
                "buyer_name": "Nestle Nigeria",
                "buyer_tin": "11223344-002",
                "seller_name": "Test Supplier Ltd",
                "seller_tin": "98765432-001",
                "line_items": [
                    {
                        "description": "Indomie Instant Noodles 70g",
                        "quantity": "500",
                        "unit_price": "139.53",
                        "amount": "69767.44",
                        "sku": "NOODLE-001",
                    },
                ],
            },
        ],
        metadata=ParseMetadata(
            parser_type="excel",
            original_filename="invoices_march.xlsx",
            file_size_bytes=45000,
            row_count=2,
        ),
        red_flags=[
            WS1RedFlag(field_name="encoding", message="Non-UTF8 detected", severity="info"),
        ],
    )


@pytest.fixture
def sample_transform_result():
    """A TransformResult from Phase 3."""
    return TransformResult(
        invoices=[
            TransformedInvoice(
                invoice_number="INV-2026-001",
                direction="OUTBOUND",
                transaction_type="B2B",
                firs_invoice_type_code="380",
                issue_date="2026-03-15",
                total_amount="150000.00",
                tax_exclusive_amount="139534.88",
                total_tax_amount="10465.12",
                buyer_business_name="Dangote Cement PLC",
                buyer_tin="12345678-001",
                buyer_address="1 Alfred Rewane Road",
                buyer_city="Lagos",
                buyer_state="Lagos",
                seller_business_name="Test Supplier Ltd",
                seller_tin="98765432-001",
                seller_address="5 Broad Street",
                seller_city="Lagos",
                seller_state="Lagos",
                line_items=[
                    TransformedLineItem(
                        line_number=1,
                        description="Portland Cement Type I",
                        quantity="100",
                        unit_price="1395.35",
                        line_total="139534.88",
                        unit_of_measure="BAG",
                        customer_sku="CEM-001",
                        item_type="GOODS",
                    ),
                ],
            ),
        ],
        customers=[
            ExtractedCustomer(
                business_name="Dangote Cement PLC",
                tin="12345678-001",
                role="BUYER",
            ),
        ],
        inventory=[
            ExtractedProduct(
                description="Portland Cement Type I",
                customer_sku="CEM-001",
                item_type="GOODS",
            ),
        ],
    )


@pytest.fixture
def sample_enrich_result():
    """An EnrichResult from Phase 4."""
    return EnrichResult(
        invoices=[
            EnrichedInvoice(
                invoice_number="INV-2026-001",
                direction="OUTBOUND",
                transaction_type="B2B",
                total_amount="150000.00",
                tax_exclusive_amount="139534.88",
                total_tax_amount="10465.12",
                issue_date="2026-03-15",
                buyer_business_name="Dangote Cement PLC",
                buyer_tin="12345678-001",
                buyer_address="1 Alfred Rewane Road",
                buyer_city="Lagos",
                buyer_state="Lagos",
                seller_business_name="Test Supplier Ltd",
                seller_tin="98765432-001",
                seller_address_validated=True,
                buyer_address_validated=True,
                buyer_lga="Ikoyi",
                buyer_lga_code="25",
                confidence=0.72,
                line_items=[
                    EnrichedLineItem(
                        line_number=1,
                        description="Portland Cement Type I",
                        quantity="100",
                        unit_price="1395.35",
                        line_total="139534.88",
                        unit_of_measure="BAG",
                        customer_sku="CEM-001",
                        item_type="GOODS",
                        hs_code="2523.29",
                        hs_code_confidence=0.88,
                        hs_code_source="HIS",
                        category="Building Materials",
                    ),
                ],
            ),
        ],
        customers=[
            ExtractedCustomer(
                business_name="Dangote Cement PLC",
                tin="12345678-001",
                role="BUYER",
            ),
        ],
    )
