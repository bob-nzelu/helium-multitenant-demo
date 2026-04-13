"""
WS3 Orchestrator — Model validation tests.

Covers ProcessPreviewRequest, ProcessPreviewResponse200/202,
RedFlagModel, StatisticsModel, ProgressModel, OrchestratorErrorResponse.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.orchestrator.models import (
    OrchestratorErrorResponse,
    ProcessPreviewRequest,
    ProcessPreviewResponse200,
    ProcessPreviewResponse202,
    ProgressModel,
    RedFlagModel,
    StatisticsModel,
)


# ---------------------------------------------------------------------------
# ProcessPreviewRequest
# ---------------------------------------------------------------------------


def test_process_preview_request_valid():
    req = ProcessPreviewRequest(queue_id="q-001", data_uuid="d-001")
    assert req.queue_id == "q-001"
    assert req.data_uuid == "d-001"


def test_process_preview_request_missing_queue_id():
    with pytest.raises(ValidationError) as exc_info:
        ProcessPreviewRequest(data_uuid="d-001")  # type: ignore[call-arg]
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("queue_id",) for e in errors)


def test_process_preview_request_missing_data_uuid():
    with pytest.raises(ValidationError) as exc_info:
        ProcessPreviewRequest(queue_id="q-001")  # type: ignore[call-arg]
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("data_uuid",) for e in errors)


def test_process_preview_request_missing_both_fields():
    with pytest.raises(ValidationError) as exc_info:
        ProcessPreviewRequest()  # type: ignore[call-arg]
    assert len(exc_info.value.errors()) == 2


# ---------------------------------------------------------------------------
# StatisticsModel
# ---------------------------------------------------------------------------


def test_statistics_model_defaults():
    stats = StatisticsModel()
    assert stats.total_invoices == 0
    assert stats.confidence == 0.0
    assert stats.worker_count == 0


def test_statistics_model_populated():
    stats = StatisticsModel(
        total_invoices=50,
        valid_count=45,
        failed_count=3,
        duplicate_count=2,
        processing_time_ms=1200,
        confidence=0.93,
        batch_count=1,
        worker_count=4,
    )
    assert stats.total_invoices == 50
    assert stats.valid_count == 45
    assert stats.confidence == 0.93


# ---------------------------------------------------------------------------
# RedFlagModel
# ---------------------------------------------------------------------------


def test_red_flag_model_required_fields():
    rf = RedFlagModel(type="duplicate_irn", severity="warning", message="Duplicate IRN detected")
    assert rf.type == "duplicate_irn"
    assert rf.severity == "warning"
    assert rf.invoice_index is None
    assert rf.suggestion is None


def test_red_flag_model_all_fields():
    rf = RedFlagModel(
        type="missing_tin",
        severity="error",
        message="Buyer TIN missing",
        invoice_index=3,
        invoice_number="INV-0003",
        field="buyer_tin",
        phase="transform",
        suggestion="Request TIN from buyer",
    )
    assert rf.invoice_index == 3
    assert rf.phase == "transform"


def test_red_flag_model_missing_required_fields():
    with pytest.raises(ValidationError):
        RedFlagModel(type="x")  # type: ignore[call-arg]  # missing severity + message


# ---------------------------------------------------------------------------
# ProcessPreviewResponse200
# ---------------------------------------------------------------------------


def test_response_200_serialization():
    stats = StatisticsModel(total_invoices=10, valid_count=9, processing_time_ms=800)
    resp = ProcessPreviewResponse200(
        queue_id="q-001",
        data_uuid="d-001",
        status="preview_ready",
        statistics=stats,
        red_flags=[],
        hlx_blob_uuid="blob-uuid-abc",
    )
    data = resp.model_dump()
    assert data["status"] == "preview_ready"
    assert data["hlx_blob_uuid"] == "blob-uuid-abc"
    assert data["statistics"]["total_invoices"] == 10


def test_response_200_immediate_finalize_no_blob():
    stats = StatisticsModel()
    resp = ProcessPreviewResponse200(
        queue_id="q-001",
        data_uuid="d-001",
        status="finalized",
        statistics=stats,
        hlx_blob_uuid=None,
    )
    assert resp.hlx_blob_uuid is None
    assert resp.status == "finalized"


# ---------------------------------------------------------------------------
# ProcessPreviewResponse202
# ---------------------------------------------------------------------------


def test_response_202_defaults():
    resp = ProcessPreviewResponse202(queue_id="q-001", data_uuid="d-001")
    assert resp.status == "processing"
    assert resp.phases_total == 7
    assert resp.progress.invoices_ready == 0


def test_response_202_serialization():
    resp = ProcessPreviewResponse202(
        queue_id="q-001",
        data_uuid="d-001",
        phases_completed=3,
        current_phase="enrich",
        estimated_completion_seconds=120,
    )
    data = resp.model_dump()
    assert data["phases_completed"] == 3
    assert data["current_phase"] == "enrich"
    assert data["estimated_completion_seconds"] == 120


# ---------------------------------------------------------------------------
# OrchestratorErrorResponse
# ---------------------------------------------------------------------------


def test_error_response_structure():
    err = OrchestratorErrorResponse(error_code="ORCH_002", message="Not found")
    assert err.error_code == "ORCH_002"
    assert err.details is None


def test_error_response_with_details():
    err = OrchestratorErrorResponse(
        error_code="ORCH_003",
        message="Already processing",
        details={"queue_id": "q-001", "current_status": "PROCESSING"},
    )
    assert err.details["current_status"] == "PROCESSING"
