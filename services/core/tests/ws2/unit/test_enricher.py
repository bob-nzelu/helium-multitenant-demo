"""Unit tests for Phase 4 Enricher."""

import pytest

from src.processing.enricher import Enricher
from src.processing.his_client import HISStubClient
from src.processing.models import (
    EnrichedInvoice,
    EnrichedLineItem,
    TransformedInvoice,
    TransformedLineItem,
    TransformResult,
)


@pytest.fixture
def enricher():
    return Enricher(his_client=HISStubClient())


class TestEnricherBasic:
    @pytest.mark.asyncio
    async def test_enrich_single_invoice(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        assert len(result.invoices) == 1
        assert isinstance(result.invoices[0], EnrichedInvoice)

    @pytest.mark.asyncio
    async def test_enrich_preserves_invoice_data(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        inv = result.invoices[0]
        assert inv.invoice_number == "INV-2026-001"
        assert inv.total_amount == "150000.00"
        assert inv.buyer_business_name == "Dangote Cement PLC"

    @pytest.mark.asyncio
    async def test_enrich_line_items_get_hs_code(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        inv = result.invoices[0]
        assert len(inv.line_items) == 1
        li = inv.line_items[0]
        # Stub provides HS codes
        assert li.hs_code is not None or li.hs_code_source == "ORIGINAL"

    @pytest.mark.asyncio
    async def test_enrich_category(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        li = result.invoices[0].line_items[0]
        assert li.category is not None

    @pytest.mark.asyncio
    async def test_enrich_address_validation(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        inv = result.invoices[0]
        # Seller has address → should be validated
        assert inv.seller_address_validated is True
        # Buyer has address → should be validated
        assert inv.buyer_address_validated is True


class TestEnricherMetadata:
    @pytest.mark.asyncio
    async def test_api_stats(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        assert result.metadata.api_calls_total > 0
        assert result.metadata.api_calls_success > 0
        assert result.metadata.enrich_time_ms >= 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_states(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        states = result.metadata.circuit_breaker_states
        assert "hsn" in states
        assert "category" in states

    @pytest.mark.asyncio
    async def test_red_flags_carried(self, enricher, pipeline_context):
        from src.processing.models import RedFlag, TransformResult

        tr = TransformResult(
            invoices=[
                TransformedInvoice(
                    invoice_number="X",
                    total_amount="100",
                    issue_date="2026-01-01",
                    line_items=[],
                )
            ],
            red_flags=[
                RedFlag(type="test_flag", severity="info", message="From phase 3", phase="transform")
            ],
        )
        result = await enricher.enrich(tr, pipeline_context)
        assert any(f.type == "test_flag" for f in result.red_flags)


class TestEnricherConfidence:
    @pytest.mark.asyncio
    async def test_confidence_computed(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        inv = result.invoices[0]
        assert 0.0 < inv.confidence <= 0.85  # Capped at 0.85 before Phase 5

    @pytest.mark.asyncio
    async def test_confidence_with_complete_data(self, enricher, pipeline_context):
        tr = TransformResult(
            invoices=[
                TransformedInvoice(
                    invoice_number="COMPLETE-001",
                    issue_date="2026-03-15",
                    total_amount="150000",
                    tax_exclusive_amount="139534.88",
                    total_tax_amount="10465.12",
                    buyer_business_name="Dangote",
                    buyer_tin="12345678-001",
                    seller_business_name="Supplier",
                    seller_tin="98765432-001",
                    confidence=0.9,  # High textract confidence
                    line_items=[],
                )
            ],
        )
        result = await enricher.enrich(tr, pipeline_context)
        assert result.invoices[0].confidence > 0.5


class TestEnricherEmpty:
    @pytest.mark.asyncio
    async def test_empty_invoices(self, enricher, pipeline_context):
        result = await enricher.enrich(TransformResult(), pipeline_context)
        assert len(result.invoices) == 0
        assert result.metadata.api_calls_total == 0


class TestEnricherPassthrough:
    @pytest.mark.asyncio
    async def test_customers_passed_through(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        assert len(result.customers) == len(sample_transform_result.customers)

    @pytest.mark.asyncio
    async def test_inventory_passed_through(self, enricher, sample_transform_result, pipeline_context):
        result = await enricher.enrich(sample_transform_result, pipeline_context)
        assert len(result.inventory) == len(sample_transform_result.inventory)
