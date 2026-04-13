"""WS3: Porto Bello — Business logic gate (v1 stub, pass-through)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.processing.models import ResolveResult, ResolvedInvoice

logger = logging.getLogger(__name__)


@dataclass
class GateDecision:
    """Per-invoice gate decision."""

    gate_name: str
    passed: bool
    reason: str


@dataclass
class PortoBelloResult:
    """Output of the Porto Bello gate."""

    invoices: list[ResolvedInvoice] = field(default_factory=list)
    gates: list[GateDecision] = field(default_factory=list)


class PortoBelloGate:
    """Business logic gate — evaluates whether invoices need counterparty verification.

    v1: Pure pass-through. All invoices proceed without verification.
    v2 (future): Check buyer details completeness, route incomplete invoices
    to PENDING_COUNTERPARTY status for portal-based verification.
    """

    async def evaluate(self, resolve_result: ResolveResult) -> PortoBelloResult:
        """Evaluate resolved invoices against business rules.

        v1: Pass-through. All invoices pass.

        Args:
            resolve_result: Output from Phase 5 (RESOLVE).

        Returns:
            PortoBelloResult with all invoices passed through.
        """
        logger.debug(
            "Porto Bello gate (v1 stub): passing %d invoices through",
            len(resolve_result.invoices),
        )
        return PortoBelloResult(
            invoices=resolve_result.invoices,
            gates=[
                GateDecision(
                    gate_name="porto_bello",
                    passed=True,
                    reason="Porto Bello disabled (v1 stub)",
                )
            ],
        )
