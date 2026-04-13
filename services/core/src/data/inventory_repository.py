"""
Inventory Data Repository (WS4)

Per Q6: Fully qualified table names (inventory.inventory).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg import AsyncConnection

from src.models.entities import INVENTORY_LIST_FIELDS

logger = structlog.get_logger()

TABLE = "inventory.inventory"
CLASSIFICATION_CANDIDATES_TABLE = "inventory.inventory_classification_candidates"
EDIT_HISTORY_TABLE = "inventory.inventory_edit_history"


async def get_by_id(conn: AsyncConnection, product_id: str) -> dict[str, Any] | None:
    """Fetch a single inventory record with classification candidates."""
    cur = await conn.execute(
        f"SELECT * FROM {TABLE} WHERE product_id = %s",
        (product_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None

    cols = [desc.name for desc in cur.description]
    inventory = dict(zip(cols, row))

    inventory["classification_candidates"] = await _fetch_children(
        conn, CLASSIFICATION_CANDIDATES_TABLE, "product_id", product_id, "rank",
    )

    return inventory


async def list_paginated(
    conn: AsyncConnection,
    *,
    page: int = 1,
    per_page: int = 50,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    type_filter: str | None = None,
    vat_treatment: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch paginated inventory list items with filters."""
    fields = ", ".join(INVENTORY_LIST_FIELDS)
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if type_filter:
        where_clauses.append("type = %s")
        params.append(type_filter)

    if vat_treatment:
        where_clauses.append("vat_treatment = %s")
        params.append(vat_treatment)

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
    type_filter: str | None = None,
    vat_treatment: str | None = None,
    search: str | None = None,
) -> int:
    """Count inventory records matching filters."""
    where_clauses = ["deleted_at IS NULL"]
    params: list = []

    if type_filter:
        where_clauses.append("type = %s")
        params.append(type_filter)

    if vat_treatment:
        where_clauses.append("vat_treatment = %s")
        params.append(vat_treatment)

    if search:
        where_clauses.append("fts_vector @@ websearch_to_tsquery('english', %s)")
        params.append(search)

    where = " AND ".join(where_clauses)
    cur = await conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {where}", params)
    row = await cur.fetchone()
    return row[0] if row else 0


async def update_fields(
    conn: AsyncConnection,
    product_id: str,
    fields: dict[str, Any],
    updated_by: str,
) -> dict[str, Any] | None:
    """Update specified fields on an inventory record."""
    set_parts: list = []
    params: list = []

    for field_name, value in fields.items():
        set_parts.append(f"{field_name} = %s")
        params.append(value)

    if "product_name" in fields and fields["product_name"] is not None:
        normalized = _normalize_product_name(fields["product_name"])
        set_parts.append("product_name_normalized = %s")
        params.append(normalized)

    if "hsn_code" in fields or "service_code" in fields:
        set_parts.append("classification_source = %s")
        params.append("MANUAL")

    set_parts.append("updated_by = %s")
    params.append(updated_by)
    set_parts.append("updated_at = %s")
    params.append(datetime.now(timezone.utc).isoformat())

    params.append(product_id)

    sql = f"""
        UPDATE {TABLE}
        SET {', '.join(set_parts)}
        WHERE product_id = %s
        RETURNING *
    """

    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def soft_delete(conn: AsyncConnection, product_id: str) -> dict[str, Any] | None:
    """Soft delete an inventory record by setting deleted_at."""
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = %s
        WHERE product_id = %s AND deleted_at IS NULL
        RETURNING product_id, deleted_at
        """,
        (now, product_id),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def recover(conn: AsyncConnection, product_id: str) -> dict[str, Any] | None:
    """Recover a soft-deleted inventory record."""
    cur = await conn.execute(
        f"""
        UPDATE {TABLE}
        SET deleted_at = NULL
        WHERE product_id = %s
        RETURNING *
        """,
        (product_id,),
    )

    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


def _normalize_product_name(name: str) -> str:
    """Uppercase, strip punctuation, collapse whitespace."""
    import re
    name = name.upper().strip()
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
