"""
WS3 Orchestrator — Shared test fixtures.

Provides mocks for SSE, HeartBeat blob client, parser registry,
transformer/enricher/resolver, DB pool, and sample data factories.

NOTE: `helium_formats.hlx.crypto` is only present in the worktree copy of
helium_formats, not in the pip-installed version. We stub it into sys.modules
before any src import so preview_generator.py (and its importers) can load.
"""
from __future__ import annotations

import sys
import types
import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Stub helium_formats.hlx.crypto (not present in installed package)
# ---------------------------------------------------------------------------


def _install_crypto_stub() -> None:
    """Provide a minimal helium_formats.hlx.crypto stub for tests."""
    if "helium_formats.hlx.crypto" not in sys.modules:
        mod = types.ModuleType("helium_formats.hlx.crypto")

        def _encrypt_hlx(data: bytes, company_id: str) -> bytes:
            return b"stubenc:" + data

        def _decrypt_hlx(data: bytes, company_id: str) -> bytes:
            prefix = b"stubenc:"
            return data[len(prefix):]

        mod.encrypt_hlx = _encrypt_hlx
        mod.decrypt_hlx = _decrypt_hlx
        sys.modules["helium_formats.hlx.crypto"] = mod
        # Also attach to the hlx package if already imported
        import helium_formats.hlx as _hlx_pkg
        _hlx_pkg.crypto = mod


_install_crypto_stub()

from src.config import CoreConfig
from src.ingestion.models import BlobResponse, ParseMetadata, ParseResult
from src.processing.models import (
    EnrichResult,
    EnrichedInvoice,
    PipelineContext,
    RedFlag,
    ResolveResult,
    ResolvedInvoice,
    TransformResult,
    TransformedInvoice,
)


# ---------------------------------------------------------------------------
# SSE manager
# ---------------------------------------------------------------------------


class _CapturingSSEManager:
    """Records every published SSEEvent for assertion in tests."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:  # noqa: D401
        self.published.append(event)

    def reset(self) -> None:
        self.published.clear()

    def events_of_type(self, event_type: str) -> list[Any]:
        return [e for e in self.published if e.event_type == event_type]


@pytest.fixture
def mock_sse_manager() -> _CapturingSSEManager:
    return _CapturingSSEManager()


# ---------------------------------------------------------------------------
# HeartBeat blob client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_heartbeat_client() -> AsyncMock:
    """Returns fake BlobResponse on fetch_blob; records calls on upload_blob."""
    client = AsyncMock()

    async def _fetch_blob(queue_entry_blob_uuid: str) -> BlobResponse:
        return BlobResponse(
            content=b"fake-excel-content",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="invoices.xlsx",
            size=18,
            blob_hash="abc123",
        )

    async def _upload_blob(
        blob_uuid: str,
        filename: str,
        data: bytes,
        content_type: str,
        company_id: str,
    ) -> None:
        client.uploaded_blobs.append(
            {
                "blob_uuid": blob_uuid,
                "filename": filename,
                "size": len(data),
                "company_id": company_id,
            }
        )

    client.uploaded_blobs: list[dict] = []
    client.fetch_blob = AsyncMock(side_effect=_fetch_blob)
    client.upload_blob = AsyncMock(side_effect=_upload_blob)
    return client


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------


def _make_parse_result(n_invoices: int = 3) -> ParseResult:
    rows = [
        {
            "invoice_number": f"INV-{i:04d}",
            "total_amount": str(1000 * (i + 1)),
        }
        for i in range(n_invoices)
    ]
    return ParseResult(
        file_type="excel",
        raw_data=rows,
        metadata=ParseMetadata(
            parser_type="excel",
            original_filename="invoices.xlsx",
            row_count=n_invoices,
        ),
        is_hlm=False,
        file_hash="",
        red_flags=[],
    )


@pytest.fixture
def mock_parser_registry() -> MagicMock:
    """Returns a registry whose get() yields a mock parser producing 3 invoices."""
    registry = MagicMock()
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=_make_parse_result(3))
    registry.get = MagicMock(return_value=parser)
    return registry


# ---------------------------------------------------------------------------
# Sample invoice factories
# ---------------------------------------------------------------------------


def make_resolved_invoice(index: int = 0, **overrides) -> ResolvedInvoice:
    """Build a minimal ResolvedInvoice for testing."""
    inv = ResolvedInvoice(
        invoice_number=f"INV-{index:04d}",
        helium_invoice_no=f"HEL-{index:04d}",
        direction="OUTBOUND",
        document_type="COMMERCIAL_INVOICE",
        transaction_type="B2B",
        firs_invoice_type_code="380",
        issue_date="2026-03-20",
        currency_code="NGN",
        total_amount=str(5000 + index * 100),
        tax_exclusive_amount=str(4500 + index * 100),
        total_tax_amount="500",
        seller_business_name="GreyHouse Trading Ltd",
        seller_tin="12345678-0001",
        buyer_business_name=f"Buyer Corp {index}",
        buyer_tin=None,
        buyer_address="1 Broad Street, Lagos",
        overall_confidence=0.95,
        customer_id=None,
        customer_match_type="NEW",
        customer_match_confidence=0.0,
    )
    for k, v in overrides.items():
        object.__setattr__(inv, k, v)
    return inv


@pytest.fixture
def sample_invoices():
    """Factory fixture: sample_invoices(n) -> list[ResolvedInvoice]."""
    def _make(n: int = 3) -> list[ResolvedInvoice]:
        return [make_resolved_invoice(i) for i in range(n)]
    return _make


@pytest.fixture
def sample_queue_entry() -> dict:
    return {
        "queue_id": "q-0001",
        "data_uuid": "data-uuid-0001",
        "blob_uuid": "blob-uuid-0001",
        "company_id": "COMP-001",
        "trace_id": "trace-abc",
        "uploaded_by": "user-001",
        "status": "PENDING",
        "immediate_processing": False,
    }


@pytest.fixture
def pipeline_context() -> PipelineContext:
    return PipelineContext(
        data_uuid="data-uuid-0001",
        company_id="COMP-001",
        trace_id="trace-abc",
        helium_user_id="user-001",
        immediate_processing=False,
    )


# ---------------------------------------------------------------------------
# Transformer / Enricher / Resolver mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transformer() -> AsyncMock:
    transformer = AsyncMock()

    async def _transform(parse_result, context):
        invoices = [
            TransformedInvoice(
                invoice_number=f"INV-{i:04d}",
                total_amount=str(5000 + i * 100),
            )
            for i in range(3)
        ]
        return TransformResult(invoices=invoices)

    transformer.transform = AsyncMock(side_effect=_transform)
    return transformer


@pytest.fixture
def mock_enricher() -> AsyncMock:
    enricher = AsyncMock()

    async def _enrich(transform_result, context):
        invoices = [
            EnrichedInvoice(
                invoice_number=inv.invoice_number,
                total_amount=inv.total_amount,
            )
            for inv in transform_result.invoices
        ]
        return EnrichResult(invoices=invoices)

    enricher.enrich = AsyncMock(side_effect=_enrich)
    return enricher


@pytest.fixture
def mock_resolver() -> AsyncMock:
    resolver = AsyncMock()

    async def _resolve(enrich_result, context):
        invoices = [
            ResolvedInvoice(
                invoice_number=inv.invoice_number,
                total_amount=inv.total_amount,
                overall_confidence=0.95,
            )
            for inv in enrich_result.invoices
        ]
        return ResolveResult(invoices=invoices)

    resolver.resolve = AsyncMock(side_effect=_resolve)
    return resolver


# ---------------------------------------------------------------------------
# DB pool
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Minimal mock pool — tests never touch real DB."""
    pool = MagicMock()
    return pool


# ---------------------------------------------------------------------------
# CoreConfig
# ---------------------------------------------------------------------------


@pytest.fixture
def core_config() -> CoreConfig:
    """Return a default CoreConfig (all fields have sensible defaults)."""
    return CoreConfig(batch_size=100)
