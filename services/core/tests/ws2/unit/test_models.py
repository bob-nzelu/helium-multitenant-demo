"""Unit tests for WS2 processing models."""

import pytest

from src.processing.models import (
    CustomerConfig,
    EnrichedInvoice,
    EnrichedLineItem,
    EnrichResult,
    ExtractedCustomer,
    ExtractedProduct,
    MatchType,
    PipelineContext,
    ProductMatchType,
    ProvisionalRecords,
    RedFlag,
    RedFlagSeverity,
    ResolvedCustomer,
    ResolvedInvoice,
    ResolvedLineItem,
    ResolvedProduct,
    ResolveResult,
    TransformedInvoice,
    TransformedLineItem,
    TransformMetadata,
    TransformResult,
)


class TestRedFlag:
    def test_create(self):
        rf = RedFlag(type="missing_tin", severity="warning", message="TIN missing", phase="transform")
        assert rf.type == "missing_tin"
        assert rf.severity == "warning"
        assert rf.auto_resolvable is False

    def test_with_details(self):
        rf = RedFlag(
            type="suspicious_amount",
            severity="error",
            message="Amount exceeds 10x average",
            phase="transform",
            invoice_index=0,
            field="total_amount",
            auto_resolvable=False,
            suggested_value=None,
            details={"actual": 5000000, "average": 50000},
        )
        assert rf.details["actual"] == 5000000
        assert rf.invoice_index == 0


class TestEnums:
    def test_severity_values(self):
        assert RedFlagSeverity.ERROR == "error"
        assert RedFlagSeverity.WARNING == "warning"
        assert RedFlagSeverity.INFO == "info"

    def test_match_types(self):
        assert MatchType.TIN_EXACT == "TIN_EXACT"
        assert MatchType.FUZZY_NAME == "FUZZY_NAME"
        assert MatchType.NEW == "NEW"

    def test_product_match_types(self):
        assert ProductMatchType.HELIUM_SKU_EXACT == "HELIUM_SKU_EXACT"
        assert ProductMatchType.CUSTOMER_SKU_EXACT == "CUSTOMER_SKU_EXACT"


class TestPipelineContext:
    def test_create(self, pipeline_context):
        assert pipeline_context.data_uuid.startswith("01234567")
        assert pipeline_context.company_id == "COMP-001"
        assert pipeline_context.customer_config.company_prefix == "TST"

    def test_defaults(self):
        ctx = PipelineContext(data_uuid="x", company_id="y", trace_id="z")
        assert ctx.helium_user_id == ""
        assert ctx.immediate_processing is False


class TestTransformResult:
    def test_empty(self):
        result = TransformResult()
        assert result.invoices == []
        assert result.customers == []
        assert result.metadata.invoice_count == 0

    def test_with_invoices(self):
        inv = TransformedInvoice(invoice_number="INV-001", total_amount="100.00")
        result = TransformResult(invoices=[inv])
        assert len(result.invoices) == 1
        assert result.invoices[0].invoice_number == "INV-001"

    def test_line_items(self):
        li = TransformedLineItem(
            line_number=1,
            description="Widget",
            quantity="10",
            unit_price="5.50",
            line_total="55.00",
        )
        assert li.line_number == 1
        assert li.unit_of_measure is None


class TestEnrichResult:
    def test_enriched_line_item_inheritance(self):
        li = EnrichedLineItem(
            line_number=1,
            description="Cement",
            quantity="100",
            unit_price="10",
            line_total="1000",
            hs_code="2523.29",
            hs_code_confidence=0.88,
            hs_code_source="HIS",
            category="Building Materials",
        )
        assert li.hs_code == "2523.29"
        assert li.hs_code_source == "HIS"
        assert li.description == "Cement"

    def test_enriched_invoice(self):
        inv = EnrichedInvoice(
            invoice_number="INV-001",
            seller_address_validated=True,
            buyer_lga="Ikeja",
            buyer_lga_code="25",
        )
        assert inv.seller_address_validated is True
        assert inv.buyer_lga == "Ikeja"


class TestResolveResult:
    def test_provisional_records(self):
        pr = ProvisionalRecords(
            new_customers=[ResolvedCustomer(customer_id="c1", is_provisional=True)],
            new_products=[
                ResolvedProduct(product_id="p1", is_provisional=True),
                ResolvedProduct(product_id="p2", is_provisional=True),
            ],
        )
        assert pr.total_new == 3

    def test_resolved_customer(self):
        c = ResolvedCustomer(
            customer_id="cust-123",
            company_name="Dangote",
            match_type="TIN_EXACT",
            match_confidence=1.0,
        )
        assert c.is_provisional is False
        assert c.match_confidence == 1.0

    def test_resolved_product(self):
        p = ResolvedProduct(
            product_id="prod-456",
            product_name="Cement",
            match_type="FUZZY_NAME",
            match_confidence=0.91,
            company_id="COMP-001",
        )
        assert p.is_provisional is False

    def test_resolved_invoice(self):
        inv = ResolvedInvoice(
            invoice_number="INV-001",
            customer_id="cust-123",
            customer_match_type="TIN_EXACT",
            customer_match_confidence=1.0,
            overall_confidence=0.92,
        )
        assert inv.customer_id == "cust-123"
        assert inv.overall_confidence == 0.92

    def test_extracted_customer(self):
        c = ExtractedCustomer(business_name="Test Co", tin="12345678-001")
        assert c.role == "BUYER"
        assert c.country == "NG"

    def test_extracted_product(self):
        p = ExtractedProduct(description="Widget", item_type="GOODS")
        assert p.source_invoice_index == 0
