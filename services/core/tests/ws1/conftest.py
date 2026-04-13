"""Shared fixtures for WS1 tests."""

from __future__ import annotations

import gzip
import io
import json

import pytest


@pytest.fixture
def sample_xlsx_bytes() -> bytes:
    """Minimal valid .xlsx file."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(["Invoice No", "Amount", "Customer"])
    ws.append(["INV-001", 1500.00, "Acme Corp"])
    ws.append(["INV-002", 2300.50, "Beta Ltd"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_csv_bytes() -> bytes:
    return b"invoice_number,amount,customer\nINV-001,1500.00,Acme Corp\nINV-002,2300.50,Beta Ltd\n"


@pytest.fixture
def sample_csv_semicolon() -> bytes:
    return b"invoice_number;amount;customer\nINV-001;1500.00;Acme Corp\nINV-002;2300.50;Beta Ltd\n"


@pytest.fixture
def sample_csv_with_hdx() -> bytes:
    return b"#date,source\n#2026-03-01,WFP\ninvoice_number,amount\nINV-001,1500\n"


@pytest.fixture
def sample_json_bytes() -> bytes:
    return json.dumps([
        {"invoice_number": "INV-001", "amount": 1500},
        {"invoice_number": "INV-002", "amount": 2300},
    ]).encode()


@pytest.fixture
def sample_json_single() -> bytes:
    return json.dumps({"invoice_number": "INV-001", "amount": 1500}).encode()


@pytest.fixture
def sample_xml_bytes() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice>
    <InvoiceNumber>INV-001</InvoiceNumber>
    <Amount>1500.00</Amount>
    <Customer>
        <Name>Acme Corp</Name>
        <TIN>12345678-001</TIN>
    </Customer>
</Invoice>"""


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake pdf content for testing"


@pytest.fixture
def sample_hlm_bytes() -> bytes:
    return json.dumps({
        "hlm_version": "1.0",
        "data_class": "invoice",
        "invoices": [{"invoice_number": "INV-001"}],
    }).encode()


@pytest.fixture
def sample_hlmz_bytes(sample_hlm_bytes: bytes) -> bytes:
    return gzip.compress(sample_hlm_bytes)
