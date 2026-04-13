"""
Phase 4: Enricher — HIS enrichment with circuit breaker + confidence scoring.

Calls HIS enrichment APIs in parallel for each invoice and line item.
Gracefully degrades if APIs are unavailable.

Per DEC-WS2-003: Confidence weights
  - Textract quality:     40%
  - Field completeness:   25%
  - Amount verification:  20%
  - Entity resolution:    15% (computed later in Phase 5)

Per DEC-WS2-009: Max 10 concurrent invoices.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from src.config import CoreConfig

from .his_client import HISClientProtocol, HISStubClient
from .models import (
    APIStats,
    EnrichedInvoice,
    EnrichedLineItem,
    EnrichMetadata,
    EnrichResult,
    PipelineContext,
    RedFlag,
    TransformResult,
)

logger = structlog.get_logger()


class EnrichError(Exception):
    """Phase 4 catastrophic failure."""

    def __init__(self, message: str, error_code: str = "WS2-E000"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class Enricher:
    """Phase 4: Enrich transformed data via HIS."""

    def __init__(
        self,
        his_client: HISClientProtocol | None = None,
        config: CoreConfig | None = None,
        audit_logger=None,
    ) -> None:
        self._config = config or CoreConfig()
        self._his = his_client or HISStubClient(
            failure_threshold=self._config.circuit_failure_threshold,
            recovery_timeout=self._config.circuit_recovery_timeout,
            success_threshold=self._config.circuit_success_threshold,
        )
        self._semaphore = asyncio.Semaphore(self._config.his_concurrent_invoices)
        self._audit_logger = audit_logger

    async def enrich(
        self,
        transform_result: TransformResult,
        context: PipelineContext,
    ) -> EnrichResult:
        """Execute Phase 4 enrichment."""
        start = time.monotonic()

        # WS6: Audit enrich.started
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="enrich.started",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={"invoice_count": len(transform_result.invoices)},
            )

        stats = APIStats()
        red_flags = list(transform_result.red_flags)
        enriched_invoices: list[EnrichedInvoice] = []

        tasks = [
            self._enrich_invoice(inv, idx, context, stats)
            for idx, inv in enumerate(transform_result.invoices)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "invoice_enrichment_failed",
                    invoice_index=idx,
                    error=str(result),
                )
                red_flags.append(RedFlag(
                    type="enrichment_failed",
                    severity="warning",
                    message=f"Enrichment failed for invoice {idx}: {result}",
                    phase="enrich",
                    invoice_index=idx,
                ))
                # Pass through as un-enriched
                inv = transform_result.invoices[idx]
                enriched_invoices.append(self._to_enriched_invoice(inv))
            else:
                enriched_invoices.append(result)

        elapsed = int((time.monotonic() - start) * 1000)
        total_calls = stats.hsn_calls + stats.category_calls + stats.address_calls
        total_success = stats.hsn_success + stats.category_success + stats.address_success

        logger.info(
            "enrich_complete",
            data_uuid=context.data_uuid,
            invoices=len(enriched_invoices),
            api_calls=total_calls,
            api_success=total_success,
            duration_ms=elapsed,
        )

        # WS6: Audit enrich.completed + enrich.his_called
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="enrich.his_called",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "total_calls": total_calls,
                    "total_success": total_success,
                },
            )
            await self._audit_logger.log(
                event_type="enrich.completed",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "enriched_count": len(enriched_invoices),
                    "duration_ms": elapsed,
                    "api_calls_total": total_calls,
                    "api_calls_success": total_success,
                },
            )

        return EnrichResult(
            invoices=enriched_invoices,
            customers=list(transform_result.customers),
            inventory=list(transform_result.inventory),
            red_flags=red_flags,
            metadata=EnrichMetadata(
                enrich_time_ms=elapsed,
                api_calls_total=total_calls,
                api_calls_success=total_success,
                api_calls_failed=total_calls - total_success,
                circuit_breaker_states=self._his.circuit_states,
            ),
            api_stats=stats,
        )

    async def _enrich_invoice(
        self,
        invoice: any,
        index: int,
        context: PipelineContext,
        stats: APIStats,
    ) -> EnrichedInvoice:
        """Enrich a single invoice (runs under semaphore)."""
        async with self._semaphore:
            enriched = self._to_enriched_invoice(invoice)

            # Enrich line items in parallel
            enriched_items: list[EnrichedLineItem] = []
            for li in invoice.line_items:
                enriched_li = await self._enrich_line_item(li, stats)
                enriched_items.append(enriched_li)
            enriched.line_items = enriched_items

            # Validate addresses
            if invoice.seller_address:
                addr_result = await self._his.validate_address(
                    invoice.seller_address,
                    invoice.seller_city,
                    invoice.seller_state,
                )
                stats.address_calls += 1
                if not addr_result.error:
                    stats.address_success += 1
                    enriched.seller_address_validated = addr_result.valid
                    enriched.seller_lga = addr_result.lga
                    enriched.seller_lga_code = addr_result.lga_code
                    enriched.seller_state_code = addr_result.state_code

            if invoice.buyer_address:
                addr_result = await self._his.validate_address(
                    invoice.buyer_address,
                    invoice.buyer_city,
                    invoice.buyer_state,
                )
                stats.address_calls += 1
                if not addr_result.error:
                    stats.address_success += 1
                    enriched.buyer_address_validated = addr_result.valid
                    enriched.buyer_lga = addr_result.lga
                    enriched.buyer_lga_code = addr_result.lga_code
                    enriched.buyer_state_code = addr_result.state_code

            # Compute confidence (partial — entity resolution adds 15% in Phase 5)
            enriched.confidence = self._compute_partial_confidence(enriched)

            return enriched

    async def _enrich_line_item(
        self,
        li: any,
        stats: APIStats,
    ) -> EnrichedLineItem:
        """Enrich a single line item with HS code and category."""
        enriched = EnrichedLineItem(
            line_number=li.line_number,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            line_total=li.line_total,
            unit_of_measure=li.unit_of_measure,
            tax_amount=li.tax_amount,
            tax_rate=li.tax_rate,
            hs_code=li.hs_code,
            customer_sku=li.customer_sku,
            helium_sku=li.helium_sku,
            item_type=li.item_type,
            hs_code_source="ORIGINAL" if li.hs_code else "NONE",
        )

        if li.description:
            # HS code classification
            if not li.hs_code:
                hsn_result = await self._his.classify_hsn(li.description)
                stats.hsn_calls += 1
                if not hsn_result.error and hsn_result.hs_code:
                    stats.hsn_success += 1
                    enriched.hs_code = hsn_result.hs_code
                    enriched.hs_code_confidence = hsn_result.confidence
                    enriched.hs_code_source = "HIS"

            # Category classification
            cat_result = await self._his.classify_category(
                li.description, enriched.hs_code
            )
            stats.category_calls += 1
            if not cat_result.error:
                stats.category_success += 1
                enriched.category = cat_result.category
                enriched.subcategory = cat_result.subcategory
                enriched.category_confidence = cat_result.confidence
                if cat_result.item_type:
                    enriched.item_type = cat_result.item_type

            # Service code (SERVICE items only)
            if enriched.item_type == "SERVICE":
                svc_result = await self._his.classify_service(li.description)
                if not svc_result.error:
                    enriched.service_code = svc_result.service_code
                    enriched.service_code_confidence = svc_result.confidence

        return enriched

    def _to_enriched_invoice(self, invoice: any) -> EnrichedInvoice:
        """Convert a TransformedInvoice to an EnrichedInvoice (no enrichment data)."""
        return EnrichedInvoice(
            invoice_number=invoice.invoice_number,
            helium_invoice_no=getattr(invoice, "helium_invoice_no", ""),
            direction=invoice.direction,
            document_type=invoice.document_type,
            transaction_type=invoice.transaction_type,
            firs_invoice_type_code=invoice.firs_invoice_type_code,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            currency_code=invoice.currency_code,
            total_amount=invoice.total_amount,
            tax_exclusive_amount=invoice.tax_exclusive_amount,
            total_tax_amount=invoice.total_tax_amount,
            seller_business_name=invoice.seller_business_name,
            seller_tin=invoice.seller_tin,
            seller_rc_number=invoice.seller_rc_number,
            seller_email=invoice.seller_email,
            seller_phone=invoice.seller_phone,
            seller_address=invoice.seller_address,
            seller_city=invoice.seller_city,
            seller_state=invoice.seller_state,
            seller_country=invoice.seller_country,
            buyer_business_name=invoice.buyer_business_name,
            buyer_tin=invoice.buyer_tin,
            buyer_rc_number=invoice.buyer_rc_number,
            buyer_email=invoice.buyer_email,
            buyer_phone=invoice.buyer_phone,
            buyer_address=invoice.buyer_address,
            buyer_city=invoice.buyer_city,
            buyer_state=invoice.buyer_state,
            buyer_country=invoice.buyer_country,
            line_items=[],
            source_file_uuid=getattr(invoice, "source_file_uuid", ""),
            stream_type=getattr(invoice, "stream_type", None),
            confidence=getattr(invoice, "confidence", 0.0),
            red_flags=list(getattr(invoice, "red_flags", [])),
        )

    @staticmethod
    def _compute_partial_confidence(invoice: EnrichedInvoice) -> float:
        """
        Compute partial confidence (Phases 2-4, without entity resolution).

        Weights: textract=40%, completeness=25%, amounts=20%.
        Entity resolution (15%) is added by Phase 5.
        """
        # Textract confidence (40% weight) — use existing confidence or estimate
        textract = min(invoice.confidence, 1.0) if invoice.confidence > 0 else 0.7

        # Field completeness (25% weight)
        required_fields = [
            invoice.invoice_number,
            invoice.issue_date,
            invoice.total_amount,
            invoice.buyer_business_name or invoice.buyer_tin,
            invoice.seller_business_name or invoice.seller_tin,
        ]
        completeness = sum(1 for f in required_fields if f) / len(required_fields)

        # Amount verification (20% weight)
        try:
            total = float(invoice.total_amount) if invoice.total_amount else 0
            tax = float(invoice.total_tax_amount) if invoice.total_tax_amount else 0
            subtotal = float(invoice.tax_exclusive_amount) if invoice.tax_exclusive_amount else 0
            if subtotal > 0 and total > 0:
                expected = subtotal + tax
                amount_score = 1.0 - min(abs(total - expected) / total, 1.0)
            else:
                amount_score = 0.5
        except (ValueError, ZeroDivisionError):
            amount_score = 0.5

        # Partial confidence (entity resolution 15% applied in Phase 5)
        partial = (textract * 0.40) + (completeness * 0.25) + (amount_score * 0.20)
        return round(min(partial, 0.85), 4)  # Cap at 0.85 — Phase 5 adds up to 0.15
