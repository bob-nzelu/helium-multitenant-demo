"""
Mock HLX Generator — Creates a real tar.gz with the correct internal structure.

Produces:
  {data_uuid}.hlx (tar.gz)
  ├── manifest.json
  ├── report.json
  ├── metadata.json
  └── sheets/
      ├── submission.hlm
      ├── duplicate.hlm
      ├── failed.hlm
      ├── customers.hlm
      └── inventory.hlm
"""

import io
import json
import logging
import random
import tarfile
import uuid
from datetime import datetime, timezone
from typing import Tuple

logger = logging.getLogger("core.mock.hlx")

# Sample data pools for generating realistic invoice rows
BORROWER_NAMES = [
    "Adewale Ogundimu", "Ngozi Eze", "Olumide Fashola", "Fatima Abdullahi",
    "Chukwuemeka Nwankwo", "Aisha Mohammed", "Tunde Bakare", "Nneka Obi",
    "Ibrahim Suleiman", "Yetunde Coker", "Obinna Agu", "Halima Bello",
]

FEE_NAMES = [
    "Mortgage Origination Fee", "Property Valuation Fee", "Legal & Documentation Fee",
    "Credit Life Insurance Premium", "Property Insurance Premium",
    "Account Maintenance Fee", "Early Repayment Penalty", "Statement & Certificate Fee",
]

BRANCHES = ["Victoria Island", "Ikeja", "Lekki", "Surulere", "Abuja - Wuse"]


def _gen_invoice_row(seq: int) -> dict:
    fee = random.choice(FEE_NAMES)
    buyer = random.choice(BORROWER_NAMES)
    is_exempt = "Insurance" in fee
    amount = round(random.uniform(5000, 500000), 2)
    vat = 0.0 if is_exempt else round(amount * 0.075, 2)
    return {
        "invoice_number": f"ABB-{seq:07d}",
        "buyer_name": buyer,
        "description": fee,
        "issue_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "subtotal": amount,
        "vat_amount": vat,
        "total_amount": round(amount + vat, 2),
        "branch": random.choice(BRANCHES),
        "direction": "OUTBOUND",
        "document_type": "COMMERCIAL_INVOICE",
        "transaction_type": "B2C",
        "payment_means": "BANK_TRANSFER",
        "seller_name": "Abbey Mortgage Bank PLC",
        "seller_tin": "02345678-0001",
    }


def _gen_failed_row(seq: int, error: str) -> dict:
    row = _gen_invoice_row(seq)
    row["__ERROR__"] = error
    row["__STREAM__"] = "validation"
    return row


def _gen_duplicate_row(seq: int) -> dict:
    row = _gen_invoice_row(seq)
    row["__DUPLICATE_OF__"] = f"ABB-{seq - 1:07d}"
    return row


def _gen_customer_row(name: str, is_new: bool) -> dict:
    return {
        "company_name": name,
        "customer_type": "B2C",
        "tin": f"001{random.randint(10000, 99999):05d}-0001",
        "city": random.choice(["Lagos", "Abuja", "Ikeja"]),
        "state": random.choice(["Lagos", "FCT"]),
        "__IS_NEW__": is_new,
    }


def _gen_inventory_row(fee: str) -> dict:
    return {
        "product_name": fee,
        "type": "SERVICE",
        "vat_treatment": "EXEMPT" if "Insurance" in fee else "STANDARD",
        "vat_rate": 0.0 if "Insurance" in fee else 7.5,
        "__IS_NEW__": random.choice([True, False]),
    }


def generate_mock_hlx(
    data_uuid: str,
    company_id: str,
    total_invoices: int,
    valid_count: int,
    failed_count: int,
    duplicate_count: int,
    filename: str = "upload.xlsx",
) -> Tuple[bytes, str]:
    """
    Generate a mock .hlx tar.gz archive.

    Returns:
        (hlx_bytes, hlx_blob_uuid)
    """
    hlx_blob_uuid = f"hlx-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    seq_base = random.randint(1000, 9000)

    # Generate sheet data
    submission_rows = [_gen_invoice_row(seq_base + i) for i in range(valid_count)]
    failed_rows = [
        _gen_failed_row(seq_base + valid_count + i, random.choice([
            "Missing required field: buyer_tin",
            "Negative amount not allowed",
            "Invalid date format",
        ]))
        for i in range(failed_count)
    ]
    duplicate_rows = [_gen_duplicate_row(seq_base + valid_count + failed_count + i) for i in range(duplicate_count)]

    # Customer and inventory sheets
    unique_buyers = list(set(r["buyer_name"] for r in submission_rows))
    customer_rows = [_gen_customer_row(name, i < 3) for i, name in enumerate(unique_buyers)]
    unique_fees = list(set(r["description"] for r in submission_rows))
    inventory_rows = [_gen_inventory_row(fee) for fee in unique_fees]

    # Build manifest
    manifest = {
        "hlx_version": "1.0",
        "data_uuid": data_uuid,
        "queue_id": f"q-{uuid.uuid4().hex[:12]}",
        "company_id": company_id,
        "generated_at": now.isoformat(),
        "generated_by": "core-mock",
        "schema_version": "2.1.3.0",
        "bundle_integrity": {
            "source_data_uuid": data_uuid,
            "source_type": "bulk_upload",
            "all_sheets_same_source": True,
        },
        "sheets": [
            {"id": "submission", "filename": "sheets/submission.hlm", "display_name": "Invoices for Submission", "category": "output", "interaction_tier": "primary", "row_count": valid_count, "column_count": 14, "icon": "check_circle", "sort_order": 1, "description": "Valid invoices ready for FIRS submission"},
            {"id": "duplicate", "filename": "sheets/duplicate.hlm", "display_name": "Duplicate Invoices", "category": "output", "interaction_tier": "informational", "row_count": duplicate_count, "column_count": 15, "icon": "content_copy", "sort_order": 2, "description": "Invoices with matching invoice number"},
            {"id": "failed", "filename": "sheets/failed.hlm", "display_name": "Failed Invoices", "category": "failed", "interaction_tier": "actionable", "row_count": failed_count, "column_count": 16, "icon": "error", "sort_order": 7, "description": "Invoices that failed validation"},
            {"id": "customers", "filename": "sheets/customers.hlm", "display_name": "Extracted Customers", "category": "entity", "interaction_tier": "informational", "row_count": len(customer_rows), "column_count": 6, "icon": "people", "sort_order": 8, "description": "Customers detected in this upload"},
            {"id": "inventory", "filename": "sheets/inventory.hlm", "display_name": "Extracted Products", "category": "entity", "interaction_tier": "informational", "row_count": len(inventory_rows), "column_count": 5, "icon": "inventory_2", "sort_order": 9, "description": "Products/services detected in this upload"},
        ],
        "report": {"filename": "report.json", "has_summary_cards": True, "has_red_flags": True, "has_compliance_score": True},
        "metadata": {"filename": "metadata.json"},
        "statistics": {
            "total_invoices": total_invoices,
            "valid_count": valid_count,
            "failed_count": failed_count,
            "duplicate_count": duplicate_count,
            "processing_time_ms": 9000,
            "overall_confidence": 0.94,
        },
    }

    # Build report
    report = {
        "summary": {
            "total_invoices": total_invoices,
            "valid_for_submission": valid_count,
            "failed_validation": failed_count,
            "duplicates_detected": duplicate_count,
            "total_value": round(sum(r["total_amount"] for r in submission_rows), 2),
            "total_vat": round(sum(r["vat_amount"] for r in submission_rows), 2),
        },
        "red_flags": [],
        "compliance_score": 94 if failed_count == 0 else 78,
        "generated_at": now.isoformat(),
    }

    # Build metadata
    metadata = {
        "data_uuid": data_uuid,
        "company_id": company_id,
        "source_filename": filename,
        "processing_started_at": (now - __import__("datetime").timedelta(seconds=10)).isoformat(),
        "processing_completed_at": now.isoformat(),
        "schema_version": "2.1.3.0",
        "core_version": "0.1.0-mock",
        "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
    }

    # Pack into tar.gz
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in [
            ("manifest.json", manifest),
            ("report.json", report),
            ("metadata.json", metadata),
            ("sheets/submission.hlm", submission_rows),
            ("sheets/duplicate.hlm", duplicate_rows),
            ("sheets/failed.hlm", failed_rows),
            ("sheets/customers.hlm", customer_rows),
            ("sheets/inventory.hlm", inventory_rows),
        ]:
            content = json.dumps(data, indent=2).encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

    hlx_bytes = buf.getvalue()
    logger.info(f"HLX generated: {hlx_blob_uuid} ({len(hlx_bytes)} bytes, {total_invoices} invoices)")

    return hlx_bytes, hlx_blob_uuid
