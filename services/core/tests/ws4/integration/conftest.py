"""
WS4 Integration Test Fixtures

Real PostgreSQL tests. Requires local PostgreSQL running.
Follows ws0 pattern: skip if PG unreachable.
"""

import asyncio
import os
import sys

import pytest
import pytest_asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from tests.ws4.integration.helpers import PG_DSN, TRUNCATE_SQL, MockSSEManager, _check_pg


# ── Schema + Table DDL ────────────────────────────────────────────────

SETUP_SQL = """
CREATE SCHEMA IF NOT EXISTS invoices;
CREATE SCHEMA IF NOT EXISTS customers;
CREATE SCHEMA IF NOT EXISTS inventory;

-- Drop all tables first (reverse dependency order) to ensure clean schema
DROP TABLE IF EXISTS invoices.invoice_edit_history CASCADE;
DROP TABLE IF EXISTS invoices.invoice_line_items CASCADE;
DROP TABLE IF EXISTS invoices.invoice_tax_categories CASCADE;
DROP TABLE IF EXISTS invoices.invoice_attachments CASCADE;
DROP TABLE IF EXISTS invoices.invoice_references CASCADE;
DROP TABLE IF EXISTS invoices.invoice_allowance_charges CASCADE;
DROP TABLE IF EXISTS invoices.invoices CASCADE;
DROP TABLE IF EXISTS customers.customer_edit_history CASCADE;
DROP TABLE IF EXISTS customers.customer_branches CASCADE;
DROP TABLE IF EXISTS customers.customer_contacts CASCADE;
DROP TABLE IF EXISTS customers.customers CASCADE;
DROP TABLE IF EXISTS inventory.inventory_edit_history CASCADE;
DROP TABLE IF EXISTS inventory.inventory_classification_candidates CASCADE;
DROP TABLE IF EXISTS inventory.inventory CASCADE;

CREATE TABLE IF NOT EXISTS invoices.invoices (
    invoice_id TEXT PRIMARY KEY, helium_invoice_no TEXT, invoice_number TEXT,
    irn TEXT, direction TEXT, document_type TEXT, transaction_type TEXT,
    issue_date TEXT, due_date TEXT, workflow_status TEXT, transmission_status TEXT,
    payment_status TEXT, seller_name TEXT, buyer_name TEXT,
    subtotal NUMERIC, tax_amount NUMERIC, total_amount NUMERIC,
    wht_amount NUMERIC DEFAULT 0, discount_amount NUMERIC DEFAULT 0,
    product_summary TEXT, line_items_count INTEGER DEFAULT 0,
    category TEXT, reference TEXT, attachment_count INTEGER DEFAULT 0,
    notes_to_firs TEXT, payment_terms_note TEXT, company_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ, deleted_by TEXT, updated_by TEXT,
    fts_vector TSVECTOR
);
CREATE INDEX IF NOT EXISTS idx_invoices_fts ON invoices.invoices USING GIN (fts_vector);

CREATE TABLE IF NOT EXISTS invoices.invoice_line_items (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    line_number INTEGER, description TEXT, quantity NUMERIC, unit_price NUMERIC, total NUMERIC
);
CREATE TABLE IF NOT EXISTS invoices.invoice_tax_categories (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    category_code TEXT, rate NUMERIC, taxable_amount NUMERIC, tax_amount NUMERIC
);
CREATE TABLE IF NOT EXISTS invoices.invoice_attachments (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    filename TEXT, content_type TEXT, file_size INTEGER
);
CREATE TABLE IF NOT EXISTS invoices.invoice_references (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    reference_type TEXT, reference_value TEXT
);
CREATE TABLE IF NOT EXISTS invoices.invoice_allowance_charges (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    charge_type TEXT, amount NUMERIC, reason TEXT
);
CREATE TABLE IF NOT EXISTS invoices.invoice_edit_history (
    id SERIAL PRIMARY KEY,
    invoice_id TEXT REFERENCES invoices.invoices(invoice_id) ON DELETE CASCADE,
    field_name TEXT, old_value TEXT, new_value TEXT,
    changed_by TEXT, changed_at TIMESTAMPTZ, change_reason TEXT
);

CREATE TABLE IF NOT EXISTS customers.customers (
    customer_id TEXT PRIMARY KEY, company_name TEXT, company_name_normalized TEXT,
    customer_code TEXT, tin TEXT, rc_number TEXT, trading_name TEXT, short_code TEXT,
    customer_type TEXT, tax_classification TEXT,
    is_mbs_registered BOOLEAN DEFAULT FALSE, is_fze BOOLEAN DEFAULT FALSE,
    state TEXT, city TEXT, compliance_score INTEGER DEFAULT 0,
    total_invoices INTEGER DEFAULT 0, last_active_date TIMESTAMPTZ,
    total_lifetime_value NUMERIC DEFAULT 0,
    address TEXT, postal_code TEXT, lga TEXT, lga_code TEXT, state_code TEXT,
    email TEXT, phone TEXT, website TEXT, business_description TEXT,
    tax_id TEXT, primary_identifier TEXT, industry TEXT,
    business_unit TEXT, default_due_date_days INTEGER,
    company_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ, updated_by TEXT,
    fts_vector TSVECTOR
);
CREATE INDEX IF NOT EXISTS idx_customers_fts ON customers.customers USING GIN (fts_vector);

CREATE TABLE IF NOT EXISTS customers.customer_branches (
    branch_id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers.customers(customer_id) ON DELETE CASCADE,
    branch_name TEXT, address TEXT, city TEXT, state TEXT
);
CREATE TABLE IF NOT EXISTS customers.customer_contacts (
    id SERIAL PRIMARY KEY,
    customer_id TEXT REFERENCES customers.customers(customer_id) ON DELETE CASCADE,
    contact_name TEXT, email TEXT, phone TEXT, role TEXT
);
CREATE TABLE IF NOT EXISTS customers.customer_edit_history (
    id SERIAL PRIMARY KEY,
    customer_id TEXT REFERENCES customers.customers(customer_id) ON DELETE CASCADE,
    field_name TEXT, old_value TEXT, new_value TEXT,
    changed_by TEXT, changed_at TIMESTAMPTZ, change_reason TEXT
);

CREATE TABLE IF NOT EXISTS inventory.inventory (
    product_id TEXT PRIMARY KEY, product_name TEXT, product_name_normalized TEXT,
    helium_sku TEXT, hsn_code TEXT, service_code TEXT,
    type TEXT, vat_treatment TEXT, vat_rate NUMERIC,
    product_category TEXT, service_category TEXT,
    avg_unit_price NUMERIC, currency TEXT DEFAULT 'NGN',
    description TEXT, unit_of_measure TEXT, customer_sku TEXT, oem_sku TEXT,
    is_tax_exempt BOOLEAN DEFAULT FALSE, classification_source TEXT,
    company_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ, updated_by TEXT,
    fts_vector TSVECTOR
);
CREATE INDEX IF NOT EXISTS idx_inventory_fts ON inventory.inventory USING GIN (fts_vector);

CREATE TABLE IF NOT EXISTS inventory.inventory_classification_candidates (
    id SERIAL PRIMARY KEY,
    product_id TEXT REFERENCES inventory.inventory(product_id) ON DELETE CASCADE,
    rank INTEGER, hsn_code TEXT, description TEXT, confidence NUMERIC
);
CREATE TABLE IF NOT EXISTS inventory.inventory_edit_history (
    id SERIAL PRIMARY KEY,
    product_id TEXT REFERENCES inventory.inventory(product_id) ON DELETE CASCADE,
    field_name TEXT, old_value TEXT, new_value TEXT,
    changed_by TEXT, changed_at TIMESTAMPTZ, change_reason TEXT
);
"""


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _ensure_schemas():
    """Create schemas + tables once per test session."""
    if not _check_pg():
        pytest.skip("PostgreSQL not available")

    import psycopg
    with psycopg.connect(PG_DSN) as conn:
        conn.autocommit = True
        conn.execute(SETUP_SQL)


@pytest_asyncio.fixture
async def pg_pool(_ensure_schemas):
    """Per-test async connection pool."""
    if not _check_pg():
        pytest.skip("PostgreSQL not available")

    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(conninfo=PG_DSN, min_size=1, max_size=5, open=False)
    await pool.open()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def pg_conn(pg_pool):
    """Per-test connection with truncation for isolation."""
    async with pg_pool.connection() as conn:
        await conn.execute(TRUNCATE_SQL)
        yield conn


@pytest.fixture
def mock_sse_manager():
    return MockSSEManager()
