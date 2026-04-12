"""
sync.db Seeder — Pre-populates Float's local SQLite database for demo.

Usage:
    python seed_sync_db.py <path_to_sync.db>

Seeds:
  - 28 customers (5 suppliers + 20 B2C borrowers + 3 B2B enterprises)
  - 8 inventory items (mortgage fee services)
  - 15 invoices (10 outbound + 5 inbound) with line items

This is a backup for the demo — the live SSE flow should populate data
in real-time, but pre-seeding ensures Float opens with data visible.
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "abbey"


def _uuid_hex():
    return uuid.uuid4().hex[:16].upper()


def _generate_irn(invoice_number, service_id="A8BM72KQ", issue_date="2026-04-10"):
    date_part = issue_date.replace("-", "")
    return f"{invoice_number}-{service_id}-{date_part}"


def load_json(filename):
    with open(DATA_DIR / filename) as f:
        return json.load(f)


def seed_customers(conn):
    """Seed 28 customers into sync.db."""
    suppliers = load_json("suppliers.json")
    borrowers = load_json("borrowers.json")
    enterprises = load_json("enterprises.json")

    seq = 1
    for s in suppliers:
        cid = f"cust-abbey-sup{seq:02d}"
        conn.execute("""
            INSERT OR IGNORE INTO customers (
                customer_id, tin, rc_number, primary_identifier,
                company_name, company_name_normalized, customer_code,
                email, phone, address, city, state, state_code,
                country, customer_type, tax_classification, industry,
                created_by, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cid, s["tin"], s.get("rc_number"), s.get("primary_identifier", "TIN"),
            s["company_name"], s["company_name"].lower(), f"CUST-S{seq:03d}",
            s.get("email"), s.get("phone"), s.get("address"), s.get("city"),
            s.get("state"), s.get("state_code"), "NGA",
            s.get("customer_type", "B2B"), s.get("tax_classification", "STANDARD"),
            s.get("industry"), "usr-abbey-001",
            datetime.now(timezone.utc).isoformat()
        ))
        seq += 1

    for i, b in enumerate(borrowers, 1):
        cid = f"cust-abbey-b{i:02d}"
        conn.execute("""
            INSERT OR IGNORE INTO customers (
                customer_id, tin, primary_identifier,
                company_name, company_name_normalized, customer_code,
                email, phone, address, city, state, state_code,
                country, customer_type, tax_classification,
                created_by, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cid, b["tin"], "TIN",
            b["name"], b["name"].lower(), f"CUST-B{i:03d}",
            b.get("email"), b.get("phone"), b.get("address"), b.get("city"),
            b.get("state"), b.get("state_code"), "NGA",
            None, "STANDARD",
            "usr-abbey-004",
            datetime.now(timezone.utc).isoformat()
        ))

    for i, e in enumerate(enterprises, 1):
        cid = f"cust-abbey-e{i:02d}"
        conn.execute("""
            INSERT OR IGNORE INTO customers (
                customer_id, tin, rc_number, primary_identifier,
                company_name, company_name_normalized, trading_name, customer_code,
                email, phone, address, city, state, state_code,
                country, customer_type, tax_classification, industry,
                business_description, created_by, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            cid, e["tin"], e.get("rc_number"), e.get("primary_identifier", "TIN"),
            e["company_name"], e["company_name"].lower(), e.get("trading_name"),
            f"CUST-E{i:03d}",
            e.get("email"), e.get("phone"), e.get("address"), e.get("city"),
            e.get("state"), e.get("state_code"), "NGA",
            e.get("customer_type", "B2B"), e.get("tax_classification", "STANDARD"),
            e.get("industry"), e.get("business_description"),
            "usr-abbey-002",
            datetime.now(timezone.utc).isoformat()
        ))

    print(f"  Customers: {5 + len(borrowers) + len(enterprises)} seeded")


def seed_inventory(conn):
    """Seed 8 mortgage fee products."""
    fees = load_json("fee_catalog.json")

    for i, fee in enumerate(fees, 1):
        pid = f"prod-abbey-{i:03d}"
        conn.execute("""
            INSERT OR IGNORE INTO inventory (
                product_id, helium_sku, customer_sku,
                product_name, product_name_normalized, description,
                unit_of_measure, service_code, service_category,
                type, vat_treatment, vat_rate, is_tax_exempt,
                avg_unit_price, currency,
                created_by, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pid, f"HLM-ABB-{i:03d}", fee["sku"],
            fee["name"], fee["name"].upper(), fee.get("description", ""),
            fee.get("unit_of_measure", "unit"),
            fee.get("service_code"), fee.get("service_category"),
            fee.get("type", "SERVICE"),
            fee.get("vat_treatment", "STANDARD"),
            fee.get("vat_rate", 7.5),
            1 if fee.get("vat_treatment") == "EXEMPT" else 0,
            (fee["min_amount"] + fee["max_amount"]) / 2,
            "NGN",
            "usr-abbey-001",
            datetime.now(timezone.utc).isoformat()
        ))

    print(f"  Inventory: {len(fees)} products seeded")


def seed_invoices(conn):
    """Seed 15 invoices (10 outbound + 5 inbound)."""
    tenant_config = load_json("tenant_config.json")
    borrowers = load_json("borrowers.json")
    suppliers = load_json("suppliers.json")
    fees = load_json("fee_catalog.json")

    seller_name = tenant_config["company_name"]
    seller_tin = tenant_config["tin"]
    seller_addr = tenant_config["address"]
    seller_city = tenant_config["city"]
    service_id = tenant_config["firs_service_id"]

    invoice_count = 0

    # 10 outbound invoices
    import random
    random.seed(42)  # deterministic for reproducibility

    for i in range(1, 11):
        inv_id = _uuid_hex()
        inv_num = f"ABB-{i:07d}"
        issue_date = f"2026-04-{(i % 12) + 1:02d}"
        irn = _generate_irn(inv_num, service_id, issue_date)
        helium_no = f"PRO-ABBEY-{inv_id}"

        buyer = borrowers[(i - 1) % len(borrowers)]
        fee = fees[(i - 1) % len(fees)]
        amount = round(random.uniform(fee["min_amount"], fee["max_amount"]), 2)
        vat = round(amount * fee["vat_rate"] / 100, 2)
        total = round(amount + vat, 2)

        statuses = ["COMMITTED", "TRANSMITTED", "VALIDATED", "TRANSMITTED", "COMMITTED",
                     "TRANSMITTED", "TRANSMITTED", "VALIDATED", "COMMITTED", "QUEUED"]

        conn.execute("""
            INSERT OR IGNORE INTO invoices (
                invoice_id, helium_invoice_no, invoice_number, irn,
                csid, csid_status, direction, document_type, firs_invoice_type_code,
                transaction_type, issue_date, subtotal, tax_amount, total_amount,
                payment_means, workflow_status, transmission_status, payment_status,
                company_id, seller_name, seller_tin, seller_address, seller_city,
                buyer_id, buyer_name, buyer_tin, buyer_address, buyer_city,
                product_summary, line_items_count, source, source_id,
                schema_version_applied, sign_date, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            inv_id, helium_no, inv_num, irn,
            irn[:32].upper(), "ISSUED", "OUTBOUND", "COMMERCIAL_INVOICE", "380",
            "B2C", issue_date, amount, vat, total,
            "BANK_TRANSFER", statuses[i-1], "ACCEPTED" if statuses[i-1] in ("TRANSMITTED", "VALIDATED") else "NOT_REQUIRED",
            "UNPAID" if i % 3 != 0 else "PAID",
            "abbey", seller_name, seller_tin, seller_addr, seller_city,
            f"cust-abbey-b{((i-1) % 20) + 1:02d}", buyer["name"], buyer["tin"],
            buyer.get("address", ""), buyer.get("city", ""),
            fee["name"], 1, "Simulator", "simulator-abbey",
            "2.1.3.0", issue_date if statuses[i-1] != "COMMITTED" else None,
            datetime.now(timezone.utc).isoformat()
        ))

        # Line item
        line_id = f"line-{inv_id[:8]}-01"
        conn.execute("""
            INSERT OR IGNORE INTO invoice_line_items (
                line_id, invoice_id, line_number, line_item_type,
                description, quantity, unit_price, line_total,
                tax_rate, tax_amount, service_code, service_category,
                product_id, product_name, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            line_id, inv_id, 1, "SERVICE",
            fee["name"], 1.0, amount, amount,
            fee["vat_rate"] / 100, vat,
            fee.get("service_code"), fee.get("service_category"),
            f"prod-abbey-{((i-1) % 8) + 1:03d}", fee["name"],
            datetime.now(timezone.utc).isoformat()
        ))

        invoice_count += 1

    # 5 inbound invoices (from suppliers)
    inbound_data = [
        ("FCCB-56599",    "2026-01-31", suppliers[0], "Credit report for loan",    1459500.00, 109462.50),
        ("SLK-1811582",   "2026-02-14", suppliers[1], "Internet Subscription",      53023.26,   3976.74),
        ("CWA-3000052834","2026-02-06", suppliers[2], "Generator Repair",         2928241.84, 219618.14),
        ("IPNX-60016A",   "2025-12-06", suppliers[3], "Co-location Subscription", 6489000.00, 486675.00),
        ("AVON-HR-SL-01", "2026-02-24", suppliers[4], "Health Examination",        194000.00,      0.00),
    ]

    for j, (inv_num, issue_date, supplier, desc, subtotal, vat) in enumerate(inbound_data, 1):
        inv_id = _uuid_hex()
        irn = _generate_irn(inv_num, service_id, issue_date)
        helium_no = f"PRO-ABBEY-{inv_id}"
        total = round(subtotal + vat, 2)

        conn.execute("""
            INSERT OR IGNORE INTO invoices (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, firs_invoice_type_code, transaction_type,
                issue_date, subtotal, tax_amount, total_amount,
                payment_means, workflow_status, payment_status,
                company_id, seller_name, seller_tin, seller_address, seller_city,
                buyer_name, buyer_tin,
                product_summary, line_items_count, source, source_id,
                inbound_received_at, inbound_status,
                schema_version_applied, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            inv_id, helium_no, inv_num, irn,
            "INBOUND", "COMMERCIAL_INVOICE", "380", "B2B",
            issue_date, subtotal, vat, total,
            "BANK_TRANSFER", "COMMITTED",
            "PAID" if j in (2, 4) else "UNPAID",
            "abbey", supplier["company_name"], supplier["tin"],
            supplier.get("address", ""), supplier.get("city", ""),
            seller_name, seller_tin,
            desc, 1, "FIRS", "firs-delivery",
            f"{issue_date}T09:00:00Z",
            "ACCEPTED" if j != 3 else "PENDING_REVIEW",
            "2.1.3.0",
            datetime.now(timezone.utc).isoformat()
        ))

        line_id = f"line-{inv_id[:8]}-01"
        conn.execute("""
            INSERT OR IGNORE INTO invoice_line_items (
                line_id, invoice_id, line_number, line_item_type,
                description, quantity, unit_price, line_total,
                tax_rate, tax_amount, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            line_id, inv_id, 1, "SERVICE",
            desc, 1.0, subtotal, subtotal,
            0.075 if vat > 0 else 0.0, vat,
            datetime.now(timezone.utc).isoformat()
        ))

        invoice_count += 1

    print(f"  Invoices: {invoice_count} seeded (10 outbound + 5 inbound)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python seed_sync_db.py <path_to_sync.db>")
        print("Example: python seed_sync_db.py /path/to/Float/sync.db")
        sys.exit(1)

    db_path = sys.argv[1]

    if not os.path.exists(db_path):
        print(f"ERROR: sync.db not found at {db_path}")
        print("Make sure Float has been launched at least once to create the database.")
        sys.exit(1)

    print(f"Seeding sync.db at: {db_path}")
    conn = sqlite3.connect(db_path)

    try:
        seed_customers(conn)
        seed_inventory(conn)
        seed_invoices(conn)
        conn.commit()
        print("Done! Restart Float to see the data.")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
