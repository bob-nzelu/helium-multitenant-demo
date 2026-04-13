"""
Customer Data Repository (WS4)

Per Q6: Fully qualified table names (customers.customers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg import AsyncConnection

from src.models.entities import CUSTOMER_LIST_FIELDS

logger = structlog.get_logger()

TABLE = "customers.customers"
BRANCHES_TABLE = "customers.customer_branches"
CONTACTS_TABLE = "customers.customer_contacts"
EDIT_HISTORY_TABLE = "customers.customer_edit_history"


async def get_by_id(conn: AsyncConnection, customer_id: str) -> dict[str, Any] | None:
    """Fetch a single customer with branches and contacts."""
    cur = await conn.execute(
        f"SELECT * FROM {TABLE} WHERE customer_id = %s",
        (customer_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None

    cols = [desc.name for desc in cur.description]
    customer = dict(zip(cols, row))

    customer["branches"] = await _fetch_children(
        conn, BRANCHES_TABLE, "customer_id", customer_id, "branch_id",
    )

    customer["contacts"] = await _fetch_children(
        conn, CONTACTS_TABLE, "customer_id", customer_id, "id",
    )

    return customer


async def list_paginated(
    conn: AsyncConnection,
    *,
    page: int = 1,
    per_page: int = 50,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    customer_type: str | None = None,
    state: str | None = None,
    compliance_min: int | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch paginated customer list items with filters."""
    fields = ", ".join(CUSTOMER_LIST_FIELDS)
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if customer_type:
        where_clauses.append("customer_type = %s")
        params.append(customer_type)

    if state:
        where_clauses.append("state = %s")
        params.append(state)

    if compliance_min is not None:
        where_clauses.append("compliance_score >= %s")
        params.append(compliance_min)

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
    customer_type: str | None = None,
    state: str | None = None,
    compliance_min: int | None = None,
    search: str | None = None,
) -> int:
    """Count customer records matching filters."""
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if customer_type:
        where_clauses.append("customer_type = %s")
        params.append(customer_type)

    if state:
        where_clauses.append("state = %s")
        params.append(state)

    if compliance_min is not None:
        where_clauses.append("compliance_score >= %s")
        params.append(compliance_min)

    if search:
        where_clauses.append("fts_vector @@ websearch_to_tsquery('english', %s)")
        params.append(search)

    where = " AND ".join(where_clauses)
    cur = await conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {where}", params)
    row = await cur.fetchone()
    return row[0] if row else 0


async def update_fields(
    conn: AsyncConnection,
    customer_id: str,
    fields: dict[str, Any],
    updated_by: str,
) -> dict[str, Any] | None:
    """Update specified fields on a customer record."""
    set_parts: list = []
    params: list = []

    for field_name, value in fields.items():
        set_parts.append(f"{field_name} = %s")
        params.append(value)

    if "company_name" in fields and fields["company_name"] is not None:
        normalized = _normalize_name(fields["company_name"])
        set_parts.append("company_name_normalized = %s")
        params.append(normalized)

    set_parts.append("updated_by = %s")
    params.append(updated_by)
    set_parts.append("updated_at = %s")
    params.append(datetime.now(timezone.utc).isoformat())

    params.append(customer_id)

    sql = f"""
        UPDATE {TABLE}
        SET {', '.join(set_parts)}
        WHERE customer_id = %s
        RETURNING *
    """

    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def soft_delete(conn: AsyncConnection, customer_id: str) -> dict[str, Any] | None:
    """Soft delete a customer by setting deleted_at."""
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = %s
        WHERE customer_id = %s AND deleted_at IS NULL
        RETURNING customer_id, deleted_at
        """,
        (now, customer_id),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def recover(conn: AsyncConnection, customer_id: str) -> dict[str, Any] | None:
    """Recover a soft-deleted customer."""
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = NULL
        WHERE customer_id = %s
        RETURNING *
        """,
        (customer_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    import re
    name = name.lower().strip()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


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
