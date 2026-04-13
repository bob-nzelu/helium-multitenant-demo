"""
Record Creator — Final DB committal for finalized invoices.

Step 8 of the finalize flow: After edit validation passes, commit
invoice records, upsert customer/inventory entities, and create
line items. All writes happen in a single transaction.

Tables written:
  - invoices (INSERT)
  - invoice_line_items (INSERT)
  - customers (INSERT or UPDATE — upsert by TIN+company_id)
  - inventory (INSERT or UPDATE — upsert by customer_sku+company_id)
  - inventory_name_variants (INSERT on conflict ignore)

See: HLX_FORMAT.md v1.1 Section 9 (Finalize Flow Step 8)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)


@dataclass
class CommitResult:
    """Result of committing a batch of finalized invoices."""

    invoices_created: int = 0
    line_items_created: int = 0
    customers_created: int = 0
    customers_updated: int = 0
    inventory_created: int = 0
    inventory_updated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoices_created": self.invoices_created,
            "line_items_created": self.line_items_created,
            "customers_created": self.customers_created,
            "customers_updated": self.customers_updated,
            "inventory_created": self.inventory_created,
            "inventory_updated": self.inventory_updated,
            "errors": self.errors,
            "success": self.success,
        }


class RecordCreator:
    """Commits finalized invoice data to PostgreSQL.

    All writes within a single call to ``commit_batch`` run inside one
    transaction. If any write fails, the entire batch rolls back.
    """

    # ── Invoice Insert ───────────────────────────────────────────────

    INVOICE_INSERT = """
        INSERT INTO invoices (
            invoice_id, invoice_number, irn, qr_code_data,
            issue_date, due_date, currency_code,
            subtotal, tax_amount, total_amount,
            direction, transaction_type,
            firs_invoice_type_code,
            seller_tin, seller_name, seller_address, seller_city,
            seller_state_code, seller_country_code, seller_lga_code,
            seller_postal_code,
            buyer_tin, buyer_name, buyer_address, buyer_city,
            buyer_state_code, buyer_country_code, buyer_lga_code,
            buyer_postal_code,
            reference, category, notes_to_firs,
            payment_terms_note, terms,
            reference_irn, reference_issue_date,
            status, company_id, batch_id,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29,
            $30, $31, $32, $33, $34, $35, $36, $37, $38, $39,
            $40, $41
        )
        ON CONFLICT (invoice_id) DO NOTHING
    """

    LINE_ITEM_INSERT = """
        INSERT INTO invoice_line_items (
            line_item_id, invoice_id, line_number,
            description, quantity, unit_price,
            line_total, tax_amount, discount_amount,
            unit_of_measure, product_id,
            hsn_code, service_code,
            product_category, service_category,
            vat_treatment,
            created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14, $15, $16, $17
        )
        ON CONFLICT (line_item_id) DO NOTHING
    """

    # ── Customer Upsert ──────────────────────────────────────────────

    CUSTOMER_UPSERT = """
        INSERT INTO customers (
            customer_id, tin, customer_name, customer_name_normalized,
            address, city, state_code, country_code,
            lga_code, postal_code,
            customer_type, company_id,
            created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14
        )
        ON CONFLICT (tin, company_id) DO UPDATE SET
            customer_name = COALESCE(EXCLUDED.customer_name, customers.customer_name),
            address = COALESCE(EXCLUDED.address, customers.address),
            city = COALESCE(EXCLUDED.city, customers.city),
            state_code = COALESCE(EXCLUDED.state_code, customers.state_code),
            country_code = COALESCE(EXCLUDED.country_code, customers.country_code),
            lga_code = COALESCE(EXCLUDED.lga_code, customers.lga_code),
            postal_code = COALESCE(EXCLUDED.postal_code, customers.postal_code),
            updated_at = CURRENT_TIMESTAMP
    """

    # ── Inventory Upsert ─────────────────────────────────────────────

    INVENTORY_UPSERT = """
        INSERT INTO inventory (
            product_id, customer_sku, product_name,
            product_name_normalized, description,
            unit_of_measure, type,
            hsn_code, service_code,
            product_category, service_category,
            vat_treatment, vat_rate,
            classification_confidence, classification_source,
            created_by, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14, $15, $16, $17, $18
        )
        ON CONFLICT (product_id) DO UPDATE SET
            product_name = COALESCE(EXCLUDED.product_name, inventory.product_name),
            hsn_code = COALESCE(EXCLUDED.hsn_code, inventory.hsn_code),
            service_code = COALESCE(EXCLUDED.service_code, inventory.service_code),
            product_category = COALESCE(EXCLUDED.product_category, inventory.product_category),
            service_category = COALESCE(EXCLUDED.service_category, inventory.service_category),
            vat_treatment = COALESCE(EXCLUDED.vat_treatment, inventory.vat_treatment),
            classification_confidence = EXCLUDED.classification_confidence,
            classification_source = EXCLUDED.classification_source,
            total_times_invoiced = inventory.total_times_invoiced + 1,
            last_invoice_date = EXCLUDED.updated_at,
            updated_at = CURRENT_TIMESTAMP
    """

    INVENTORY_VARIANT_INSERT = """
        INSERT INTO inventory_name_variants (
            product_id, name_variant, name_variant_normalized,
            source, first_seen_at, last_seen_at, occurrence_count
        ) VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
        ON CONFLICT (product_id, name_variant_normalized) DO UPDATE SET
            last_seen_at = CURRENT_TIMESTAMP,
            occurrence_count = inventory_name_variants.occurrence_count + 1
    """

    async def commit_batch(
        self,
        conn: AsyncConnection,
        rows: list[dict[str, Any]],
        company_id: str,
        batch_id: str,
        created_by: str | None = None,
    ) -> CommitResult:
        """Commit a batch of finalized invoice rows in a single transaction.

        Args:
            conn: Active async connection (caller manages transaction).
            rows: Finalized invoice rows (from validated .hlm).
            company_id: Tenant company identifier.
            batch_id: Batch/HLX identifier for grouping.
            created_by: helium_user_id of the user who finalized.

        Returns:
            CommitResult with counts and any errors.
        """
        result = CommitResult()
        now = datetime.utcnow().isoformat()

        try:
            async with conn.transaction():
                for row in rows:
                    await self._commit_invoice(
                        conn, row, company_id, batch_id, now, result
                    )
                    await self._commit_line_items(conn, row, now, result)
                    await self._upsert_customer(
                        conn, row, company_id, now, result
                    )
                    await self._upsert_inventory_from_line_items(
                        conn, row, company_id, created_by, now, result
                    )

        except Exception as e:
            logger.error("commit_batch failed: %s", e, exc_info=True)
            result.errors.append(f"Transaction failed: {e}")

        return result

    async def _commit_invoice(
        self,
        conn: AsyncConnection,
        row: dict[str, Any],
        company_id: str,
        batch_id: str,
        now: str,
        result: CommitResult,
    ) -> None:
        """Insert a single invoice row."""
        params = (
            row["invoice_id"],
            row["invoice_number"],
            row.get("irn"),
            row.get("qr_code_data"),
            row.get("issue_date"),
            row.get("due_date"),
            row.get("currency_code", "NGN"),
            row.get("subtotal", 0),
            row.get("tax_amount", 0),
            row.get("total_amount", 0),
            row.get("direction", "OUTBOUND"),
            row.get("transaction_type", "B2B"),
            row.get("firs_invoice_type_code"),
            row.get("seller_tin"),
            row.get("seller_name"),
            row.get("seller_address"),
            row.get("seller_city"),
            row.get("seller_state_code"),
            row.get("seller_country_code"),
            row.get("seller_lga_code"),
            row.get("seller_postal_code"),
            row.get("buyer_tin"),
            row.get("buyer_name"),
            row.get("buyer_address"),
            row.get("buyer_city"),
            row.get("buyer_state_code"),
            row.get("buyer_country_code"),
            row.get("buyer_lga_code"),
            row.get("buyer_postal_code"),
            row.get("reference"),
            row.get("category"),
            row.get("notes_to_firs"),
            row.get("payment_terms_note"),
            row.get("terms"),
            row.get("reference_irn"),
            row.get("reference_issue_date"),
            "FINALIZED",
            company_id,
            batch_id,
            now,
            now,
        )
        await conn.execute(self.INVOICE_INSERT, params)
        result.invoices_created += 1

    async def _commit_line_items(
        self,
        conn: AsyncConnection,
        row: dict[str, Any],
        now: str,
        result: CommitResult,
    ) -> None:
        """Insert line items for an invoice."""
        invoice_id = row["invoice_id"]
        line_items = row.get("line_items", [])

        for item in line_items:
            params = (
                item.get("line_item_id"),
                invoice_id,
                item.get("line_number", 0),
                item.get("description"),
                item.get("quantity", 0),
                item.get("unit_price", 0),
                item.get("line_total", 0),
                item.get("tax_amount", 0),
                item.get("discount_amount", 0),
                item.get("unit_of_measure"),
                item.get("product_id"),
                item.get("hsn_code"),
                item.get("service_code"),
                item.get("product_category"),
                item.get("service_category"),
                item.get("vat_treatment"),
                now,
            )
            await conn.execute(self.LINE_ITEM_INSERT, params)
            result.line_items_created += 1

    async def _upsert_customer(
        self,
        conn: AsyncConnection,
        row: dict[str, Any],
        company_id: str,
        now: str,
        result: CommitResult,
    ) -> None:
        """Upsert the counterparty as a customer record.

        On OUTBOUND invoices, the buyer is the counterparty.
        On INBOUND invoices, the seller is the counterparty.
        """
        direction = row.get("direction", "OUTBOUND")
        prefix = "buyer_" if direction == "OUTBOUND" else "seller_"

        tin = row.get(f"{prefix}tin")
        name = row.get(f"{prefix}name")

        # Skip if no TIN — can't upsert without identity
        if not tin:
            return

        customer_id = row.get("counterparty_customer_id") or f"CUST-{tin}"
        normalized = name.upper().strip() if name else None

        params = (
            customer_id,
            tin,
            name,
            normalized,
            row.get(f"{prefix}address"),
            row.get(f"{prefix}city"),
            row.get(f"{prefix}state_code"),
            row.get(f"{prefix}country_code"),
            row.get(f"{prefix}lga_code"),
            row.get(f"{prefix}postal_code"),
            row.get("transaction_type", "B2B"),
            company_id,
            now,
            now,
        )

        cur = await conn.execute(self.CUSTOMER_UPSERT, params)
        # statusmessage tells us INSERT vs UPDATE
        if cur.statusmessage and "INSERT" in cur.statusmessage:
            result.customers_created += 1
        else:
            result.customers_updated += 1

    async def _upsert_inventory_from_line_items(
        self,
        conn: AsyncConnection,
        row: dict[str, Any],
        company_id: str,
        created_by: str | None,
        now: str,
        result: CommitResult,
    ) -> None:
        """Upsert inventory records from invoice line items.

        Only line items with a product_id are upserted. Ad-hoc items
        (no product_id) are skipped — they don't create inventory entries.
        """
        line_items = row.get("line_items", [])
        seen_products: set[str] = set()

        for item in line_items:
            product_id = item.get("product_id")
            if not product_id or product_id in seen_products:
                continue
            seen_products.add(product_id)

            customer_sku = item.get("customer_sku", product_id[:16])
            product_name = item.get("description", "")
            normalized = product_name.upper().strip() if product_name else None
            item_type = "SERVICE" if item.get("service_code") else "GOODS"

            params = (
                product_id,
                customer_sku,
                product_name,
                normalized,
                item.get("full_description"),
                item.get("unit_of_measure"),
                item_type,
                item.get("hsn_code"),
                item.get("service_code"),
                item.get("product_category"),
                item.get("service_category"),
                item.get("vat_treatment"),
                item.get("vat_rate", 7.5),
                item.get("classification_confidence", 0),
                item.get("classification_source"),
                created_by,
                now,
                now,
            )

            cur = await conn.execute(self.INVENTORY_UPSERT, params)
            if cur.statusmessage and "INSERT" in cur.statusmessage:
                result.inventory_created += 1
            else:
                result.inventory_updated += 1

            # Track name variant for dedup
            if product_name and normalized:
                variant_params = (
                    product_id,
                    product_name,
                    normalized,
                    "invoice",
                )
                await conn.execute(
                    self.INVENTORY_VARIANT_INSERT, variant_params
                )
