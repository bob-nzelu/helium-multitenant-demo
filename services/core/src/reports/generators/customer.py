"""
WS7: Customer Report Generator — Excel

Content: Customer master data export — names, TINs, compliance scores,
invoice counts, lifetime values.

Date filter: customer created_at for rows, invoice issue_date for metrics.
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

COLUMNS = [
    ColumnConfig("S/No", "sno", width=7, alignment=AlignmentCategory.SEQUENCE, is_sno=True),
    ColumnConfig("Customer ID", "customer_id", width=21, alignment=AlignmentCategory.ID_LONG),
    ColumnConfig("Company Name", "company_name", width=58, alignment=AlignmentCategory.DESCRIPTION),
    ColumnConfig("TIN", "tin", width=21, alignment=AlignmentCategory.ID_SHORT, number_format="@"),
    ColumnConfig("Customer Type", "customer_type", width=17, alignment=AlignmentCategory.TAG),
    ColumnConfig("Status", "status", width=12, alignment=AlignmentCategory.TAG),
    ColumnConfig("Compliance Score", "compliance_score", width=17, alignment=AlignmentCategory.COUNT),
    ColumnConfig("Total Invoices", "total_invoices", width=17, alignment=AlignmentCategory.COUNT),
    ColumnConfig("Lifetime Value \u20a6", "total_lifetime_value", width=23, alignment=AlignmentCategory.MONETARY, number_format="#,##0.00", is_monetary=True),
    ColumnConfig("Lifetime Tax \u20a6", "total_lifetime_tax", width=21, alignment=AlignmentCategory.MONETARY, number_format="#,##0.00", is_monetary=True),
    ColumnConfig("Last Invoice Date", "last_invoice_date", width=17, alignment=AlignmentCategory.DATE),
    ColumnConfig("Last Active Date", "last_active_date", width=17, alignment=AlignmentCategory.DATE),
    ColumnConfig("Created", "created_at", width=17, alignment=AlignmentCategory.DATE),
]


async def generate(
    pool: AsyncConnectionPool,
    filters: dict[str, Any],
    company_id: str,
) -> tuple[bytes, str]:
    """Generate customer report Excel. Returns (bytes, content_type)."""
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    customer_id = filters.get("customer_id")

    where_parts = ["deleted_at IS NULL"]
    params: list[Any] = []

    if company_id and company_id != "default":
        where_parts.append("company_id = %s")
        params.append(company_id)

    if date_from:
        where_parts.append("created_at::DATE >= %s::DATE")
        params.append(str(date_from))

    if date_to:
        where_parts.append("created_at::DATE <= %s::DATE")
        params.append(str(date_to))

    if customer_id:
        where_parts.append("customer_id = %s")
        params.append(customer_id)

    where_clause = " AND ".join(where_parts)

    async with pool.connection() as conn:
        await conn.execute("SET search_path TO customers")
        cur = await conn.execute(
            f"""
            SELECT
                customer_id, company_name, tin, customer_type, status,
                compliance_score, total_invoices,
                total_lifetime_value, total_lifetime_tax,
                last_invoice_date, last_active_date, created_at
            FROM customers
            WHERE {where_clause}
            ORDER BY total_lifetime_value DESC NULLS LAST
            """,
            params,
        )
        db_rows = await cur.fetchall()
        col_names = [desc.name for desc in cur.description]

    rows = []
    for idx, db_row in enumerate(db_rows, start=1):
        row_dict = dict(zip(col_names, db_row))
        row_dict["sno"] = idx
        # Format dates
        for date_key in ("last_invoice_date", "last_active_date", "created_at"):
            val = row_dict.get(date_key)
            if val is not None:
                if isinstance(val, (date,)):
                    row_dict[date_key] = val.strftime("%d-%m-%Y")
                elif hasattr(val, "strftime"):
                    row_dict[date_key] = val.strftime("%d-%m-%Y")
                else:
                    row_dict[date_key] = str(val).split(" ")[0]
        rows.append(row_dict)

    wb = create_workbook()
    ws = add_sheet(wb, "Customer Report")
    format_sheet_complete(ws, COLUMNS, rows, schema_paths=None)

    content = save_workbook(wb)
    logger.info(
        "customer_report_generated",
        company_id=company_id,
        row_count=len(rows),
        size=len(content),
    )
    return content, CONTENT_TYPE
