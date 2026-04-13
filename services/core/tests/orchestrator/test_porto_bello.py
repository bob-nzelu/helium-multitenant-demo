"""
WS3 Orchestrator — Porto Bello Gate tests.

Verifies the v1 stub: all invoices pass through, single gate decision emitted.
"""
from __future__ import annotations

import pytest

from src.orchestrator.porto_bello import GateDecision, PortoBelloGate, PortoBelloResult
from src.processing.models import ResolveResult

from tests.orchestrator.conftest import make_resolved_invoice


# ---------------------------------------------------------------------------
# GateDecision structure
# ---------------------------------------------------------------------------


def test_gate_decision_fields():
    gd = GateDecision(gate_name="porto_bello", passed=True, reason="stub")
    assert gd.gate_name == "porto_bello"
    assert gd.passed is True
    assert gd.reason == "stub"


# ---------------------------------------------------------------------------
# PortoBelloResult structure
# ---------------------------------------------------------------------------


def test_porto_bello_result_defaults():
    result = PortoBelloResult()
    assert result.invoices == []
    assert result.gates == []


# ---------------------------------------------------------------------------
# Pass-through behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_porto_bello_passes_all_invoices():
    gate = PortoBelloGate()
    invoices = [make_resolved_invoice(i) for i in range(5)]
    resolve_result = ResolveResult(invoices=invoices)

    result = await gate.evaluate(resolve_result)

    assert len(result.invoices) == 5
    assert result.invoices == invoices


@pytest.mark.asyncio
async def test_porto_bello_emits_one_gate_decision():
    gate = PortoBelloGate()
    resolve_result = ResolveResult(invoices=[make_resolved_invoice(0)])

    result = await gate.evaluate(resolve_result)

    assert len(result.gates) == 1
    assert result.gates[0].gate_name == "porto_bello"
    assert result.gates[0].passed is True


@pytest.mark.asyncio
async def test_porto_bello_empty_invoices():
    gate = PortoBelloGate()
    resolve_result = ResolveResult(invoices=[])

    result = await gate.evaluate(resolve_result)

    assert result.invoices == []
    assert len(result.gates) == 1
    assert result.gates[0].passed is True
