"""
WS7: Transmission Report Generator — Excel

Content: All FIRS transmission attempts — dates, statuses, IRNs, error details.
Date filter: transmission_date
Format: WestMetro-compliant Excel (strict formatting spec)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

from src.reports.excel_formatter import (
    AlignmentCategory,
    ColumnConfig,
    add_sheet,
    create_workbook,
    format_sheet_complete,
    save_workbook,
)

logger = structlog.get_logger()

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Column definitions — strict WestMetro spec
COLUMNS = [
    ColumnConfig("S/No", "sno", width=7, alignment=AlignmentCategory.SEQUENCE, is_sno=True),
    ColumnConfig("Invoice ID", "invoice_id", width=21, alignment=AlignmentCategory.ID_SHORT),
    ColumnConfig("Invoice Number", "invoice_number", width=21, alignment=AlignmentCategory.ID_SHORT, number_format="@"),
    ColumnConfig("IRN", "irn", width=40, alignment=AlignmentCategory.ID_LONG),
    ColumnConfig("Direction", "direction", width=12, alignment=AlignmentCategory.TAG),
    ColumnConfig("Transmission Status", "transmission_status", width=21, alignment=AlignmentCategory.TAG),
    ColumnConfig("Submitted At", "transmission_date", width=17, alignment=AlignmentCategory.DATE),
    ColumnConfig("FIRS IRN", "firs_irn", width=40, alignment=AlignmentCategory.ID_LONG),
    ColumnConfig("Response Code", "firs_response_code", width=17, alignment=AlignmentCategory.TAG),
    ColumnConfig("Rejection Reason", "firs_rejection_reason", width=40, alignment=AlignmentCategory.DESCRIPTION),
    ColumnConfig("Total Amount \u20a6", "total_amount", width=23, alignment=AlignmentCategory.MONETARY, number_format="#,##0.00", is_monetary=True),
    ColumnConfig("Buyer Name", "buyer_name", width=40, alignment=AlignmentCategory.DESCRIPTION),
    ColumnConfig("Issue Date", "issue_date", width=17, alignment=AlignmentCategory.DATE),
]


async def generate(
    pool: AsyncConnectionPool,
    filters: dict[str, Any],
    company_id: str,
) -> tuple[bytes, str]:
    """Generate transmission report Excel. Returns (bytes, content_type)."""
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    status_filter = filters.get("status")

    # Build query
    where_parts = ["deleted_at IS NULL"]
    params: list[Any] = []

    if company_id and company_id != "default":
        where_parts.append("company_id = %s")
        params.append(company_id)

    if date_from:
        where_parts.append("transmission_date::DATE >= %s::DATE")
        params.append(str(date_from))

    if date_to:
        where_parts.append("transmission_date::DATE <= %s::DATE")
        params.append(str(date_to))

    if status_filter:
        placeholders = ", ".join(["%s"] * len(status_filter))
        where_parts.append(f"transmission_status IN ({placeholders})")
        params.extend(status_filter)

    where_clause = " AND ".join(where_parts)

    async with pool.connection() as conn:
        await conn.execute("SET search_path TO invoices")
        cur = await conn.execute(
            f"""
            SELECT
                invoice_id, invoice_number, irn, direction,
                transmission_status, transmission_date,
                firs_irn, firs_response_code, firs_rejection_reason,
                total_amount, buyer_name, issue_date
            FROM invoices
            WHERE {where_clause}
            ORDER BY transmission_date DESC NULLS LAST
            """,
            params,
        )
        db_rows = await cur.fetchall()
        col_names = [desc.name for desc in cur.description]

    # Build row dicts with S/No
    rows = []
    for idx, db_row in enumerate(db_rows, start=1):
        row_dict = dict(zip(col_names, db_row))
        row_dict["sno"] = idx
        # Format dates as DD-MM-YYYY text
        for date_key in ("transmission_date", "issue_date"):
            val = row_dict.get(date_key)
            if val is not None:
                if isinstance(val, (date,)):
                    row_dict[date_key] = val.strftime("%d-%m-%Y")
                else:
                    try:
                        row_dict[date_key] = str(val).split(" ")[0]
                    except Exception:
                        pass
        rows.append(row_dict)

    # Build workbook
    wb = create_workbook()
    ws = add_sheet(wb, "Transmission Report")
    format_sheet_complete(ws, COLUMNS, rows, schema_paths=None)

    content = save_workbook(wb)
    logger.info(
        "transmission_report_generated",
        company_id=company_id,
        row_count=len(rows),
        size=len(content),
    )
    return content, CONTENT_TYPE
