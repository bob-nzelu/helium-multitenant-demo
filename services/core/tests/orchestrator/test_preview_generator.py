"""
WS3 Orchestrator — Preview Generator tests.

Covers invoice branching (all 7 categories), .hlx packing/encryption/upload,
report.json structure, metadata.json structure, and empty input handling.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestrator.preview_generator import (
    SHEET_DUPLICATE,
    SHEET_FAILED,
    SHEET_FOC,
    SHEET_LATE,
    SHEET_POSSIBLE_B2B,
    SHEET_SUBMISSION,
    SHEET_UNUSUAL,
    PreviewGenerator,
)
from src.processing.models import (
    PipelineContext,
    RedFlag,
    ResolveResult,
    ResolvedInvoice,
)
from tests.orchestrator.conftest import make_resolved_invoice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(**overrides) -> PipelineContext:
    base = dict(
        data_uuid="data-uuid-preview",
        company_id="COMP-001",
        trace_id="trace-preview",
        helium_user_id="user-001",
    )
    base.update(overrides)
    return PipelineContext(**base)


def _make_generator(blob_client=None):
    if blob_client is None:
        blob_client = AsyncMock()
        blob_client.upload_blob = AsyncMock()
    return PreviewGenerator(blob_client)


def _fake_pack(manifest, sheets, report, metadata):
    return b"fake-hlx-bytes"


def _fake_encrypt(data, company_id):
    return b"encrypted-" + data


# ---------------------------------------------------------------------------
# Branch logic: submission (happy path)
# ---------------------------------------------------------------------------


def test_branch_submission_happy_path():
    gen = _make_generator()
    invoices = [make_resolved_invoice(0, issue_date="2026-03-22", total_amount="5000")]
    branch = gen._branch_invoices(invoices, [])
    assert len(branch.categories.get(SHEET_SUBMISSION, [])) == 1


# ---------------------------------------------------------------------------
# Branch logic: duplicate detection
# ---------------------------------------------------------------------------


def test_branch_duplicate_detection():
    gen = _make_generator()
    inv = make_resolved_invoice(0)
    flags = [
        RedFlag(
            type="duplicate_irn",
            severity="warning",
            message="Duplicate IRN",
            phase="parse",
            invoice_index=0,
        )
    ]
    branch = gen._branch_invoices([inv], flags)
    assert len(branch.categories.get(SHEET_DUPLICATE, [])) == 1


def test_branch_duplicate_hash_detection():
    gen = _make_generator()
    inv = make_resolved_invoice(0)
    flags = [
        RedFlag(
            type="duplicate_hash",
            severity="warning",
            message="File hash duplicate",
            phase="parse",
            invoice_index=0,
        )
    ]
    branch = gen._branch_invoices([inv], flags)
    assert len(branch.categories.get(SHEET_DUPLICATE, [])) == 1


# ---------------------------------------------------------------------------
# Branch logic: late invoice (>48h)
# ---------------------------------------------------------------------------


def test_branch_late_invoice():
    gen = _make_generator()
    old_date = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    inv = make_resolved_invoice(0, issue_date=old_date)
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_LATE, [])) == 1


def test_branch_recent_invoice_not_late():
    gen = _make_generator()
    inv = make_resolved_invoice(0, issue_date="2026-03-22", total_amount="5000")
    branch = gen._branch_invoices([inv], [])
    assert branch.categories.get(SHEET_LATE, []) == []


# ---------------------------------------------------------------------------
# Branch logic: FOC (total <= 0.01)
# ---------------------------------------------------------------------------


def test_branch_foc_zero_total():
    gen = _make_generator()
    inv = make_resolved_invoice(0, issue_date="2026-03-22", total_amount="0.00")
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_FOC, [])) == 1


def test_branch_foc_threshold_boundary():
    gen = _make_generator()
    inv = make_resolved_invoice(0, issue_date="2026-03-22", total_amount="0.01")
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_FOC, [])) == 1


def test_branch_above_foc_threshold_is_submission():
    gen = _make_generator()
    inv = make_resolved_invoice(0, issue_date="2026-03-22", total_amount="100.00")
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_SUBMISSION, [])) == 1


# ---------------------------------------------------------------------------
# Branch logic: unusual amount
# ---------------------------------------------------------------------------


def test_branch_unusual_amount():
    gen = _make_generator()
    inv = make_resolved_invoice(0, issue_date="2026-03-22", total_amount="9999999")
    flags = [
        RedFlag(
            type="unusual_amount",
            severity="warning",
            message="Amount unusually high",
            phase="enrich",
            invoice_index=0,
        )
    ]
    branch = gen._branch_invoices([inv], flags)
    assert len(branch.categories.get(SHEET_UNUSUAL, [])) == 1


# ---------------------------------------------------------------------------
# Branch logic: possible B2B
# ---------------------------------------------------------------------------


def test_branch_possible_b2b():
    gen = _make_generator()
    inv = make_resolved_invoice(
        0,
        issue_date="2026-03-22",
        total_amount="5000",
        transaction_type="B2C",
        buyer_tin="98765432-0001",
    )
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_POSSIBLE_B2B, [])) == 1


def test_branch_b2b_without_tin_is_submission():
    gen = _make_generator()
    inv = make_resolved_invoice(
        0,
        issue_date="2026-03-22",
        total_amount="5000",
        transaction_type="B2C",
        buyer_tin=None,
    )
    branch = gen._branch_invoices([inv], [])
    assert len(branch.categories.get(SHEET_SUBMISSION, [])) == 1


# ---------------------------------------------------------------------------
# Branch logic: failed (error red flags)
# ---------------------------------------------------------------------------


def test_branch_failed_error_flags():
    gen = _make_generator()
    inv = make_resolved_invoice(0)
    flags = [
        RedFlag(
            type="missing_tin",
            severity="error",
            message="Seller TIN missing",
            phase="transform",
            invoice_index=0,
        )
    ]
    branch = gen._branch_invoices([inv], flags)
    failed = branch.categories.get(SHEET_FAILED, [])
    assert len(failed) == 1
    assert "__ERROR__" in failed[0]


# ---------------------------------------------------------------------------
# .hlx packing called correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_calls_pack_hlx():
    blob_client = AsyncMock()
    blob_client.upload_blob = AsyncMock()
    gen = PreviewGenerator(blob_client)
    invoices = [make_resolved_invoice(0, issue_date="2026-03-22", total_amount="5000")]
    resolve_result = ResolveResult(invoices=invoices)
    ctx = _make_context()

    with patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack) as mock_pack, \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt):
        await gen.generate(ctx, resolve_result, [], {}, 500)

    mock_pack.assert_called_once()


# ---------------------------------------------------------------------------
# Encryption called correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_calls_encrypt_with_company_id():
    blob_client = AsyncMock()
    blob_client.upload_blob = AsyncMock()
    gen = PreviewGenerator(blob_client)
    invoices = [make_resolved_invoice(0, issue_date="2026-03-22", total_amount="5000")]
    resolve_result = ResolveResult(invoices=invoices)
    ctx = _make_context(company_id="COMP-XYZ")

    with patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt) as mock_enc:
        await gen.generate(ctx, resolve_result, [], {}, 500)

    mock_enc.assert_called_once()
    call_args = mock_enc.call_args[0]
    assert call_args[1] == "COMP-XYZ"


# ---------------------------------------------------------------------------
# Blob upload called correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_calls_blob_upload():
    blob_client = AsyncMock()
    blob_client.upload_blob = AsyncMock()
    gen = PreviewGenerator(blob_client)
    invoices = [make_resolved_invoice(0, issue_date="2026-03-22", total_amount="5000")]
    resolve_result = ResolveResult(invoices=invoices)
    ctx = _make_context()

    with patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt):
        blob_uuid = await gen.generate(ctx, resolve_result, [], {}, 500)

    blob_client.upload_blob.assert_called_once()
    call_kwargs = blob_client.upload_blob.call_args.kwargs
    assert call_kwargs["content_type"] == "application/x-helium-exchange"
    assert call_kwargs["company_id"] == ctx.company_id
    assert blob_uuid  # UUID returned


# ---------------------------------------------------------------------------
# Empty result (no invoices)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_empty_invoices():
    blob_client = AsyncMock()
    blob_client.upload_blob = AsyncMock()
    gen = PreviewGenerator(blob_client)
    resolve_result = ResolveResult(invoices=[])
    ctx = _make_context()

    with patch("src.orchestrator.preview_generator.pack_hlx", side_effect=_fake_pack), \
         patch("src.orchestrator.preview_generator.encrypt_hlx", side_effect=_fake_encrypt):
        blob_uuid = await gen.generate(ctx, resolve_result, [], {}, 100)

    assert isinstance(blob_uuid, str)
    assert len(blob_uuid) > 0


# ---------------------------------------------------------------------------
# Report.json structure
# ---------------------------------------------------------------------------


def test_build_report_structure():
    gen = _make_generator()
    from helium_formats.hlx.models import HLXStatistics
    stats = HLXStatistics(total_invoices=5, valid_count=4, failed_count=1, processing_time_ms=500, overall_confidence=0.92)
    flags = [
        RedFlag(type="duplicate_irn", severity="warning", message="dup", phase="parse"),
        RedFlag(type="missing_tin", severity="error", message="no tin", phase="transform"),
    ]
    report = gen._build_report(stats, flags, {"fetch": 50, "parse": 100}, [], "2026-03-22T00:00:00Z")

    assert "summary" in report
    assert "red_flags" in report
    assert "red_flag_summary" in report
    assert report["red_flag_summary"]["error_count"] == 1
    assert report["red_flag_summary"]["warning_count"] == 1
    assert "phase_timings" in report
    assert report["phase_timings"]["fetch"] == 50


# ---------------------------------------------------------------------------
# Metadata.json structure
# ---------------------------------------------------------------------------


def test_build_metadata_structure():
    gen = _make_generator()
    ctx = _make_context()
    metadata = gen._build_metadata(ctx, "2026-03-22T00:00:00Z")

    assert metadata["data_uuid"] == ctx.data_uuid
    assert metadata["company_id"] == ctx.company_id
    assert metadata["uploaded_by"] == ctx.helium_user_id
    assert metadata["x_trace_id"] == ctx.trace_id
    assert "hlx_id" in metadata
    assert metadata["version_number"] == 1
    assert "pipeline" in metadata
    assert "versions" in metadata
