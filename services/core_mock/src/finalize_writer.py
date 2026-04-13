"""
Finalize Writer — Writes reviewed invoices to PostgreSQL invoices table.

Called when Float sends the finalized .hlm data back to Core.
Generates IRN + QR, commits records, returns statistics.
"""

import hashlib
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger("core.mock.finalize")

FIRS_SERVICE_ID = "A8BM72KQ"


def _uuid_hex():
    return uuid.uuid4().hex[:16].upper()


def _generate_irn(invoice_number: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", invoice_number).upper()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{cleaned}-{FIRS_SERVICE_ID}-{date_str}"


def _generate_csid(irn: str) -> str:
    return hashlib.sha256(irn.encode()).hexdigest()[:32].upper()


class FinalizeWriter:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def finalize_invoices(
        self,
        data_uuid: str,
        company_id: str,
        hlm_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Write finalized invoices to the invoices table.

        If hlm_data is provided, uses it. Otherwise generates mock data.
        Returns statistics and IRN list.
        """
        # If hlm_data provided, use it; otherwise generate mock invoices
        if hlm_data and isinstance(hlm_data, list):
            invoices = hlm_data
        elif hlm_data and isinstance(hlm_data, dict) and "rows" in hlm_data:
            invoices = hlm_data["rows"]
        else:
            # Generate 8-12 mock invoices for finalization
            count = random.randint(8, 12)
            invoices = self._generate_mock_invoices(count)

        irn_list = []
        invoices_created = 0

        async with self._pool.acquire() as conn:
            for inv in invoices:
                invoice_id = _uuid_hex()
                inv_num = inv.get("invoice_number", f"ABB-{random.randint(1000000, 9999999):07d}")
                irn = _generate_irn(inv_num)
                csid = _generate_csid(irn)
                helium_no = f"PRO-ABBEY-{invoice_id}"

                subtotal = float(inv.get("subtotal", inv.get("fee_amount", 0)))
                vat = float(inv.get("vat_amount", round(subtotal * 0.075, 2)))
                total = round(subtotal + vat, 2)

                try:
                    await conn.execute("""
                        INSERT INTO invoices (
                            tenant_id, invoice_id, helium_invoice_no, invoice_number, irn,
                            csid, csid_status, direction, document_type, firs_invoice_type_code,
                            transaction_type, issue_date, subtotal, tax_amount, total_amount,
                            payment_means, workflow_status, transmission_status, payment_status,
                            company_id, seller_name, seller_tin, seller_address, seller_city,
                            buyer_name, buyer_tin,
                            product_summary, line_items_count, source, source_id,
                            schema_version_applied, sign_date, finalized_at
                        ) VALUES (
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                            $16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33
                        )
                    """,
                        company_id, invoice_id, helium_no, inv_num, irn,
                        csid, "ISSUED", "OUTBOUND", "COMMERCIAL_INVOICE", "380",
                        inv.get("transaction_type", "B2C"),
                        inv.get("issue_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                        subtotal, vat, total,
                        "BANK_TRANSFER", "TRANSMITTED", "TRANSMIT_PENDING", "UNPAID",
                        company_id,
                        inv.get("seller_name", "Abbey Mortgage Bank PLC"),
                        inv.get("seller_tin", "02345678-0001"),
                        "3 Abiola Segun Akinola Crescent, off Obafemi Awolowo Way",
                        "Ikeja",
                        inv.get("buyer_name", "Unknown"),
                        inv.get("buyer_tin", ""),
                        inv.get("description", inv.get("product_summary", "")),
                        1, "bulk_upload", data_uuid,
                        "2.1.3.0",
                        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        datetime.now(timezone.utc).isoformat(),
                    )
                    irn_list.append(irn)
                    invoices_created += 1
                except Exception as e:
                    logger.error(f"Failed to insert invoice {inv_num}: {e}")

        logger.info(f"Finalized {invoices_created} invoices into invoices.db")

        return {
            "statistics": {
                "invoices_created": invoices_created,
                "customers_created": 0,
                "customers_updated": 0,
                "inventory_created": 0,
                "inventory_updated": 0,
                "queued_to_edge": invoices_created,
                "finalization_time_ms": random.randint(800, 2500),
            },
            "irn_list": irn_list,
            "warnings": [],
        }

    def _generate_mock_invoices(self, count: int) -> List[Dict]:
        """Generate mock invoice data for finalization."""
        fees = [
            ("Mortgage Origination Fee", 150000, 500000),
            ("Property Valuation Fee", 50000, 200000),
            ("Legal & Documentation Fee", 75000, 250000),
            ("Credit Life Insurance Premium", 25000, 80000),
            ("Account Maintenance Fee", 3000, 10000),
            ("Statement & Certificate Fee", 5000, 25000),
        ]
        buyers = [
            "Adewale Ogundimu", "Ngozi Eze", "Olumide Fashola",
            "Fatima Abdullahi", "Chukwuemeka Nwankwo", "Tunde Bakare",
            "Nneka Obi", "Ibrahim Suleiman", "Yetunde Coker", "Dayo Oni",
        ]

        invoices = []
        for i in range(count):
            fee_name, min_amt, max_amt = random.choice(fees)
            amount = round(random.uniform(min_amt, max_amt), 2)
            is_exempt = "Insurance" in fee_name
            vat = 0.0 if is_exempt else round(amount * 0.075, 2)

            invoices.append({
                "invoice_number": f"ABB-{random.randint(1000000, 9999999):07d}",
                "buyer_name": random.choice(buyers),
                "description": fee_name,
                "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "subtotal": amount,
                "vat_amount": vat,
                "transaction_type": "B2C",
                "seller_name": "Abbey Mortgage Bank PLC",
                "seller_tin": "02345678-0001",
            })

        return invoices
