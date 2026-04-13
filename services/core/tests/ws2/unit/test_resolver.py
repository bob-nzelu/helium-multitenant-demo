"""Unit tests for Phase 5 Resolver — entity resolution logic.

Tests the resolution algorithms using in-memory caches (no DB needed).
"""

import pytest

from src.processing.models import MatchType, ProductMatchType
from src.processing.name_utils import normalize_name
from src.processing.resolver import Resolver


class MockPool:
    """Mock connection pool that returns empty results."""

    class _MockConn:
        class _MockCur:
            def __init__(self):
                self.description = []
            async def fetchall(self):
                return []

        async def execute(self, *a, **kw):
            return self._MockCur()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    def connection(self):
        return self._MockConn()


@pytest.fixture
def resolver(config):
    r = Resolver(pool=MockPool(), config=config)
    return r


class TestCustomerResolutionTIN:
    @pytest.mark.asyncio
    async def test_tin_exact_match(self, resolver, pipeline_context):
        # Pre-load cache with a customer
        resolver._customers = [
            {
                "customer_id": "cust-001",
                "tin": "12345678-001",
                "rc_number": None,
                "company_name": "Dangote Cement PLC",
                "company_name_normalized": "DANGOTE CEMENT",
                "email": None, "phone": None,
                "address": None, "city": None, "state": None, "country": "NG",
            }
        ]

        result = await resolver._resolve_customer(
            name="Dangote Cement PLC",
            tin="12345678-001",
            rc_number=None,
            address=None,
            company_id="COMP-001",
        )
        assert result.customer_id == "cust-001"
        assert result.match_type == MatchType.TIN_EXACT.value
        assert result.match_confidence == 1.0
        assert result.is_provisional is False


class TestCustomerResolutionRC:
    @pytest.mark.asyncio
    async def test_rc_exact_match(self, resolver):
        resolver._customers = [
            {
                "customer_id": "cust-002",
                "tin": None,
                "rc_number": "RC123456",
                "company_name": "First Bank",
                "company_name_normalized": "FIRST BANK",
                "email": None, "phone": None,
                "address": None, "city": None, "state": None, "country": "NG",
            }
        ]

        result = await resolver._resolve_customer(
            name="First Bank PLC",
            tin=None,
            rc_number="RC123456",
            address=None,
            company_id="COMP-001",
        )
        assert result.customer_id == "cust-002"
        assert result.match_type == MatchType.RC_EXACT.value


class TestCustomerResolutionFuzzy:
    @pytest.mark.asyncio
    async def test_fuzzy_name_match(self, resolver):
        resolver._customers = [
            {
                "customer_id": "cust-003",
                "tin": None,
                "rc_number": None,
                "company_name": "Dangote Industries Limited",
                "company_name_normalized": normalize_name("Dangote Industries Limited"),
                "email": None, "phone": None,
                "address": None, "city": None, "state": None, "country": "NG",
            }
        ]

        result = await resolver._resolve_customer(
            name="Dangote Industries",  # No "Limited" suffix
            tin=None,
            rc_number=None,
            address=None,
            company_id="COMP-001",
        )
        assert result.match_type == MatchType.FUZZY_NAME.value
        assert result.match_confidence >= 0.85

    @pytest.mark.asyncio
    async def test_fuzzy_below_threshold(self, resolver):
        resolver._customers = [
            {
                "customer_id": "cust-004",
                "tin": None,
                "rc_number": None,
                "company_name": "Zenith Bank PLC",
                "company_name_normalized": normalize_name("Zenith Bank PLC"),
                "email": None, "phone": None,
                "address": None, "city": None, "state": None, "country": "NG",
            }
        ]

        result = await resolver._resolve_customer(
            name="Access Holdings Group",
            tin=None,
            rc_number=None,
            address=None,
            company_id="COMP-001",
        )
        # Too different — should create provisional
        assert result.is_provisional is True
        assert result.match_type == MatchType.NEW.value


class TestCustomerResolutionVariants:
    @pytest.mark.asyncio
    async def test_variant_name_match(self, resolver):
        resolver._customers = [
            {
                "customer_id": "cust-005",
                "tin": None,
                "rc_number": None,
                "company_name": "MTN Nigeria Communications PLC",
                "company_name_normalized": normalize_name("MTN Nigeria Communications PLC"),
                "email": None, "phone": None,
                "address": None, "city": None, "state": None, "country": "NG",
            }
        ]
        resolver._customer_variants = [
            {
                "customer_id": "cust-005",
                "name_variant": "MTN Nigeria",
                "name_variant_normalized": normalize_name("MTN Nigeria"),
            }
        ]

        result = await resolver._resolve_customer(
            name="MTN Nigeria",
            tin=None,
            rc_number=None,
            address=None,
            company_id="COMP-001",
        )
        assert result.customer_id == "cust-005"
        assert result.match_confidence >= 0.85


class TestCustomerResolutionProvisional:
    @pytest.mark.asyncio
    async def test_no_match_creates_provisional(self, resolver):
        resolver._customers = []
        resolver._customer_variants = []

        result = await resolver._resolve_customer(
            name="Brand New Company",
            tin=None,
            rc_number=None,
            address="123 New St",
            company_id="COMP-001",
        )
        assert result.is_provisional is True
        assert result.match_type == MatchType.NEW.value
        assert result.match_confidence == 0.0
        assert result.created_source == "PIPELINE_AUTO"
        assert result.company_name == "Brand New Company"


class TestInventoryResolution:
    @pytest.mark.asyncio
    async def test_helium_sku_exact(self, resolver):
        resolver._inventory["COMP-001"] = [
            {
                "product_id": "prod-001",
                "helium_sku": "HLM-TST-00001",
                "customer_sku": "CEM-001",
                "product_name": "Portland Cement",
                "product_name_normalized": normalize_name("Portland Cement"),
                "hs_code": "2523.29",
                "service_code": None,
                "product_category": "Building Materials",
                "type": "GOODS",
            }
        ]

        result = await resolver._resolve_inventory_item(
            description="Portland Cement",
            customer_sku=None,
            helium_sku="HLM-TST-00001",
            hs_code=None,
            company_id="COMP-001",
        )
        assert result.product_id == "prod-001"
        assert result.match_type == ProductMatchType.HELIUM_SKU_EXACT.value

    @pytest.mark.asyncio
    async def test_customer_sku_exact(self, resolver):
        resolver._inventory["COMP-001"] = [
            {
                "product_id": "prod-002",
                "helium_sku": None,
                "customer_sku": "NOODLE-001",
                "product_name": "Indomie 70g",
                "product_name_normalized": normalize_name("Indomie 70g"),
                "hs_code": None,
                "service_code": None,
                "product_category": None,
                "type": "GOODS",
            }
        ]

        result = await resolver._resolve_inventory_item(
            description="Indomie Noodles",
            customer_sku="NOODLE-001",
            helium_sku=None,
            hs_code=None,
            company_id="COMP-001",
        )
        assert result.match_type == ProductMatchType.CUSTOMER_SKU_EXACT.value

    @pytest.mark.asyncio
    async def test_fuzzy_product_name(self, resolver):
        resolver._inventory["COMP-001"] = [
            {
                "product_id": "prod-003",
                "helium_sku": None,
                "customer_sku": None,
                "product_name": "Portland Cement Type I",
                "product_name_normalized": normalize_name("Portland Cement Type I"),
                "hs_code": None,
                "service_code": None,
                "product_category": None,
                "type": "GOODS",
            }
        ]
        resolver._inventory_variants["COMP-001"] = []

        result = await resolver._resolve_inventory_item(
            description="Portland Cement Type 1",  # "1" vs "I"
            customer_sku=None,
            helium_sku=None,
            hs_code=None,
            company_id="COMP-001",
        )
        assert result.match_type == ProductMatchType.FUZZY_NAME.value
        assert result.match_confidence >= 0.85

    @pytest.mark.asyncio
    async def test_provisional_product(self, resolver):
        resolver._inventory["COMP-001"] = []
        resolver._inventory_variants["COMP-001"] = []

        result = await resolver._resolve_inventory_item(
            description="Completely New Product",
            customer_sku=None,
            helium_sku=None,
            hs_code="9999.99",
            company_id="COMP-001",
        )
        assert result.is_provisional is True
        assert result.hs_code == "9999.99"
        assert result.company_id == "COMP-001"


class TestResolverConfidence:
    def test_overall_confidence(self):
        from src.processing.models import (
            ResolvedCustomer,
            ResolvedInvoice,
            ResolvedLineItem,
        )

        inv = ResolvedInvoice(
            invoice_number="X",
            confidence=0.72,  # Partial from Phase 4
        )
        cust = ResolvedCustomer(
            customer_id="c1",
            match_confidence=1.0,  # TIN exact
        )
        items = [
            ResolvedLineItem(
                line_number=1,
                description="A",
                quantity="1",
                unit_price="1",
                line_total="1",
                product_match_confidence=0.92,
            ),
        ]
        confidence = Resolver._compute_overall_confidence(inv, cust, items)
        # 0.72 + (avg(1.0, 0.92) * 0.15) = 0.72 + 0.144 = 0.864
        assert 0.85 <= confidence <= 0.90
