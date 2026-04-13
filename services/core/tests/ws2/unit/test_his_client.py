"""Unit tests for HIS stub client."""

import pytest

from src.processing.his_client import (
    AddressResult,
    CategoryResult,
    HISStubClient,
    HSNResult,
    ServiceResult,
)


@pytest.fixture
def stub_client():
    return HISStubClient()


class TestHSNClassification:
    @pytest.mark.asyncio
    async def test_returns_hs_code(self, stub_client):
        result = await stub_client.classify_hsn("Portland Cement Type I")
        assert isinstance(result, HSNResult)
        assert result.hs_code is not None
        assert "." in result.hs_code  # XXXX.XX format
        assert result.confidence > 0
        assert result.source == "HIS"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_different_descriptions_may_differ(self, stub_client):
        r1 = await stub_client.classify_hsn("Laptop Computer")
        r2 = await stub_client.classify_hsn("Milk Powder")
        # Stub uses hash-based selection, so different inputs may get different codes
        assert r1.hs_code is not None
        assert r2.hs_code is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self, stub_client):
        # Force circuit open
        breaker = stub_client._breakers["hsn"]
        for _ in range(5):
            breaker.record_failure()
        assert breaker.state == "OPEN"

        result = await stub_client.classify_hsn("Test")
        assert result.error == "Circuit breaker open"
        assert result.hs_code is None


class TestCategoryClassification:
    @pytest.mark.asyncio
    async def test_returns_category(self, stub_client):
        result = await stub_client.classify_category("Cement", hs_code="2523.29")
        assert isinstance(result, CategoryResult)
        assert result.category is not None
        assert result.item_type in ("GOODS", "SERVICE", None)
        assert result.error is None

    @pytest.mark.asyncio
    async def test_without_hs_code(self, stub_client):
        result = await stub_client.classify_category("Consulting services")
        assert result.category is not None


class TestServiceClassification:
    @pytest.mark.asyncio
    async def test_returns_service_code(self, stub_client):
        result = await stub_client.classify_service("Management consulting")
        assert isinstance(result, ServiceResult)
        assert result.service_code is not None
        assert result.confidence > 0


class TestAddressValidation:
    @pytest.mark.asyncio
    async def test_returns_valid_address(self, stub_client):
        result = await stub_client.validate_address(
            "1 Alfred Rewane Road", city="Ikoyi", state="Lagos"
        )
        assert isinstance(result, AddressResult)
        assert result.valid is True
        assert result.state_code is not None
        assert result.lga is not None

    @pytest.mark.asyncio
    async def test_without_city_state(self, stub_client):
        result = await stub_client.validate_address("Some random address")
        assert result.valid is True


class TestCircuitStates:
    def test_all_circuits_closed(self, stub_client):
        states = stub_client.circuit_states
        assert set(states.keys()) == {"hsn", "category", "service", "address"}
        for state in states.values():
            assert state == "CLOSED"


class TestClose:
    @pytest.mark.asyncio
    async def test_close_is_noop(self, stub_client):
        await stub_client.close()  # Should not raise
