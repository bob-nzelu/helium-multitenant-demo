"""Unit tests for Phase 3 Transformer (default path, no Transforma)."""

import pytest

from src.processing.models import PipelineContext, CustomerConfig, RedFlag
from src.processing.transformer import Transformer, TransformError


class TestTransformerDefault:
    """Tests using the built-in default transformer (no Transforma library)."""

    @pytest.fixture
    def transformer(self):
        """Transformer with no DB pool (uses default transform)."""
        # Pass None for pool — default transform doesn't need DB
        t = Transformer(pool=None, config=None)
        t._transforma_available = False
        return t

    @pytest.mark.asyncio
    async def test_transform_basic(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        assert len(result.invoices) == 2
        assert result.invoices[0].invoice_number == "INV-2026-001"
        assert result.invoices[1].invoice_number == "INV-2026-002"

    @pytest.mark.asyncio
    async def test_transform_extracts_customers(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        assert len(result.customers) >= 1
        buyer_names = [c.business_name for c in result.customers]
        assert "Dangote Cement PLC" in buyer_names

    @pytest.mark.asyncio
    async def test_transform_extracts_products(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        assert len(result.inventory) >= 1
        descriptions = [p.description for p in result.inventory]
        assert "Portland Cement Type I" in descriptions

    @pytest.mark.asyncio
    async def test_transform_line_items(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        inv = result.invoices[0]
        assert len(inv.line_items) == 1
        li = inv.line_items[0]
        assert li.description == "Portland Cement Type I"
        assert li.quantity == "100"
        assert li.unit_price == "1395.35"
        assert li.customer_sku == "CEM-001"

    @pytest.mark.asyncio
    async def test_transform_carries_parse_flags(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        parse_flags = [f for f in result.red_flags if f.phase == "parse"]
        assert len(parse_flags) == 1
        assert "Non-UTF8" in parse_flags[0].message

    @pytest.mark.asyncio
    async def test_transform_metadata(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        assert result.metadata.is_default_script is True
        assert result.metadata.invoice_count == 2
        assert result.metadata.transform_time_ms >= 0

    @pytest.mark.asyncio
    async def test_transform_amounts(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        inv = result.invoices[0]
        assert inv.total_amount == "150000.00"
        assert inv.tax_exclusive_amount == "139534.88"
        assert inv.total_tax_amount == "10465.12"

    @pytest.mark.asyncio
    async def test_transform_buyer_fields(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        inv = result.invoices[0]
        assert inv.buyer_business_name == "Dangote Cement PLC"
        assert inv.buyer_tin == "12345678-001"
        assert inv.buyer_city == "Lagos"

    @pytest.mark.asyncio
    async def test_transform_seller_fields(self, transformer, sample_parse_result, pipeline_context):
        result = await transformer.transform(sample_parse_result, pipeline_context)
        inv = result.invoices[0]
        assert inv.seller_business_name == "Test Supplier Ltd"
        assert inv.seller_tin == "98765432-001"


class TestTransformerHLMPassthrough:
    @pytest.fixture
    def transformer(self):
        t = Transformer(pool=None, config=None)
        t._transforma_available = False
        return t

    @pytest.mark.asyncio
    async def test_hlm_passthrough(self, transformer, pipeline_context):
        from src.ingestion.models import ParseMetadata, ParseResult

        hlm_result = ParseResult(
            file_type="hlm",
            raw_data={
                "invoices": [
                    {"invoice_number": "HLM-001", "total_amount": "50000"},
                    {"invoice_number": "HLM-002", "total_amount": "75000"},
                ],
            },
            metadata=ParseMetadata(parser_type="hlm", original_filename="data.hlm"),
            is_hlm=True,
        )

        result = await transformer.transform(hlm_result, pipeline_context)
        assert len(result.invoices) == 2
        assert result.invoices[0].invoice_number == "HLM-001"
        assert result.metadata.transform_time_ms >= 0


class TestTransformerEdgeCases:
    @pytest.fixture
    def transformer(self):
        t = Transformer(pool=None, config=None)
        t._transforma_available = False
        return t

    @pytest.mark.asyncio
    async def test_empty_data(self, transformer, pipeline_context):
        from src.ingestion.models import ParseMetadata, ParseResult

        empty = ParseResult(
            file_type="csv",
            raw_data=[],
            metadata=ParseMetadata(parser_type="csv", original_filename="empty.csv"),
        )
        result = await transformer.transform(empty, pipeline_context)
        assert len(result.invoices) == 0

    @pytest.mark.asyncio
    async def test_non_dict_rows_skipped(self, transformer, pipeline_context):
        from src.ingestion.models import ParseMetadata, ParseResult

        bad = ParseResult(
            file_type="json",
            raw_data=["string1", "string2", 42],
            metadata=ParseMetadata(parser_type="json", original_filename="bad.json"),
        )
        result = await transformer.transform(bad, pipeline_context)
        assert len(result.invoices) == 0

    @pytest.mark.asyncio
    async def test_missing_invoice_number_flag(self, transformer, pipeline_context):
        from src.ingestion.models import ParseMetadata, ParseResult

        no_num = ParseResult(
            file_type="csv",
            raw_data=[{"total_amount": "100"}],
            metadata=ParseMetadata(parser_type="csv", original_filename="no_num.csv"),
        )
        result = await transformer.transform(no_num, pipeline_context)
        # Should still create invoice with generated number
        assert len(result.invoices) == 1

    @pytest.mark.asyncio
    async def test_zero_amount_flag(self, transformer, pipeline_context):
        from src.ingestion.models import ParseMetadata, ParseResult

        zero = ParseResult(
            file_type="csv",
            raw_data=[{"invoice_number": "INV-X", "total_amount": "0"}],
            metadata=ParseMetadata(parser_type="csv", original_filename="zero.csv"),
        )
        result = await transformer.transform(zero, pipeline_context)
        zero_flags = [f for f in result.red_flags if f.type == "zero_amount"]
        assert len(zero_flags) == 1
