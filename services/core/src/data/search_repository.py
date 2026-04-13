"""
FTS Search Repository (WS4)

Per MENTAL_MODEL \u00a76 + Q9 APPROVED: PostgreSQL tsvector/tsquery with GIN indexes.
Same mechanism for both inline search params and POST /search.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from src.database.pool import get_connection
from src.models.entities import (
    CUSTOMER_LIST_FIELDS,
    INVENTORY_LIST_FIELDS,
    INVOICE_LIST_FIELDS,
)


logger = structlog.get_logger()


SEARCH_FIELDS: dict[str, list[str]] = {
    "invoice": [
        "invoice_id", "invoice_number", "buyer_name",
        "total_amount", "workflow_status", "created_at",
    ],
    "customer": [
        "customer_id", "company_name", "tin",
        "customer_type", "compliance_score", "created_at",
    ],
    "inventory": [
        "product_id", "product_name", "hsn_code",
        "type", "avg_unit_price", "created_at",
    ],
}

TABLES: dict[str, str] = {
    "invoice": "invoices.invoices",
    "customer": "customers.customers",
    "inventory": "inventory.inventory",
}


async def search_entity(
    conn: AsyncConnection,
    entity_type: str,
    query: str,
    *,
    page: int = 1,
    per_page: int = 50,
    date_from: str | None = None,
    date_to: str | None = None,
    status: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Search a single entity type using FTS.

    Returns (items, total_count).
    """
    table = TABLES[entity_type]
    fields = ", ".join(SEARCH_FIELDS[entity_type])

    where_clauses = [
        "deleted_at IS NULL",
        "fts_vector @@ websearch_to_tsquery('english', %s)",
    ]

    params = [query]

    if entity_type == "invoice":
        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(date_from)

        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(date_to)

        if status:
            placeholders = ", ".join(["%s"] * len(status))
            where_clauses.append(f"workflow_status IN ({placeholders})")
            params.extend(status)

    where = " AND ".join(where_clauses)
    offset = (page - 1) * per_page

    count_params = list(params)
    count_sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"
    cur = await conn.execute(count_sql, count_params)
    count_row = await cur.fetchone()
    total_count = count_row[0] if count_row else 0

    results_params = [query] + list(params) + [per_page, offset]
    results_sql = f"""
        SELECT {fields},
               ts_rank(fts_vector, websearch_to_tsquery('english', %s)) AS relevance
        FROM {table}
        WHERE {where}
        ORDER BY relevance DESC
        LIMIT %s OFFSET %s
    """

    cur = await conn.execute(results_sql, results_params)
    rows = await cur.fetchall()
    cols = [desc.name for desc in cur.description]
    items = [dict(zip(cols, row)) for row in rows]

    return items, total_count


async def search_all(
    pool: AsyncConnectionPool,
    query: str,
    entity_types: list[str],
    *,
    page: int = 1,
    per_page: int = 50,
    date_from: str | None = None,
    date_to: str | None = None,
    status: list[str] | None = None,
) -> dict[str, tuple[list[dict[str, Any]], int]]:
    """Search multiple entity types in parallel using asyncio.gather."""

    async def _search_one(entity_type: str) -> tuple[str, list[dict[str, Any]], int]:
        async with get_connection(pool, "public") as conn:
            items, count = await search_entity(
                conn,
                entity_type,
                query,
                page=page,
                per_page=per_page,
                date_from=date_from,
                date_to=date_to,
                status=status,
            )

        return entity_type, items, count

    tasks = [_search_one(et) for et in entity_types]
    results_list = await asyncio.gather(*tasks)

    return {et: (items, count) for et, items, count in results_list}
