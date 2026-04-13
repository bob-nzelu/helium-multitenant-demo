"""
Invoice Data Repository (WS4)

Per Q6 APPROVED: Fully qualified table names (invoices.invoices).
Per Q2 APPROVED: Direct psycopg3 pool (no wrapper).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg import AsyncConnection

from src.models.entities import INVOICE_LIST_FIELDS

logger = structlog.get_logger()


TABLE = "invoices.invoices"
LINE_ITEMS_TABLE = "invoices.invoice_line_items"
TAX_CATEGORIES_TABLE = "invoices.invoice_tax_categories"
ATTACHMENTS_TABLE = "invoices.invoice_attachments"
REFERENCES_TABLE = "invoices.invoice_references"
ALLOWANCE_CHARGES_TABLE = "invoices.invoice_allowance_charges"
EDIT_HISTORY_TABLE = "invoices.invoice_edit_history"


async def get_by_id(conn: AsyncConnection, invoice_id: str) -> dict[str, Any] | None:
    """Fetch a single invoice with all child collections."""
    cur = await conn.execute(
        f"SELECT * FROM {TABLE} WHERE invoice_id = %s",
        (invoice_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None

    cols = [desc.name for desc in cur.description]
    invoice = dict(zip(cols, row))

    invoice["line_items"] = await _fetch_children(
        conn, LINE_ITEMS_TABLE, "invoice_id", invoice_id, "line_number",
    )

    invoice["tax_categories"] = await _fetch_children(
        conn, TAX_CATEGORIES_TABLE, "invoice_id", invoice_id, "id",
    )

    invoice["attachments"] = await _fetch_children(
        conn, ATTACHMENTS_TABLE, "invoice_id", invoice_id, "id",
    )

    invoice["references"] = await _fetch_children(
        conn, REFERENCES_TABLE, "invoice_id", invoice_id, "id",
    )

    invoice["allowance_charges"] = await _fetch_children(
        conn, ALLOWANCE_CHARGES_TABLE, "invoice_id", invoice_id, "id",
    )

    return invoice


async def list_paginated(
    conn: AsyncConnection,
    *,
    page: int = 1,
    per_page: int = 50,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    status: list[str] | None = None,
    direction: str | None = None,
    document_type: str | None = None,
    transaction_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch paginated invoice list items with filters."""
    fields = ", ".join(INVOICE_LIST_FIELDS)
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if status:
        placeholders = ", ".join(["%s"] * len(status))
        where_clauses.append(f"workflow_status IN ({placeholders})")
        params.extend(status)

    if direction:
        where_clauses.append("direction = %s")
        params.append(direction)

    if document_type:
        where_clauses.append("document_type = %s")
        params.append(document_type)

    if transaction_type:
        where_clauses.append("transaction_type = %s")
        params.append(transaction_type)

    if date_from:
        where_clauses.append("created_at >= %s")
        params.append(date_from)

    if date_to:
        where_clauses.append("created_at <= %s")
        params.append(date_to)

    fts_rank = ""
    if search:
        where_clauses.append("fts_vector @@ websearch_to_tsquery('english', %s)")
        params.append(search)
        fts_rank = ", ts_rank(fts_vector, websearch_to_tsquery('english', %s)) AS relevance"
        params.append(search)

    where = " AND ".join(where_clauses)

    sort_order = "ASC" if sort_order.lower() == "asc" else "DESC"
    order = f"relevance DESC, {sort_by} {sort_order}" if search else f"{sort_by} {sort_order}"

    offset = (page - 1) * per_page
    params.extend([per_page, offset])

    sql = f"""
        SELECT {fields}{fts_rank}
        FROM {TABLE}
        WHERE {where}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """

    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    cols = [desc.name for desc in cur.description]
    return [dict(zip(cols, row)) for row in rows]


async def get_count(
    conn: AsyncConnection,
    *,
    status: list[str] | None = None,
    direction: str | None = None,
    document_type: str | None = None,
    transaction_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
) -> int:
    """Count invoice records matching filters."""
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if status:
        placeholders = ", ".join(["%s"] * len(status))
        where_clauses.append(f"workflow_status IN ({placeholders})")
        params.extend(status)

    if direction:
        where_clauses.append("direction = %s")
        params.append(direction)

    if document_type:
        where_clauses.append("document_type = %s")
        params.append(document_type)

    if transaction_type:
        where_clauses.append("transaction_type = %s")
        params.append(transaction_type)

    if date_from:
        where_clauses.append("created_at >= %s")
        params.append(date_from)

    if date_to:
        where_clauses.append("created_at <= %s")
        params.append(date_to)

    if search:
        where_clauses.append("fts_vector @@ websearch_to_tsquery('english', %s)")
        params.append(search)

    where = " AND ".join(where_clauses)
    sql = f"SELECT COUNT(*) FROM {TABLE} WHERE {where}"

    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    return row[0] if row else 0


async def update_fields(
    conn: AsyncConnection,
    invoice_id: str,
    fields: dict[str, Any],
    updated_by: str,
) -> dict[str, Any] | None:
    """Update specified fields on an invoice record."""
    set_parts: list = []
    params: list = []

    for field_name, value in fields.items():
        set_parts.append(f"{field_name} = %s")
        params.append(value)

    set_parts.append("updated_by = %s")
    params.append(updated_by)
    set_parts.append("updated_at = %s")
    params.append(datetime.now(timezone.utc).isoformat())

    params.append(invoice_id)

    sql = f"""
        UPDATE {TABLE}
        SET {', '.join(set_parts)}
        WHERE invoice_id = %s
        RETURNING *
    """

    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def soft_delete(
    conn: AsyncConnection,
    invoice_id: str,
    deleted_by: str,
) -> dict[str, Any] | None:
    """Soft delete an invoice by setting deleted_at and deleted_by."""
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = %s, deleted_by = %s
        WHERE invoice_id = %s AND deleted_at IS NULL
        RETURNING invoice_id, deleted_at
        """,
        (now, deleted_by, invoice_id),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def recover(conn: AsyncConnection, invoice_id: str) -> dict[str, Any] | None:
    """Recover a soft-deleted invoice."""
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = NULL, deleted_by = NULL
        WHERE invoice_id = %s
        RETURNING *
        """,
        (invoice_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def _fetch_children(
    conn: AsyncConnection,
    table: str,
    fk_column: str,
    fk_value: str,
    order_column: str,
) -> list[dict[str, Any]]:
    """Fetch child rows from a related table."""
    cur = await conn.execute(
        f"SELECT * FROM {table} WHERE {fk_column} = %s ORDER BY {order_column}",
        (fk_value,),
    )

    rows = await cur.fetchall()
    if not rows:
        return []
    cols = [desc.name for desc in cur.description]
    return [dict(zip(cols, row)) for row in rows]
