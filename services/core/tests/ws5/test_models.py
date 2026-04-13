"""Tests for WS5 Pydantic models."""

import pytest
from pydantic import ValidationError

from src.finalize.models import (
    AcceptRequest,
    AcceptResponse,
    EdgeUpdateRequest,
    EdgeUpdateResponse,
    FinalizeRequest,
    FinalizeResponse,
    FinalizeStatistics,
    RejectRequest,
    RejectResponse,
    RetransmitRequest,
    RetryRequest,
    RetryResponse,
)


# ── FinalizeRequest ──────────────────────────────────────────────────────


class TestFinalizeRequest:
    def test_valid_request(self):
        req = FinalizeRequest(
            queue_id="q-123",
            data_uuid="d-456",
            hlx_id="hlx-789",
            hlm_data={"hlm_version": "2.0", "rows": []},
        )
        assert req.queue_id == "q-123"
        assert req.is_refinalize is False

    def test_refinalize_flag(self):
        req = FinalizeRequest(
            queue_id="q-123",
            data_uuid="d-456",
            hlx_id="hlx-789",
            hlm_data={},
            is_refinalize=True,
        )
        assert req.is_refinalize is True

    def test_missing_queue_id(self):
        with pytest.raises(ValidationError):
            FinalizeRequest(
                queue_id="",
                data_uuid="d-456",
                hlx_id="hlx-789",
                hlm_data={},
            )

    def test_missing_data_uuid(self):
        with pytest.raises(ValidationError):
            FinalizeRequest(
                queue_id="q-123",
                data_uuid="",
                hlx_id="hlx-789",
                hlm_data={},
            )


# ── FinalizeResponse ─────────────────────────────────────────────────────


class TestFinalizeResponse:
    def test_defaults(self):
        resp = FinalizeResponse(
            queue_id="q-123",
            data_uuid="d-456",
            hlx_id="hlx-789",
            statistics=FinalizeStatistics(invoices_created=5),
        )
        assert resp.status == "finalized"
        assert resp.irn_list == []
        assert resp.warnings == []
        assert resp.statistics.invoices_created == 5
        assert resp.statistics.customers_created == 0


# ── RetryRequest ─────────────────────────────────────────────────────────


class TestRetryRequest:
    def test_valid(self):
        req = RetryRequest(invoice_id="inv-001")
        assert req.invoice_id == "inv-001"

    def test_empty_invoice_id(self):
        with pytest.raises(ValidationError):
            RetryRequest(invoice_id="")


class TestRetransmitRequest:
    def test_valid(self):
        req = RetransmitRequest(invoice_id="inv-001")
        assert req.invoice_id == "inv-001"


# ── B2B Accept/Reject ────────────────────────────────────────────────────


class TestAcceptRequest:
    def test_optional_reason(self):
        req = AcceptRequest()
        assert req.action_reason is None

    def test_with_reason(self):
        req = AcceptRequest(action_reason="Verified against PO")
        assert req.action_reason == "Verified against PO"


class TestRejectRequest:
    def test_valid_reason(self):
        req = RejectRequest(action_reason="Line items do not match contract pricing")
        assert len(req.action_reason) >= 10

    def test_reason_too_short(self):
        with pytest.raises(ValidationError):
            RejectRequest(action_reason="Bad")

    def test_reason_required(self):
        with pytest.raises(ValidationError):
            RejectRequest()


# ── EdgeUpdateRequest ────────────────────────────────────────────────────


class TestEdgeUpdateRequest:
    def test_transmission_result(self):
        req = EdgeUpdateRequest(
            invoice_id="inv-001",
            update_type="transmission_result",
            data={"transmission_status": "ACCEPTED", "firs_confirmation": "FIRS-123"},
        )
        assert req.update_type == "transmission_result"

    def test_signing_result(self):
        req = EdgeUpdateRequest(
            invoice_id="inv-001",
            update_type="signing_result",
            data={"csid": "CSID-001", "csid_status": "ISSUED", "sign_date": "2026-03-24"},
        )
        assert req.data["csid"] == "CSID-001"

    def test_empty_invoice_id(self):
        with pytest.raises(ValidationError):
            EdgeUpdateRequest(
                invoice_id="",
                update_type="transmission_result",
                data={},
            )
