"""
HIS (Helium Intelligence Service) Client — Stub implementation.

Per DEC-WS2-004: HIS enrichment API is not yet available.
This module provides the interface and a stub client that returns
plausible mock data for development and testing.

When HIS comes online, replace HISStubClient with a real httpx-based client.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from .circuit_breaker import CircuitBreaker

logger = structlog.get_logger()


# ── Result types ───────────────────────────────────────────────────────────


@dataclass
class HSNResult:
    hs_code: str | None = None
    description: str | None = None
    confidence: float = 0.0
    source: str = "HIS"
    error: str | None = None


@dataclass
class CategoryResult:
    category: str | None = None
    subcategory: str | None = None
    item_type: str | None = None  # "GOODS" | "SERVICE"
    confidence: float = 0.0
    error: str | None = None


@dataclass
class ServiceResult:
    service_code: str | None = None
    description: str | None = None
    confidence: float = 0.0
    error: str | None = None


@dataclass
class AddressResult:
    valid: bool = False
    normalized_address: str | None = None
    lga: str | None = None
    lga_code: str | None = None
    state: str | None = None
    state_code: str | None = None
    confidence: float = 0.0
    error: str | None = None


# ── Protocol (interface) ───────────────────────────────────────────────────


class HISClientProtocol(Protocol):
    """Interface for HIS enrichment clients."""

    async def classify_hsn(
        self, description: str, context: dict[str, Any] | None = None
    ) -> HSNResult: ...

    async def classify_category(
        self, description: str, hs_code: str | None = None
    ) -> CategoryResult: ...

    async def classify_service(
        self, description: str
    ) -> ServiceResult: ...

    async def validate_address(
        self, address: str, city: str | None = None, state: str | None = None
    ) -> AddressResult: ...

    @property
    def circuit_states(self) -> dict[str, str]: ...

    async def close(self) -> None: ...


# ── Stub implementation ───────────────────────────────────────────────────


# Common HS codes for stub responses
_STUB_HS_CODES = {
    "GOODS": [
        ("8471.30", "Portable digital computers"),
        ("0402.10", "Milk and cream, concentrated"),
        ("3004.90", "Medicaments, in dosage form"),
        ("2710.19", "Petroleum oils"),
        ("7308.90", "Iron/steel structures"),
        ("8528.72", "Television reception apparatus"),
    ],
    "SERVICE": [
        ("9983.11", "Management consulting"),
        ("9971.10", "Financial services"),
        ("9985.11", "Transport of goods"),
        ("9973.21", "Real estate services"),
    ],
}


class HISStubClient:
    """
    Stub HIS client returning plausible mock enrichment data.

    Each endpoint has its own circuit breaker for testing integration.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self._breakers = {
            "hsn": CircuitBreaker("hsn", failure_threshold, recovery_timeout, success_threshold),
            "category": CircuitBreaker("category", failure_threshold, recovery_timeout, success_threshold),
            "service": CircuitBreaker("service", failure_threshold, recovery_timeout, success_threshold),
            "address": CircuitBreaker("address", failure_threshold, recovery_timeout, success_threshold),
        }

    async def classify_hsn(
        self, description: str, context: dict[str, Any] | None = None
    ) -> HSNResult:
        breaker = self._breakers["hsn"]
        if not breaker.is_available:
            return HSNResult(error="Circuit breaker open")

        # Stub: pick a plausible HS code
        codes = _STUB_HS_CODES["GOODS"]
        code, desc = codes[hash(description) % len(codes)]
        breaker.record_success()
        return HSNResult(
            hs_code=code,
            description=desc,
            confidence=round(random.uniform(0.70, 0.95), 2),
            source="HIS",
        )

    async def classify_category(
        self, description: str, hs_code: str | None = None
    ) -> CategoryResult:
        breaker = self._breakers["category"]
        if not breaker.is_available:
            return CategoryResult(error="Circuit breaker open")

        breaker.record_success()
        return CategoryResult(
            category="General Merchandise",
            subcategory="Consumer Goods",
            item_type="GOODS",
            confidence=round(random.uniform(0.65, 0.90), 2),
        )

    async def classify_service(self, description: str) -> ServiceResult:
        breaker = self._breakers["service"]
        if not breaker.is_available:
            return ServiceResult(error="Circuit breaker open")

        codes = _STUB_HS_CODES["SERVICE"]
        code, desc = codes[hash(description) % len(codes)]
        breaker.record_success()
        return ServiceResult(
            service_code=code,
            description=desc,
            confidence=round(random.uniform(0.60, 0.85), 2),
        )

    async def validate_address(
        self, address: str, city: str | None = None, state: str | None = None
    ) -> AddressResult:
        breaker = self._breakers["address"]
        if not breaker.is_available:
            return AddressResult(error="Circuit breaker open")

        breaker.record_success()
        return AddressResult(
            valid=True,
            normalized_address=address,
            lga=city or "Ikeja",
            lga_code="25",
            state=state or "Lagos",
            state_code="25",
            confidence=round(random.uniform(0.70, 0.95), 2),
        )

    @property
    def circuit_states(self) -> dict[str, str]:
        return {name: b.state for name, b in self._breakers.items()}

    async def close(self) -> None:
        pass
