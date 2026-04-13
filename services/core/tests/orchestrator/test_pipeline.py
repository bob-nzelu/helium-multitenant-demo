"""
WS3 Orchestrator — Pipeline Orchestrator tests.

Covers: happy path (200), .hlm detection skip, enrichment failure (degraded),
resolution failure (degraded), immediate finalize path, SSE events,
red flag accumulation, timer checkpoints, 200 response structure.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import CoreConfig
from src.ingestion.models import BlobResponse, ParseMetadata, ParseResult
from src.orchestrator.models import ProcessPreviewResponse200, ProcessPreviewResponse202
from src.orchestrator.pipeline import PipelineOrchestrator, PipelineState, SoftTimeoutReached
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
from src.sse.events import EVENT_PROCESSING_LOG, EVENT_PROCESSING_PROGRESS

from tests.orchestrator.conftest import (
    _CapturingSSEManager,
    make_resolved_invoice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> CoreConfig:
    # CoreConfig only has documented fields; pipeline reads extras via getattr()
    kwargs = {"batch_size": 100}
    # Only pass valid CoreConfig fields
    valid_fields = {f.name for f in CoreConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    for k, v in overrides.items():
        if k in valid_fields:
            kwargs[k] = v
    return CoreConfig(**kwargs)


def _make_parse_result(n: int = 3, is_hlm: bool = False) -> ParseResult:
    rows = [{"invoice_number": f"INV-{i:04d}"} for i in range(n)]
    return ParseResult(
        file_type="excel",
        raw_data=rows,
        metadata=ParseMetadata(
            parser_type="excel",
            original_filename="test.xlsx",
            row_count=n,
        ),
        is_hlm=is_hlm,
        file_hash="",
    )


def _make_transform_result(n: int = 3) -> TransformResult:
    return TransformResult(
        invoices=[TransformedInvoice(invoice_number=f"INV-{i:04d}") for i in range(n)]
    )


def _make_enrich_result(n: int = 3) -> EnrichResult:
    return EnrichResult(
        invoices=[EnrichedInvoice(invoice_number=f"INV-{i:04d}") for i in range(n)]
    )


def _make_resolve_result(n: int = 3) -> ResolveResult:
    return ResolveResult(
        invoices=[
            make_resolved_invoice(i, issue_date="2026-03-22", total_amount="5000")
            for i in range(n)
        ]
    )


def _make_orchestrator(
    sse_manager=None,
    heartbeat_client=None,
    parser=None,
    transformer=None,
    enricher=None,
    resolver=None,
    db_pool=None,
    config=None,
):
    if sse_manager is None:
        sse_manager = _CapturingSSEManager()
    if heartbeat_client is None:
        heartbeat_client = AsyncMock()
        heartbeat_client.fetch_blob = AsyncMock(return_value=BlobResponse(
            content=b"data", content_type="application/xlsx",
            filename="test.xlsx", size=4,
        ))
        heartbeat_client.upload_blob = AsyncMock()
    if parser is None:
        parser = AsyncMock()
        parser.parse = AsyncMock(return_value=_make_parse_result(3))
    if transformer is None:
        transformer = AsyncMock()
        transformer.transform = AsyncMock(return_value=_make_transform_result(3))
    if enricher is None:
        enricher = AsyncMock()
        enricher.enrich = AsyncMock(return_value=_make_enrich_result(3))
    if resolver is None:
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=_make_resolve_result(3))
    if db_pool is None:
        db_pool = MagicMock()
    if config is None:
        config = _make_config()

    registry = MagicMock()
    registry.get = MagicMock(return_value=parser)

    return PipelineOrchestrator(
        config=config,
        heartbeat_client=heartbeat_client,
        parser_registry=registry,
        transformer=transformer,
        enricher=enricher,
        resolver=resolver,
        sse_manager=sse_manager,
        db_pool=db_pool,
    )


def _fake_pack(manifest, sheets, report, metadata):
    return b"packed"


def _fake_encrypt(data, company_id):
    return b"encrypted"


QUEUE_ENTRY = {
    "queue_id": "q-001",
    "data_uuid": "d-001",
    "blob_uuid": "blob-001",
    "company_id": "COMP-001",
    "trace_id": "trace-001",
    "uploaded_by": "user-001",
    "status": "PENDING",
    "immediate_processing": False,
}


# ---------------------------------------------------------------------------
# Timeout helper
# ---------------------------------------------------------------------------


def test_check_timeout_not_raised_when_within_limit():
    orch = _make_orchestrator()
    state = PipelineState(start_time=time.monotonic())
    orch.SOFT_TIMEOUT_SECONDS = 300
    # Should not raise — just started
    orch._check_timeout(state, "fetch")


def test_check_timeout_raises_when_exceeded():
    orch = _make_orchestrator()
    state = PipelineState(start_time=time.monotonic() - 400)
    orch.SOFT_TIMEOUT_SECONDS = 280
    with pytest.raises(SoftTimeoutReached) as exc_info:
        orch._check_timeout(state, "parse")
    assert exc_info.value.phase == "parse"


def test_estimate_remaining_no_phases_completed():
    orch = _make_orchestrator()
    remaining = orch._estimate_remaining(100.0, 0, 7)
    assert remaining == 300


def test_estimate_remaining_with_phases():
    orch = _make_orchestrator()
    # 3 phases done in 30s → avg 10s each. 4 remaining → ~40s
    remaining = orch._estimate_remaining(30.0, 3, 7)
    assert remaining == 40


# ---------------------------------------------------------------------------
# Happy path (all phases succeed → 200)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_happy_path_returns_200():
    orch = _make_orchestrator()

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    assert isinstance(result, ProcessPreviewResponse200)
    assert result.queue_id == "q-001"
    assert result.data_uuid == "d-001"
    assert result.status == "preview_ready"


@pytest.mark.asyncio
async def test_pipeline_happy_path_200_has_statistics():
    orch = _make_orchestrator()

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    assert result.statistics is not None
    assert result.statistics.worker_count == orch._worker_manager.worker_count


# ---------------------------------------------------------------------------
# .hlm detection skip (Phase 3 skipped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hlm_detection_skips_transform():
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=_make_parse_result(3, is_hlm=True))

    transformer = AsyncMock()
    transformer.transform = AsyncMock(return_value=_make_transform_result(3))

    orch = _make_orchestrator(parser=parser, transformer=transformer)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="hlm"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    # transformer.transform should NOT have been called
    transformer.transform.assert_not_called()
    assert isinstance(result, ProcessPreviewResponse200)


# ---------------------------------------------------------------------------
# Enrichment failure — degraded mode, continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrichment_failure_continues_degraded():
    enricher = AsyncMock()
    enricher.enrich = AsyncMock(side_effect=RuntimeError("HIS down"))
    orch = _make_orchestrator(enricher=enricher)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    # Still returns 200 — degraded mode
    assert isinstance(result, ProcessPreviewResponse200)
    # enrichment_failed red flag should appear
    rf_types = {rf.type for rf in result.red_flags}
    assert "enrichment_failed" in rf_types


# ---------------------------------------------------------------------------
# Resolution failure — degraded mode, continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_failure_continues_degraded():
    resolver = AsyncMock()
    resolver.resolve = AsyncMock(side_effect=RuntimeError("DB unavailable"))
    orch = _make_orchestrator(resolver=resolver)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    assert isinstance(result, ProcessPreviewResponse200)
    rf_types = {rf.type for rf in result.red_flags}
    assert "resolution_failed" in rf_types


# ---------------------------------------------------------------------------
# Immediate finalize path (no preview generated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immediate_finalize_path():
    orch = _make_orchestrator()
    entry = {**QUEUE_ENTRY, "immediate_processing": True}

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", entry)

    assert isinstance(result, ProcessPreviewResponse200)
    assert result.status == "finalized"
    assert result.hlx_blob_uuid is None


# ---------------------------------------------------------------------------
# SSE events emitted at correct phases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_log_events_emitted():
    sse = _CapturingSSEManager()
    orch = _make_orchestrator(sse_manager=sse)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        await orch.process("q-001", "d-001", QUEUE_ENTRY)

    log_events = sse.events_of_type(EVENT_PROCESSING_LOG)
    assert len(log_events) >= 5  # At least one per phase


@pytest.mark.asyncio
async def test_sse_progress_events_emitted():
    sse = _CapturingSSEManager()
    orch = _make_orchestrator(sse_manager=sse)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        await orch.process("q-001", "d-001", QUEUE_ENTRY)

    progress_events = sse.events_of_type(EVENT_PROCESSING_PROGRESS)
    assert len(progress_events) >= 1


# ---------------------------------------------------------------------------
# Red flags accumulated across phases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_red_flags_from_enrich_included_in_response():
    enricher = AsyncMock()

    async def _enrich(transform_result, context):
        return EnrichResult(
            invoices=[
                EnrichedInvoice(invoice_number=inv.invoice_number)
                for inv in transform_result.invoices
            ],
            red_flags=[
                RedFlag(
                    type="address_invalid",
                    severity="warning",
                    message="Address validation failed",
                    phase="enrich",
                )
            ],
        )

    enricher.enrich = AsyncMock(side_effect=_enrich)
    orch = _make_orchestrator(enricher=enricher)

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    rf_types = {rf.type for rf in result.red_flags}
    assert "address_invalid" in rf_types


# ---------------------------------------------------------------------------
# Phase timing recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_timings_non_negative():
    orch = _make_orchestrator()

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch("src.orchestrator.pipeline.DedupChecker") as mock_dedup, \
         patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None):
        mock_dedup.check = AsyncMock(return_value=MagicMock(is_duplicate=False))

        state = PipelineState(start_time=time.monotonic())
        # Execute pipeline via process() and inspect via SSE (indirect)
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    # Processing time must be non-negative
    assert result.statistics.processing_time_ms >= 0


# ---------------------------------------------------------------------------
# SoftTimeoutReached — returns 202
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_returns_202_on_soft_timeout():
    orch = _make_orchestrator()
    orch.SOFT_TIMEOUT_SECONDS = 0  # Immediately timeout

    with patch("src.orchestrator.pipeline.detect_file_type", return_value="excel"), \
         patch.object(orch, "_get_blob_uuid", return_value="blob-001"), \
         patch.object(orch, "_update_queue_status", return_value=None), \
         patch.object(orch, "_continue_background", new=AsyncMock()):
        result = await orch.process("q-001", "d-001", QUEUE_ENTRY)

    assert isinstance(result, ProcessPreviewResponse202)
    assert result.queue_id == "q-001"
    assert result.status == "processing"


# ---------------------------------------------------------------------------
# _build_200_response
# ---------------------------------------------------------------------------


def test_build_200_response_valid_count_non_negative():
    orch = _make_orchestrator()
    state = PipelineState()
    state.invoices_total = 5
    state.duplicate_count = 10  # More than total → valid_count clamps to 0
    state.skipped_count = 10
    state.red_flags = []
    result = orch._build_200_response("q-001", "d-001", "preview_ready", state, 500, "blob-abc")
    assert result.statistics.valid_count >= 0


def test_build_200_response_structure():
    orch = _make_orchestrator()
    state = PipelineState()
    state.invoices_total = 10
    state.duplicate_count = 1
    state.skipped_count = 0
    state.red_flags = []
    state.batch_count = 1
    result = orch._build_200_response("q-001", "d-001", "preview_ready", state, 1000, "blob-xyz")
    assert result.hlx_blob_uuid == "blob-xyz"
    assert result.statistics.total_invoices == 10
    assert result.statistics.duplicate_count == 1
