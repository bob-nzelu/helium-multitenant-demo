"""
Tests for HIS Intelligence Feedback (D1-D4).

Verifies:
- CorrectionEntry model validation
- Confidence weights by source
- HISFeedbackClient retry/non-fatal behavior
- Finalize pipeline wires corrections through to HIS
"""

from __future__ import annotations

import asyncio
import sys

import pytest
import respx
from httpx import Response

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.finalize.models import (
    CorrectionEntry,
    FinalizeRequest,
    CONFIDENCE_WEIGHTS,
)
from src.processing.his_feedback_client import HISFeedbackClient


HIS_BASE = "http://his-test:8500"
FEEDBACK_URL = "/api/v1/his/intelligence/update"


# ── Model Tests ──────────────────────────────────────────────────────────


class TestCorrectionEntry:
    def test_basic_correction(self):
        c = CorrectionEntry(
            entity_type="inventory",
            entity_id="prod-001",
            field="hsn_code",
            old_value="4802.55",
            new_value="4802.56",
            source="hlx_review",
        )
        assert c.entity_type == "inventory"
        assert c.field == "hsn_code"
        assert c.source == "hlx_review"

    def test_default_source_is_hlx_review(self):
        c = CorrectionEntry(entity_type="customer", field="postal_code")
        assert c.source == "hlx_review"

    def test_optional_fields(self):
        c = CorrectionEntry(entity_type="invoice", field="document_type")
        assert c.entity_id is None
        assert c.old_value is None
        assert c.new_value is None


class TestConfidenceWeights:
    def test_hlx_review_is_highest(self):
        assert CONFIDENCE_WEIGHTS["hlx_review"] == 0.95

    def test_hlx_no_change_is_middle(self):
        assert CONFIDENCE_WEIGHTS["hlx_no_change"] == 0.70

    def test_api_finalize_is_lowest(self):
        assert CONFIDENCE_WEIGHTS["api_finalize"] == 0.40


class TestFinalizeRequestWithCorrections:
    def test_corrections_optional(self):
        req = FinalizeRequest(
            queue_id="q-1", data_uuid="d-1", hlx_id="h-1",
            hlm_data={"invoices": []},
        )
        assert req.corrections is None

    def test_corrections_array(self):
        req = FinalizeRequest(
            queue_id="q-1", data_uuid="d-1", hlx_id="h-1",
            hlm_data={"invoices": []},
            corrections=[
                {"entity_type": "inventory", "field": "hsn_code",
                 "old_value": "4802.55", "new_value": "4802.56"},
            ],
        )
        assert len(req.corrections) == 1
        assert req.corrections[0].field == "hsn_code"


# ── Client Tests ─────────────────────────────────────────────────────────


@pytest.fixture
def his_client():
    return HISFeedbackClient(base_url=HIS_BASE, api_key="test-key")


@pytest.mark.asyncio
class TestHISFeedbackClient:

    @respx.mock
    async def test_submit_corrections_success(self, his_client):
        respx.post(f"{HIS_BASE}{FEEDBACK_URL}").mock(
            return_value=Response(200, json={"accepted": 3})
        )

        result = await his_client.submit_corrections(
            corrections=[
                {"entity_type": "inventory", "field": "hsn_code",
                 "old_value": "4802.55", "new_value": "4802.56"},
            ],
            company_id="comp-001",
            source="hlx_review",
            confidence_weight=0.95,
        )

        assert result == {"accepted": 3}
        import json
        body = json.loads(respx.calls.last.request.content)
        assert body["company_id"] == "comp-001"
        assert body["source"] == "hlx_review"
        assert body["confidence_weight"] == 0.95
        assert len(body["corrections"]) == 1

    @respx.mock
    async def test_empty_corrections_returns_none(self, his_client):
        result = await his_client.submit_corrections(
            corrections=[],
            company_id="comp-001",
            source="hlx_review",
            confidence_weight=0.95,
        )
        assert result is None
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_retries_on_5xx(self, his_client):
        route = respx.post(f"{HIS_BASE}{FEEDBACK_URL}")
        route.side_effect = [
            Response(503),
            Response(200, json={"ok": True}),
        ]

        result = await his_client.submit_corrections(
            corrections=[{"entity_type": "customer", "field": "lga", "new_value": "Ikeja"}],
            company_id="comp-001",
            source="hlx_review",
            confidence_weight=0.95,
        )

        assert result == {"ok": True}
        assert route.call_count == 2

    @respx.mock
    async def test_returns_none_on_failure(self, his_client):
        respx.post(f"{HIS_BASE}{FEEDBACK_URL}").mock(
            return_value=Response(503)
        )

        result = await his_client.submit_corrections(
            corrections=[{"entity_type": "inventory", "field": "hsn_code"}],
            company_id="comp-001",
            source="api_finalize",
            confidence_weight=0.40,
        )

        assert result is None

    @respx.mock
    async def test_includes_context_when_provided(self, his_client):
        respx.post(f"{HIS_BASE}{FEEDBACK_URL}").mock(
            return_value=Response(200, json={})
        )

        await his_client.submit_corrections(
            corrections=[{"entity_type": "inventory", "field": "hsn_code"}],
            company_id="comp-001",
            source="hlx_review",
            confidence_weight=0.95,
            context={"batch_id": "batch-123", "invoice_id": "inv-001"},
        )

        import json
        body = json.loads(respx.calls.last.request.content)
        assert body["context"]["batch_id"] == "batch-123"

    @respx.mock
    async def test_never_raises_on_timeout(self, his_client):
        import httpx
        respx.post(f"{HIS_BASE}{FEEDBACK_URL}").mock(
            side_effect=httpx.TimeoutException("timeout")
        )

        result = await his_client.submit_corrections(
            corrections=[{"entity_type": "customer", "field": "postal_code"}],
            company_id="comp-001",
            source="hlx_review",
            confidence_weight=0.95,
        )

        assert result is None
