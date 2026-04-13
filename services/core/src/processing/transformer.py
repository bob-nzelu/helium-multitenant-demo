"""
Phase 3: Transformer — wire Transforma library into Core pipeline.

Core's Transformer:
1. Loads transformation script from DB (or uses default)
2. Builds Transforma callbacks (IRNChecker, HISLookup, EventEmitter)
3. Converts WS1 ParseResult → Transforma RawFileData
4. Calls execute_transformation()
5. Converts TransformationResult → WS2 TransformResult

Per decision: Transforma is imported as a library, NOT copied into Core.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

from src.config import CoreConfig
from src.ingestion.models import ParseResult

from .models import (
    ExtractedCustomer,
    ExtractedProduct,
    PipelineContext,
    RedFlag,
    TransformedInvoice,
    TransformedLineItem,
    TransformMetadata,
    TransformResult,
)

logger = structlog.get_logger()


class TransformError(Exception):
    """Phase 3 catastrophic failure."""

    def __init__(self, message: str, error_code: str = "WS2-T000", details: dict | None = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(message)


class Transformer:
    """
    Phase 3: Transform raw parsed data into FIRS-compliant records.

    If Transforma is installed, delegates to execute_transformation().
    Otherwise, applies a built-in default transformation that maps
    common field names to canonical schema.
    """

    def __init__(self, pool: AsyncConnectionPool, config: CoreConfig, audit_logger=None) -> None:
        self._pool = pool
        self._config = config
        self._audit_logger = audit_logger
        self._transforma_available = self._check_transforma()

    @staticmethod
    def _check_transforma() -> bool:
        """Check if Transforma library is importable."""
        try:
            import transforma  # noqa: F401
            return True
        except ImportError:
            return False

    async def transform(
        self,
        parse_result: ParseResult,
        context: PipelineContext,
    ) -> TransformResult:
        """Execute Phase 3 transformation."""
        start = time.monotonic()

        # WS6: Audit transform.started
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="transform.started",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "is_hlm_passthrough": parse_result.is_hlm,
                    "transforma_available": self._transforma_available,
                },
            )
        red_flags: list[RedFlag] = list(
            RedFlag(
                type=rf.field_name,
                severity=rf.severity,
                message=rf.message,
                phase="parse",
            )
            for rf in parse_result.red_flags
        )

        # HLM files are pre-transformed — pass through
        if parse_result.is_hlm:
            result = self._passthrough_hlm(parse_result, context)
            elapsed = int((time.monotonic() - start) * 1000)
            result.metadata.transform_time_ms = elapsed
            result.red_flags = red_flags + result.red_flags
            return result

        if self._transforma_available:
            result = await self._transform_via_transforma(parse_result, context)
        else:
            result = self._transform_default(parse_result, context)

        elapsed = int((time.monotonic() - start) * 1000)
        result.metadata.transform_time_ms = elapsed
        result.red_flags = red_flags + result.red_flags

        logger.info(
            "transform_complete",
            data_uuid=context.data_uuid,
            invoices=len(result.invoices),
            red_flags=len(result.red_flags),
            transforma=self._transforma_available,
            duration_ms=elapsed,
        )

        # WS6: Audit transform.completed
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="transform.completed",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "invoice_count": len(result.invoices),
                    "customer_count": len(result.customers),
                    "product_count": len(result.inventory),
                    "duration_ms": elapsed,
                },
            )

        return result

    async def _transform_via_transforma(
        self,
        parse_result: ParseResult,
        context: PipelineContext,
    ) -> TransformResult:
        """Delegate to Transforma library."""
        from transforma import (
            RawFileData,
            TransformationScript,
            execute_transformation,
        )

        script = await self._load_script(context.company_id)

        # WS6: Audit transform.script_loaded
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="transform.script_loaded",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                metadata={
                    "script_id": getattr(script, "script_id", "default"),
                    "is_default": getattr(script, "script_id", "") == "default",
                },
            )

        raw_files = [
            RawFileData(
                file_type=parse_result.file_type,
                content=parse_result.raw_data,
                filename=parse_result.metadata.original_filename,
                file_hash=parse_result.file_hash,
                metadata={"data_uuid": context.data_uuid},
            )
        ]

        # Build callbacks
        irn_checker = await self._build_irn_checker()
        event_emitter = self._build_event_emitter(context)

        try:
            result = await execute_transformation(
                script=script,
                raw_files=raw_files,
                irn_checker=irn_checker,
                event_emitter=event_emitter,
            )
        except Exception as e:
            logger.error("transforma_failed", error=str(e), data_uuid=context.data_uuid)
            # WS6: Audit transform.failed
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="transform.failed",
                    entity_type="queue",
                    entity_id=context.data_uuid,
                    action="PROCESS",
                    company_id=context.company_id,
                    metadata={"error_code": "WS2-T003", "error_message": str(e)},
                )
            raise TransformError(
                f"Transforma execution failed: {e}",
                error_code="WS2-T003",
                details={"original_error": str(e)},
            ) from e

        return self._convert_transforma_result(result, script)

    def _transform_default(
        self,
        parse_result: ParseResult,
        context: PipelineContext,
    ) -> TransformResult:
        """Built-in default transformation for common field patterns."""
        invoices: list[TransformedInvoice] = []
        customers: list[ExtractedCustomer] = []
        products: list[ExtractedProduct] = []
        flags: list[RedFlag] = []

        raw = parse_result.raw_data
        rows = raw if isinstance(raw, list) else [raw]

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue

            invoice = self._map_row_to_invoice(row, idx, context)
            invoices.append(invoice)

            # Extract customer from buyer fields
            buyer = ExtractedCustomer(
                business_name=invoice.buyer_business_name,
                tin=invoice.buyer_tin,
                rc_number=invoice.buyer_rc_number,
                email=invoice.buyer_email,
                phone=invoice.buyer_phone,
                address=invoice.buyer_address,
                city=invoice.buyer_city,
                state=invoice.buyer_state,
                role="BUYER",
            )
            if buyer.business_name or buyer.tin:
                customers.append(buyer)

            # Extract products from line items
            for li_idx, li in enumerate(invoice.line_items):
                products.append(
                    ExtractedProduct(
                        description=li.description,
                        customer_sku=li.customer_sku,
                        hs_code=li.hs_code,
                        unit_of_measure=li.unit_of_measure,
                        item_type=li.item_type,
                        source_invoice_index=idx,
                        source_line_item_index=li_idx,
                    )
                )

            # Validate required fields
            if not invoice.invoice_number:
                flags.append(RedFlag(
                    type="missing_invoice_number",
                    severity="error",
                    message="Invoice number is missing",
                    phase="transform",
                    invoice_index=idx,
                    field="invoice_number",
                ))
            if not invoice.total_amount or invoice.total_amount == "0":
                flags.append(RedFlag(
                    type="zero_amount",
                    severity="warning",
                    message="Total amount is zero or missing",
                    phase="transform",
                    invoice_index=idx,
                    field="total_amount",
                ))

        total_li = sum(len(inv.line_items) for inv in invoices)
        return TransformResult(
            invoices=invoices,
            customers=customers,
            inventory=products,
            red_flags=flags,
            metadata=TransformMetadata(
                is_default_script=True,
                invoice_count=len(invoices),
                line_item_count=total_li,
            ),
        )

    def _map_row_to_invoice(
        self,
        row: dict[str, Any],
        index: int,
        context: PipelineContext,
    ) -> TransformedInvoice:
        """Map a raw data row to a TransformedInvoice using field name heuristics."""

        def get(keys: list[str], default: Any = None) -> Any:
            """Try multiple field names (case-insensitive)."""
            lower_row = {k.lower().replace(" ", "_"): v for k, v in row.items()}
            for k in keys:
                if k.lower() in lower_row:
                    val = lower_row[k.lower()]
                    if val is not None and str(val).strip():
                        return val
            return default

        invoice_num = str(get(
            ["invoice_number", "invoice_no", "inv_no", "invoicenumber", "invoice_id"],
            default=f"INV-{context.data_uuid[:8]}-{index:04d}",
        ))

        line_items: list[TransformedLineItem] = []
        raw_items = get(["line_items", "items", "lines"], default=[])
        if isinstance(raw_items, list):
            for li_idx, item in enumerate(raw_items):
                if isinstance(item, dict):
                    li_row = {k.lower().replace(" ", "_"): v for k, v in item.items()}
                    line_items.append(TransformedLineItem(
                        line_number=li_idx + 1,
                        description=str(li_row.get("description", li_row.get("item", ""))),
                        quantity=str(li_row.get("quantity", li_row.get("qty", "1"))),
                        unit_price=str(li_row.get("unit_price", li_row.get("price", "0"))),
                        line_total=str(li_row.get("line_total", li_row.get("amount", li_row.get("total", "0")))),
                        unit_of_measure=li_row.get("unit_of_measure", li_row.get("uom")),
                        hs_code=li_row.get("hs_code", li_row.get("hsn_code")),
                        customer_sku=li_row.get("sku", li_row.get("product_code")),
                        item_type=li_row.get("type", li_row.get("item_type")),
                    ))

        return TransformedInvoice(
            invoice_number=invoice_num,
            direction=str(get(["direction"], "OUTBOUND")),
            document_type=str(get(["document_type", "doc_type"], "COMMERCIAL_INVOICE")),
            transaction_type=str(get(["transaction_type"], "B2B")),
            firs_invoice_type_code=str(get(["firs_invoice_type_code", "invoice_type_code"], "380")),
            issue_date=str(get(["issue_date", "invoice_date", "date"], "")),
            due_date=get(["due_date", "payment_due_date"]),
            currency_code=str(get(["currency", "currency_code"], "NGN")),
            total_amount=str(get(["total_amount", "total", "amount", "invoice_total"], "0")),
            tax_exclusive_amount=str(get(["tax_exclusive_amount", "subtotal", "net_amount"], "0")),
            total_tax_amount=str(get(["total_tax_amount", "tax_amount", "vat_amount", "tax"], "0")),
            seller_business_name=get(["seller_name", "seller_business_name", "supplier", "vendor"]),
            seller_tin=get(["seller_tin", "supplier_tin"]),
            seller_rc_number=get(["seller_rc_number", "supplier_rc"]),
            seller_email=get(["seller_email"]),
            seller_phone=get(["seller_phone"]),
            seller_address=get(["seller_address", "supplier_address"]),
            seller_city=get(["seller_city"]),
            seller_state=get(["seller_state"]),
            buyer_business_name=get(["buyer_name", "buyer_business_name", "customer", "customer_name"]),
            buyer_tin=get(["buyer_tin", "customer_tin"]),
            buyer_rc_number=get(["buyer_rc_number", "customer_rc"]),
            buyer_email=get(["buyer_email", "customer_email"]),
            buyer_phone=get(["buyer_phone", "customer_phone"]),
            buyer_address=get(["buyer_address", "customer_address"]),
            buyer_city=get(["buyer_city", "customer_city"]),
            buyer_state=get(["buyer_state", "customer_state"]),
            line_items=line_items,
            source_file_uuid=context.data_uuid,
        )

    def _passthrough_hlm(
        self,
        parse_result: ParseResult,
        context: PipelineContext,
    ) -> TransformResult:
        """HLM files are pre-transformed — extract data directly."""
        raw = parse_result.raw_data
        data = raw if isinstance(raw, dict) else {}

        invoices_raw = data.get("invoices", [])
        invoices = []
        for inv_data in invoices_raw:
            if isinstance(inv_data, dict):
                inv = TransformedInvoice(
                    invoice_number=inv_data.get("invoice_number", ""),
                    total_amount=str(inv_data.get("total_amount", "0")),
                    issue_date=inv_data.get("issue_date", ""),
                    source_file_uuid=context.data_uuid,
                )
                invoices.append(inv)

        return TransformResult(
            invoices=invoices,
            metadata=TransformMetadata(
                is_default_script=True,
                invoice_count=len(invoices),
                line_item_count=0,
            ),
        )

    async def _load_script(self, company_id: str) -> Any:
        """Load transformation script from DB, or return default."""
        try:
            from transforma import TransformationScript

            async with self._pool.connection() as conn:
                await conn.execute("SET search_path TO core")
                cur = await conn.execute(
                    """
                    SELECT script_id, company_id, script_name, script_type,
                           script_code, is_active
                    FROM transformation_scripts
                    WHERE company_id = %s AND is_active = TRUE
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (company_id,),
                )
                row = await cur.fetchone()

            if row:
                return TransformationScript(
                    script_id=str(row[0]),
                    company_id=row[1],
                    version="1.0",
                    config_hash="",
                    extract_module=row[4],
                    validate_module="",
                    format_module="",
                    enrich_module="",
                    customer_profile={},
                    created_at="",
                    updated_at="",
                )

            # Return a minimal default script
            return TransformationScript(
                script_id="default",
                company_id=company_id,
                version="1.0",
                config_hash="default",
                extract_module="",
                validate_module="",
                format_module="",
                enrich_module="",
                customer_profile={},
                created_at="",
                updated_at="",
            )
        except Exception as e:
            logger.warning("script_load_failed", error=str(e), company_id=company_id)
            raise TransformError(
                f"Failed to load transformation script: {e}",
                error_code="WS2-T001",
            ) from e

    async def _build_irn_checker(self) -> Any:
        """Build IRN checker callback for Transforma."""
        pool = self._pool

        async def check_irns(irns: list[str]) -> set[str]:
            async with pool.connection() as conn:
                await conn.execute("SET search_path TO core")
                cur = await conn.execute(
                    "SELECT irn FROM invoices WHERE irn = ANY(%s)",
                    (irns,),
                )
                rows = await cur.fetchall()
                return {r[0] for r in rows}

        return check_irns

    def _build_event_emitter(self, context: PipelineContext) -> Any:
        """Build event emitter callback for Transforma → SSE."""

        async def emit(event_type: str, data: dict) -> None:
            logger.debug(
                "transforma_event",
                event_type=event_type,
                data_uuid=context.data_uuid,
                **{k: v for k, v in data.items() if k != "data_uuid"},
            )

        return emit

    def _convert_transforma_result(self, result: Any, script: Any) -> TransformResult:
        """Convert Transforma TransformationResult to WS2 TransformResult."""
        invoices = []
        for inv in result.invoices:
            line_items = []
            if hasattr(inv, "line_items"):
                for li in inv.line_items:
                    line_items.append(TransformedLineItem(
                        line_number=getattr(li, "line_number", 0),
                        description=getattr(li, "description", ""),
                        quantity=str(getattr(li, "quantity", "1")),
                        unit_price=str(getattr(li, "unit_price", "0")),
                        line_total=str(getattr(li, "line_total", "0")),
                        unit_of_measure=getattr(li, "unit_of_measure", None),
                        hs_code=getattr(li, "hsn_code", None),
                        customer_sku=getattr(li, "customer_sku", None),
                        item_type=getattr(li, "type", None),
                    ))

            invoices.append(TransformedInvoice(
                invoice_number=getattr(inv, "invoice_number", ""),
                helium_invoice_no=getattr(inv, "helium_invoice_no", ""),
                direction=getattr(inv, "direction", "OUTBOUND"),
                document_type=getattr(inv, "document_type", "COMMERCIAL_INVOICE"),
                transaction_type=getattr(inv, "transaction_type", "B2B"),
                firs_invoice_type_code=getattr(inv, "firs_invoice_type_code", "380"),
                issue_date=getattr(inv, "issue_date", ""),
                due_date=getattr(inv, "due_date", None),
                total_amount=str(getattr(inv, "total_amount", "0")),
                tax_exclusive_amount=str(getattr(inv, "subtotal", "0")),
                total_tax_amount=str(getattr(inv, "tax_amount", "0")),
                seller_business_name=getattr(inv, "seller_business_name", None),
                seller_tin=getattr(inv, "seller_tin", None),
                buyer_business_name=getattr(inv, "buyer_business_name", None),
                buyer_tin=getattr(inv, "buyer_tin", None),
                line_items=line_items,
                confidence=getattr(inv, "_compliance_score", 0.0) or 0.0,
            ))

        customers = []
        for c in result.customers:
            customers.append(ExtractedCustomer(
                business_name=getattr(c, "company_name", None),
                tin=getattr(c, "tin", None),
                rc_number=getattr(c, "rc_number", None),
                email=getattr(c, "email", None),
                phone=getattr(c, "phone", None),
                address=getattr(c, "address", None),
                city=getattr(c, "city", None),
                state=getattr(c, "state", None),
            ))

        products = []
        for p in result.inventory:
            products.append(ExtractedProduct(
                description=getattr(p, "product_name", ""),
                customer_sku=getattr(p, "customer_sku", None),
                hs_code=getattr(p, "hsn_code", None),
                unit_of_measure=getattr(p, "unit_of_measure", None),
                item_type=getattr(p, "type", None),
            ))

        red_flags = []
        for rf in result.red_flags:
            red_flags.append(RedFlag(
                type=getattr(rf, "type", "unknown"),
                severity=getattr(rf, "severity", "warning"),
                message=getattr(rf, "message", ""),
                phase=getattr(rf, "phase", "transform"),
                invoice_index=getattr(rf, "row_index", None),
                field=getattr(rf, "field_name", None),
            ))

        meta = result.metadata
        return TransformResult(
            invoices=invoices,
            customers=customers,
            inventory=products,
            red_flags=red_flags,
            metadata=TransformMetadata(
                script_id=getattr(meta, "script_id", None),
                is_default_script=getattr(script, "script_id", "") == "default",
                invoice_count=len(invoices),
                line_item_count=sum(len(i.line_items) for i in invoices),
            ),
        )
