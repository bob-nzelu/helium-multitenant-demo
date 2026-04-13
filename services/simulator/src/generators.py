"""
Invoice Generators — outbound (Abbey → borrower) and inbound (supplier → Abbey).

Produces invoice JSON matching Core's canonical TransformedInvoice schema:
  - Field names match Core's processing/models.py (TransformedInvoice + TransformedLineItem)
  - Amounts are strings (decimal precision)
  - Country codes are ISO 3166-1 alpha-2 ("NG" not "NGA")
  - Inbound invoices follow the FIRS payload shape exactly
"""

import random
from datetime import date, timedelta
from typing import Optional

from .catalog import CatalogManager


class OutboundGenerator:
    """Generate outbound invoices: Abbey sells mortgage services to borrowers."""

    def __init__(self, catalog: CatalogManager):
        self._catalog = catalog
        self._counters: dict[str, int] = {}  # tenant_id → next sequence

    def _next_invoice_number(self, tenant_id: str) -> str:
        seq = self._counters.get(tenant_id, 1)
        self._counters[tenant_id] = seq + 1
        prefix = self._catalog.get_tenant_config(tenant_id).get("invoice_prefix", "INV")
        return f"{prefix}-{seq:07d}"

    def generate(
        self,
        tenant_id: str,
        issue_date: Optional[date] = None,
    ) -> dict:
        """Generate one outbound invoice matching Core's TransformedInvoice schema."""
        cat = self._catalog
        config = cat.get_tenant_config(tenant_id)
        buyer = cat.get_random_buyer(tenant_id)
        fees = cat.get_random_fees(tenant_id)
        branch = cat.get_random_branch(tenant_id)

        if issue_date is None:
            issue_date = date.today()
        due_date = issue_date + timedelta(days=30)

        invoice_number = self._next_invoice_number(tenant_id)

        # Build line items (TransformedLineItem schema)
        line_items = []
        subtotal = 0.0
        total_tax = 0.0

        for i, fee in enumerate(fees, start=1):
            unit_price = round(random.uniform(fee["min_amount"], fee["max_amount"]), 2)
            tax_rate = fee["vat_rate"]
            tax_amount = round(unit_price * tax_rate / 100, 2)
            line_total = round(unit_price + tax_amount, 2)

            line_items.append({
                "line_number": i,
                "description": fee["name"],
                "quantity": "1",
                "unit_price": str(unit_price),
                "line_total": str(line_total),
                "unit_of_measure": fee.get("unit_of_measure", "unit"),
                "tax_amount": str(tax_amount),
                "tax_rate": str(tax_rate),
                "item_type": fee.get("type", "SERVICE"),
                "customer_sku": fee["sku"],
                "hs_code": fee.get("service_code", ""),
            })
            subtotal += unit_price
            total_tax += tax_amount

        subtotal = round(subtotal, 2)
        total_tax = round(total_tax, 2)
        total_amount = round(subtotal + total_tax, 2)

        # Buyer fields — borrowers have "name", enterprises have "company_name"
        buyer_name = buyer.get("name") or buyer.get("company_name", "")
        buyer_type = buyer.get("type") or buyer.get("customer_type", "B2C")

        invoice = {
            # Identity
            "invoice_number": invoice_number,

            # Classification
            "direction": "OUTBOUND",
            "document_type": "COMMERCIAL_INVOICE",
            "transaction_type": "B2B" if buyer_type == "B2B" else "B2C",
            "firs_invoice_type_code": "380",

            # Dates
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),

            # Financial
            "currency_code": "NGN",
            "tax_exclusive_amount": str(subtotal),
            "total_tax_amount": str(total_tax),
            "total_amount": str(total_amount),

            # Seller = Abbey (tenant)
            "seller_business_name": config["company_name"],
            "seller_tin": config["tin"],
            "seller_rc_number": config.get("rc_number", ""),
            "seller_email": config.get("email", ""),
            "seller_phone": config.get("phone", ""),
            "seller_address": config["address"],
            "seller_city": config["city"],
            "seller_state": config["state_code"],
            "seller_country": "NG",

            # Buyer
            "buyer_business_name": buyer_name,
            "buyer_tin": buyer.get("tin", ""),
            "buyer_rc_number": buyer.get("rc_number", ""),
            "buyer_email": buyer.get("email", ""),
            "buyer_phone": buyer.get("phone", ""),
            "buyer_address": buyer.get("address", ""),
            "buyer_city": buyer.get("city", ""),
            "buyer_state": buyer.get("state_code", ""),
            "buyer_country": "NG",

            # Line items
            "line_items": line_items,

            # Source context
            "source": "Simulator",
            "source_id": config.get("api_key", ""),
            "tenant_id": tenant_id,

            # Branch (simulator metadata, not in Core schema)
            "branch_name": branch["name"],
            "branch_city": branch["city"],
        }

        return invoice

    def generate_bulk(
        self,
        tenant_id: str,
        count: int,
        random_dates: bool = True,
    ) -> list[dict]:
        """Generate multiple outbound invoices."""
        invoices = []
        for _ in range(count):
            d = None
            if random_dates:
                d = date.today() - timedelta(days=random.randint(0, 30))
            invoices.append(self.generate(tenant_id, issue_date=d))
        return invoices


class InboundGenerator:
    """
    Generate inbound invoices: suppliers invoice Abbey for goods/services.

    Follows the FIRS payload shape exactly — Core expects this structure
    for inbound invoices (does NOT go through Transforma).
    """

    def __init__(self, catalog: CatalogManager):
        self._catalog = catalog
        self._supplier_counters: dict[str, int] = {}  # supplier_name → seq

    def _generate_invoice_number(self, supplier: dict) -> str:
        fmt = supplier["invoice_number_format"]
        name = supplier["company_name"]
        seq = self._supplier_counters.get(name, 1)
        self._supplier_counters[name] = seq + 1

        if fmt == "{five_digit_numeric}":
            return f"{random.randint(10000, 99999)}"

        if fmt == "{seven_digit}-{five_digit}-{two_digit}":
            return f"{random.randint(1000000, 9999999)}-{random.randint(10000, 99999)}-{random.randint(10, 99)}"

        if fmt == "{ten_digit_numeric}":
            return f"{random.randint(3000000000, 3999999999)}"

        if fmt == "{eight_digit}-{four_digit}":
            return f"{random.randint(10000000, 99999999)}-{seq:04d}"

        if fmt == "{company_code}/{dept}/{location}/{sequence}/{year}":
            depts = ["HR", "IT", "OPS", "FIN"]
            locs = ["SL", "VI", "IK", "AB"]
            return f"AVON/{random.choice(depts)}/{random.choice(locs)}/{seq:02d}/{date.today().year}"

        # Fallback
        return f"INV-{seq:06d}"

    def generate(
        self,
        tenant_id: str,
        supplier_index: Optional[int] = None,
        issue_date: Optional[date] = None,
    ) -> dict:
        """
        Generate one inbound invoice matching Core's TransformedInvoice schema.

        FIRS payload shape — direction=INBOUND, source=FIRS.
        On inbound: seller = supplier, buyer = Abbey (tenant).
        """
        cat = self._catalog
        config = cat.get_tenant_config(tenant_id)
        supplier = cat.get_supplier(tenant_id, supplier_index)

        if issue_date is None:
            issue_date = date.today()
        due_date = issue_date + timedelta(days=30)

        invoice_number = self._generate_invoice_number(supplier)

        # Line items (TransformedLineItem schema)
        item_desc = random.choice(supplier["typical_items"])
        qty_min, qty_max = supplier["typical_quantity_range"]
        quantity = random.randint(qty_min, qty_max)
        amt_min, amt_max = supplier["typical_amount_range"]
        unit_price = round(random.uniform(amt_min, amt_max), 2)

        tax_rate = supplier["vat_rate"]
        line_subtotal = round(unit_price * quantity, 2)
        tax_amount = round(line_subtotal * tax_rate / 100, 2)
        line_total = round(line_subtotal + tax_amount, 2)

        line_items = [{
            "line_number": 1,
            "description": item_desc,
            "quantity": str(quantity),
            "unit_price": str(unit_price),
            "line_total": str(line_total),
            "tax_amount": str(tax_amount),
            "tax_rate": str(tax_rate),
            "item_type": "SERVICE",
        }]

        invoice = {
            # Identity
            "invoice_number": invoice_number,

            # Classification — FIRS inbound shape
            "direction": "INBOUND",
            "document_type": "COMMERCIAL_INVOICE",
            "transaction_type": "B2B",
            "firs_invoice_type_code": "380",

            # Dates
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),

            # Financial (string amounts per TransformedInvoice)
            "currency_code": "NGN",
            "tax_exclusive_amount": str(line_subtotal),
            "total_tax_amount": str(tax_amount),
            "total_amount": str(line_total),

            # Seller = Supplier (on inbound, seller is the external party)
            "seller_business_name": supplier["company_name"],
            "seller_tin": supplier["tin"],
            "seller_rc_number": supplier.get("rc_number", ""),
            "seller_email": supplier.get("email", ""),
            "seller_phone": supplier.get("phone", ""),
            "seller_address": supplier.get("address", ""),
            "seller_city": supplier.get("city", ""),
            "seller_state": supplier.get("state_code", ""),
            "seller_country": "NG",

            # Buyer = Abbey (tenant — on inbound, buyer is the receiving company)
            "buyer_business_name": config["company_name"],
            "buyer_tin": config["tin"],
            "buyer_rc_number": config.get("rc_number", ""),
            "buyer_email": config.get("email", ""),
            "buyer_phone": config.get("phone", ""),
            "buyer_address": config["address"],
            "buyer_city": config["city"],
            "buyer_state": config["state_code"],
            "buyer_country": "NG",

            # Line items
            "line_items": line_items,

            # Source context — FIRS delivery
            "source": "FIRS",
            "source_id": "firs-delivery",
            "tenant_id": tenant_id,
        }

        return invoice
