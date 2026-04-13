"""
Shared test helpers for WS4 integration tests.

Provides: needs_pg marker, data insertion helpers, MockSSEManager, TRUNCATE_SQL.
"""

import asyncio
import os
import sys

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ── PostgreSQL connection ──────────────────────────────────────────────

PG_DSN = (
    f"host={os.environ.get('CORE_DB_HOST', 'localhost')} "
    f"port={os.environ.get('CORE_DB_PORT', '5432')} "
    f"dbname={os.environ.get('CORE_DB_NAME', 'helium_core')} "
    f"user={os.environ.get('CORE_DB_USER', 'postgres')} "
    f"password={os.environ.get('CORE_DB_PASSWORD', 'Technology100')}"
)

_PG_AVAILABLE = None


def _check_pg() -> bool:
    global _PG_AVAILABLE
    if _PG_AVAILABLE is not None:
        return _PG_AVAILABLE
    try:
        import psycopg
        with psycopg.connect(PG_DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        _PG_AVAILABLE = True
    except Exception:
        _PG_AVAILABLE = False
    return _PG_AVAILABLE


needs_pg = pytest.mark.skipif(
    not _check_pg(),
    reason="PostgreSQL not available",
)


TRUNCATE_SQL = """
TRUNCATE invoices.invoice_edit_history CASCADE;
TRUNCATE invoices.invoice_line_items CASCADE;
TRUNCATE invoices.invoice_tax_categories CASCADE;
TRUNCATE invoices.invoice_attachments CASCADE;
TRUNCATE invoices.invoice_references CASCADE;
TRUNCATE invoices.invoice_allowance_charges CASCADE;
TRUNCATE invoices.invoices CASCADE;
TRUNCATE customers.customer_edit_history CASCADE;
TRUNCATE customers.customer_branches CASCADE;
TRUNCATE customers.customer_contacts CASCADE;
TRUNCATE customers.customers CASCADE;
TRUNCATE inventory.inventory_edit_history CASCADE;
TRUNCATE inventory.inventory_classification_candidates CASCADE;
TRUNCATE inventory.inventory CASCADE;
"""


# ── Data Insertion Helpers ─────────────────────────────────────────────


async def insert_invoice(conn, data: dict) -> dict:
    cols = list(data.keys())
    vals = list(data.values())

    fts_text = " ".join(
        str(v) for k, v in data.items()
        if k in ("invoice_number", "buyer_name", "seller_name", "irn") and v
    )
    cols.append("fts_vector")
    vals.append(fts_text)

    col_names = ", ".join(cols[:-1]) + ", fts_vector"
    placeholders = ", ".join(["%s"] * (len(cols) - 1)) + ", to_tsvector('english', %s)"

    await conn.execute(
        f"INSERT INTO invoices.invoices ({col_names}) VALUES ({placeholders})",
        vals,
    )
    return data


async def insert_invoice_line_item(conn, invoice_id: str, line_number: int, **kwargs) -> None:
    defaults = {
        "invoice_id": invoice_id,
        "line_number": line_number,
        "description": f"Line item {line_number}",
        "quantity": 1.0,
        "unit_price": 100.0,
        "total": 100.0,
    }
    defaults.update(kwargs)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    await conn.execute(
        f"INSERT INTO invoices.invoice_line_items ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        vals,
    )


async def insert_customer(conn, data: dict) -> dict:
    fts_text = " ".join(
        str(v) for k, v in data.items()
        if k in ("company_name", "tin", "customer_code", "trading_name") and v
    )
    cols = list(data.keys()) + ["fts_vector"]
    vals = list(data.values()) + [fts_text]

    col_names = ", ".join(cols[:-1]) + ", fts_vector"
    placeholders = ", ".join(["%s"] * (len(cols) - 1)) + ", to_tsvector('english', %s)"

    await conn.execute(
        f"INSERT INTO customers.customers ({col_names}) VALUES ({placeholders})",
        vals,
    )
    return data


async def insert_customer_branch(conn, customer_id: str, branch_id: str, **kwargs) -> None:
    defaults = {
        "branch_id": branch_id,
        "customer_id": customer_id,
        "branch_name": "Main Branch",
        "address": "123 Test St",
        "city": "Lagos",
        "state": "Lagos",
    }
    defaults.update(kwargs)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    await conn.execute(
        f"INSERT INTO customers.customer_branches ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        vals,
    )


async def insert_customer_contact(conn, customer_id: str, **kwargs) -> None:
    defaults = {
        "customer_id": customer_id,
        "contact_name": "John Doe",
        "email": "john@test.com",
        "phone": "+234-555-0001",
        "role": "Manager",
    }
    defaults.update(kwargs)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    await conn.execute(
        f"INSERT INTO customers.customer_contacts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        vals,
    )


async def insert_inventory(conn, data: dict) -> dict:
    fts_text = " ".join(
        str(v) for k, v in data.items()
        if k in ("product_name", "hsn_code", "helium_sku", "description") and v
    )
    cols = list(data.keys()) + ["fts_vector"]
    vals = list(data.values()) + [fts_text]

    col_names = ", ".join(cols[:-1]) + ", fts_vector"
    placeholders = ", ".join(["%s"] * (len(cols) - 1)) + ", to_tsvector('english', %s)"

    await conn.execute(
        f"INSERT INTO inventory.inventory ({col_names}) VALUES ({placeholders})",
        vals,
    )
    return data


async def insert_classification_candidate(conn, product_id: str, rank: int, **kwargs) -> None:
    defaults = {
        "product_id": product_id,
        "rank": rank,
        "hsn_code": "4802.55",
        "description": f"Candidate {rank}",
        "confidence": 0.95,
    }
    defaults.update(kwargs)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    await conn.execute(
        f"INSERT INTO inventory.inventory_classification_candidates ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        vals,
    )


class MockSSEManager:
    def __init__(self):
        self.events: list = []

    async def publish(self, event):
        self.events.append(event)
