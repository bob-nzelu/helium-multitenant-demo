"""
Phase 5: Resolver — entity resolution (customer + inventory matching).

Per DEC-WS2-001: Fuzzy match threshold 0.85 Levenshtein.
Per DEC-WS2-015: Auto-select threshold 0.95.
Per DEC-WS2-010: Customer scope GLOBAL, inventory scope COMPANY-SCOPED.
Per DEC-WS2-006: Provisional records in-memory only (not persisted until WS5).
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool
from uuid6 import uuid7

from src.config import CoreConfig

from .models import (
    EnrichResult,
    MatchType,
    PipelineContext,
    ProductMatchType,
    ProvisionalRecords,
    RedFlag,
    ResolvedCustomer,
    ResolvedInvoice,
    ResolvedLineItem,
    ResolvedProduct,
    ResolveMetadata,
    ResolveResult,
)
from .name_utils import levenshtein_ratio, normalize_name

logger = structlog.get_logger()


class ResolveError(Exception):
    """Phase 5 catastrophic failure."""

    def __init__(self, message: str, error_code: str = "WS2-R000"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)


class Resolver:
    """Phase 5: Entity resolution — link invoices to customer/inventory master records."""

    def __init__(self, pool: AsyncConnectionPool, config: CoreConfig, audit_logger=None) -> None:
        self._pool = pool
        self._config = config
        self._audit_logger = audit_logger

        # Caches loaded by _preload_caches
        self._customers: list[dict[str, Any]] = []
        self._customer_variants: list[dict[str, Any]] = []
        self._inventory: dict[str, list[dict[str, Any]]] = {}  # company_id → products
        self._inventory_variants: dict[str, list[dict[str, Any]]] = {}

    async def resolve(
        self,
        enrich_result: EnrichResult,
        context: PipelineContext,
    ) -> ResolveResult:
        """Execute Phase 5 entity resolution."""
        start = time.monotonic()

        # WS6: Audit resolve.started
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="resolve.started",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "invoice_count": len(enrich_result.invoices),
                    "customer_count": len(enrich_result.customers),
                },
            )

        red_flags = list(enrich_result.red_flags)

        # Pre-load caches
        await self._preload_caches([context.company_id])

        resolved_invoices: list[ResolvedInvoice] = []
        all_customers: dict[str, ResolvedCustomer] = {}
        all_products: dict[str, ResolvedProduct] = {}
        customers_matched = 0
        customers_created = 0
        products_matched = 0
        products_created = 0

        for idx, invoice in enumerate(enrich_result.invoices):
            # Resolve customer (buyer side)
            cust_resolution = await self._resolve_customer(
                invoice.buyer_business_name,
                invoice.buyer_tin,
                invoice.buyer_rc_number,
                invoice.buyer_address,
                context.company_id,
            )

            if cust_resolution.is_provisional:
                customers_created += 1
            else:
                customers_matched += 1
            all_customers[cust_resolution.customer_id] = cust_resolution

            # WS6: Audit resolve.customer_matched
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="resolve.customer_matched",
                    entity_type="customer",
                    entity_id=cust_resolution.customer_id,
                    action="PROCESS",
                    company_id=context.company_id,
                    x_trace_id=context.trace_id,
                    metadata={
                        "match_type": cust_resolution.match_type,
                        "confidence": cust_resolution.match_confidence,
                        "is_provisional": cust_resolution.is_provisional,
                        "invoice_index": idx,
                    },
                )

            # Resolve line items (inventory)
            resolved_items: list[ResolvedLineItem] = []
            for li in invoice.line_items:
                prod_resolution = await self._resolve_inventory_item(
                    li.description,
                    li.customer_sku,
                    li.helium_sku,
                    getattr(li, "hs_code", None),
                    context.company_id,
                )

                if prod_resolution.is_provisional:
                    products_created += 1
                else:
                    products_matched += 1
                all_products[prod_resolution.product_id] = prod_resolution

                resolved_items.append(ResolvedLineItem(
                    line_number=li.line_number,
                    description=li.description,
                    quantity=li.quantity,
                    unit_price=li.unit_price,
                    line_total=li.line_total,
                    unit_of_measure=li.unit_of_measure,
                    tax_amount=li.tax_amount,
                    tax_rate=li.tax_rate,
                    hs_code=getattr(li, "hs_code", None),
                    customer_sku=li.customer_sku,
                    helium_sku=li.helium_sku,
                    item_type=getattr(li, "item_type", None),
                    hs_code_confidence=getattr(li, "hs_code_confidence", None),
                    hs_code_source=getattr(li, "hs_code_source", "ORIGINAL"),
                    category=getattr(li, "category", None),
                    subcategory=getattr(li, "subcategory", None),
                    category_confidence=getattr(li, "category_confidence", None),
                    service_code=getattr(li, "service_code", None),
                    service_code_confidence=getattr(li, "service_code_confidence", None),
                    product_id=prod_resolution.product_id,
                    product_match_type=prod_resolution.match_type,
                    product_match_confidence=prod_resolution.match_confidence,
                ))

            # Build resolved invoice
            resolved_inv = ResolvedInvoice(
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
                buyer_business_name=invoice.buyer_business_name,
                buyer_tin=invoice.buyer_tin,
                buyer_rc_number=invoice.buyer_rc_number,
                line_items=resolved_items,
                source_file_uuid=getattr(invoice, "source_file_uuid", ""),
                confidence=invoice.confidence,
                seller_address_validated=getattr(invoice, "seller_address_validated", False),
                buyer_address_validated=getattr(invoice, "buyer_address_validated", False),
                seller_lga=getattr(invoice, "seller_lga", None),
                seller_lga_code=getattr(invoice, "seller_lga_code", None),
                seller_state_code=getattr(invoice, "seller_state_code", None),
                buyer_lga=getattr(invoice, "buyer_lga", None),
                buyer_lga_code=getattr(invoice, "buyer_lga_code", None),
                buyer_state_code=getattr(invoice, "buyer_state_code", None),
                customer_id=cust_resolution.customer_id,
                customer_match_type=cust_resolution.match_type,
                customer_match_confidence=cust_resolution.match_confidence,
            )

            # Compute overall confidence (add entity resolution 15%)
            resolved_inv.overall_confidence = self._compute_overall_confidence(
                resolved_inv, cust_resolution, resolved_items
            )
            resolved_invoices.append(resolved_inv)

        # Build provisional records
        new_customers = [c for c in all_customers.values() if c.is_provisional]
        new_products = [p for p in all_products.values() if p.is_provisional]

        # Red flags for new entities
        if new_customers:
            red_flags.append(RedFlag(
                type="new_customers_created",
                severity="info",
                message=f"{len(new_customers)} new customer(s) will be created",
                phase="resolve",
            ))
        if new_products:
            red_flags.append(RedFlag(
                type="new_products_created",
                severity="info",
                message=f"{len(new_products)} new product(s) will be created",
                phase="resolve",
            ))

        elapsed = int((time.monotonic() - start) * 1000)

        cust_confidences = [c.match_confidence for c in all_customers.values()]
        prod_confidences = [p.match_confidence for p in all_products.values()]

        logger.info(
            "resolve_complete",
            data_uuid=context.data_uuid,
            customers_matched=customers_matched,
            customers_created=customers_created,
            products_matched=products_matched,
            products_created=products_created,
            duration_ms=elapsed,
        )

        # WS6: Audit resolve.completed
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="resolve.completed",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "customers_matched": customers_matched,
                    "customers_created": customers_created,
                    "products_matched": products_matched,
                    "products_created": products_created,
                    "duration_ms": elapsed,
                },
            )

        return ResolveResult(
            invoices=resolved_invoices,
            customers=list(all_customers.values()),
            inventory=list(all_products.values()),
            red_flags=red_flags,
            metadata=ResolveMetadata(
                resolve_time_ms=elapsed,
                customers_matched=customers_matched,
                customers_created=customers_created,
                products_matched=products_matched,
                products_created=products_created,
                avg_customer_confidence=(
                    sum(cust_confidences) / len(cust_confidences)
                    if cust_confidences else 0.0
                ),
                avg_product_confidence=(
                    sum(prod_confidences) / len(prod_confidences)
                    if prod_confidences else 0.0
                ),
            ),
            provisional_records=ProvisionalRecords(
                new_customers=new_customers,
                new_products=new_products,
            ),
        )

    async def _preload_caches(self, company_ids: list[str]) -> None:
        """Pre-load customer and inventory data for batch resolution."""
        try:
            async with self._pool.connection() as conn:
                # Customers (global scope)
                cur = await conn.execute(
                    """
                    SELECT customer_id, tin, rc_number, company_name,
                           company_name_normalized, email, phone,
                           address, city, state, country
                    FROM customers WHERE deleted_at IS NULL
                    """
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
                self._customers = [dict(zip(cols, r)) for r in rows]

                # Customer name variants
                cur = await conn.execute(
                    """
                    SELECT customer_id, name_variant, name_variant_normalized
                    FROM customer_name_variants
                    """
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
                self._customer_variants = [dict(zip(cols, r)) for r in rows]

                # Inventory (company-scoped)
                for cid in company_ids:
                    cur = await conn.execute(
                        """
                        SELECT product_id, helium_sku, customer_sku,
                               product_name, product_name_normalized,
                               hs_code, service_code, product_category,
                               type
                        FROM inventory
                        WHERE deleted_at IS NULL
                        """,
                    )
                    rows = await cur.fetchall()
                    cols = [d[0] for d in cur.description]
                    self._inventory[cid] = [dict(zip(cols, r)) for r in rows]

                    # Inventory name variants
                    cur = await conn.execute(
                        """
                        SELECT product_id, name_variant, name_variant_normalized
                        FROM inventory_name_variants
                        """
                    )
                    rows = await cur.fetchall()
                    cols = [d[0] for d in cur.description]
                    self._inventory_variants[cid] = [dict(zip(cols, r)) for r in rows]

            logger.info(
                "resolution_caches_loaded",
                customers=len(self._customers),
                customer_variants=len(self._customer_variants),
                inventory_companies=len(self._inventory),
            )
        except Exception as e:
            logger.error("cache_load_failed", error=str(e))
            raise ResolveError(
                f"Failed to load resolution caches: {e}",
                error_code="WS2-R003",
            ) from e

    async def _resolve_customer(
        self,
        name: str | None,
        tin: str | None,
        rc_number: str | None,
        address: str | None,
        company_id: str,
    ) -> ResolvedCustomer:
        """
        Resolve a customer using the 4-step algorithm:
        1. Exact TIN match
        2. Exact RC number match
        3. Fuzzy name match (threshold: 0.85)
        4. Create provisional new customer
        """
        threshold = self._config.fuzzy_match_threshold
        auto_threshold = self._config.fuzzy_auto_select_threshold

        # Step 1: TIN exact match
        if tin:
            for c in self._customers:
                if c.get("tin") and c["tin"] == tin:
                    return self._customer_from_db(c, MatchType.TIN_EXACT, 1.0)

        # Step 2: RC exact match
        if rc_number:
            for c in self._customers:
                if c.get("rc_number") and c["rc_number"].upper() == rc_number.upper():
                    return self._customer_from_db(c, MatchType.RC_EXACT, 1.0)

        # Step 3: Fuzzy name match
        if name:
            normalized = normalize_name(name)
            best_match: dict | None = None
            best_score = 0.0

            # Check canonical names
            for c in self._customers:
                cn = c.get("company_name_normalized", "")
                if not cn:
                    continue
                score = levenshtein_ratio(normalized, cn)
                if score > best_score:
                    best_score = score
                    best_match = c

            # Check name variants
            for v in self._customer_variants:
                vn = v.get("name_variant_normalized", "")
                if not vn:
                    continue
                score = levenshtein_ratio(normalized, vn)
                if score > best_score:
                    best_score = score
                    # Find the customer by customer_id
                    for c in self._customers:
                        if c["customer_id"] == v["customer_id"]:
                            best_match = c
                            break

            if best_match and best_score >= threshold:
                match_type = MatchType.FUZZY_NAME
                if best_score >= auto_threshold:
                    logger.debug(
                        "customer_auto_matched",
                        name=name,
                        matched=best_match.get("company_name"),
                        score=best_score,
                    )
                else:
                    logger.debug(
                        "customer_fuzzy_matched",
                        name=name,
                        matched=best_match.get("company_name"),
                        score=best_score,
                    )
                return self._customer_from_db(best_match, match_type, best_score)

        # Step 4: Create provisional
        new_id = str(uuid7())
        return ResolvedCustomer(
            customer_id=new_id,
            company_name=name or "",
            company_name_normalized=normalize_name(name) if name else "",
            tin=tin,
            rc_number=rc_number,
            address=address,
            is_provisional=True,
            match_type=MatchType.NEW.value,
            match_confidence=0.0,
            created_source="PIPELINE_AUTO",
        )

    async def _resolve_inventory_item(
        self,
        description: str,
        customer_sku: str | None,
        helium_sku: str | None,
        hs_code: str | None,
        company_id: str,
    ) -> ResolvedProduct:
        """
        Resolve an inventory item using the 3-step algorithm:
        1. Exact Helium SKU match
        2. Exact customer SKU match (company-scoped)
        3. Fuzzy product name match (threshold: 0.85)
        4. Create provisional new product
        """
        threshold = self._config.fuzzy_match_threshold
        products = self._inventory.get(company_id, [])
        variants = self._inventory_variants.get(company_id, [])

        # Step 1: Helium SKU exact match
        if helium_sku:
            for p in products:
                if p.get("helium_sku") and p["helium_sku"] == helium_sku:
                    return self._product_from_db(p, ProductMatchType.HELIUM_SKU_EXACT, 1.0, company_id)

        # Step 2: Customer SKU exact match
        if customer_sku:
            for p in products:
                if p.get("customer_sku") and p["customer_sku"] == customer_sku:
                    return self._product_from_db(p, ProductMatchType.CUSTOMER_SKU_EXACT, 1.0, company_id)

        # Step 3: Fuzzy name match
        if description:
            normalized = normalize_name(description)
            best_match: dict | None = None
            best_score = 0.0

            for p in products:
                pn = p.get("product_name_normalized", "")
                if not pn:
                    continue
                score = levenshtein_ratio(normalized, pn)
                if score > best_score:
                    best_score = score
                    best_match = p

            for v in variants:
                vn = v.get("name_variant_normalized", "")
                if not vn:
                    continue
                score = levenshtein_ratio(normalized, vn)
                if score > best_score:
                    best_score = score
                    for p in products:
                        if p["product_id"] == v["product_id"]:
                            best_match = p
                            break

            if best_match and best_score >= threshold:
                return self._product_from_db(
                    best_match, ProductMatchType.FUZZY_NAME, best_score, company_id
                )

        # Step 4: Provisional new product
        new_id = str(uuid7())
        return ResolvedProduct(
            product_id=new_id,
            product_name=description or "",
            product_name_normalized=normalize_name(description) if description else "",
            customer_sku=customer_sku,
            hs_code=hs_code,
            item_type="GOODS",
            company_id=company_id,
            is_provisional=True,
            match_type=ProductMatchType.NEW.value,
            match_confidence=0.0,
            created_source="PIPELINE_AUTO",
        )

    @staticmethod
    def _customer_from_db(
        row: dict[str, Any], match_type: MatchType, confidence: float
    ) -> ResolvedCustomer:
        return ResolvedCustomer(
            customer_id=row["customer_id"],
            company_name=row.get("company_name", ""),
            company_name_normalized=row.get("company_name_normalized", ""),
            tin=row.get("tin"),
            rc_number=row.get("rc_number"),
            email=row.get("email"),
            phone=row.get("phone"),
            address=row.get("address"),
            city=row.get("city"),
            state=row.get("state"),
            country=row.get("country"),
            is_provisional=False,
            match_type=match_type.value,
            match_confidence=confidence,
        )

    @staticmethod
    def _product_from_db(
        row: dict[str, Any], match_type: ProductMatchType, confidence: float, company_id: str
    ) -> ResolvedProduct:
        return ResolvedProduct(
            product_id=row["product_id"],
            product_name=row.get("product_name", ""),
            product_name_normalized=row.get("product_name_normalized", ""),
            helium_sku=row.get("helium_sku"),
            customer_sku=row.get("customer_sku"),
            hs_code=row.get("hs_code"),
            service_code=row.get("service_code"),
            category=row.get("product_category"),
            item_type=row.get("type"),
            company_id=company_id,
            is_provisional=False,
            match_type=match_type.value,
            match_confidence=confidence,
        )

    @staticmethod
    def _compute_overall_confidence(
        invoice: ResolvedInvoice,
        customer: ResolvedCustomer,
        items: list[ResolvedLineItem],
    ) -> float:
        """
        Compute overall confidence with entity resolution (15% weight).

        partial_confidence (from Phase 4) covers:
          textract=40%, completeness=25%, amounts=20% → max 0.85

        Entity resolution adds:
          entity=15% → brings total to max 1.0
        """
        partial = invoice.confidence  # Max 0.85 from Phase 4

        # Entity resolution score (15% weight)
        entity_scores = [customer.match_confidence]
        for item in items:
            entity_scores.append(item.product_match_confidence)

        entity_avg = (
            sum(entity_scores) / len(entity_scores) if entity_scores else 0.0
        )
        entity_component = entity_avg * 0.15

        return round(min(partial + entity_component, 1.0), 4)
