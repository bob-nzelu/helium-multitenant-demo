"""
WS7: Report Repository — CRUD for core.reports table.

Follows existing psycopg3 pattern: raw SQL with parameterized queries,
dict-from-row helper, no ORM.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg import AsyncConnection

import structlog

logger = structlog.get_logger()


def _row_to_dict(cur, row) -> dict[str, Any]:
    """Convert a psycopg row to a dict using cursor description."""
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def create_report(
    conn: AsyncConnection,
    *,
    report_id: str,
    company_id: str,
    report_type: str,
    format: str,
    filters: dict[str, Any] | None = None,
    generated_by: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Insert a new report record (status=generating)."""
    cur = await conn.execute(
        """
        INSERT INTO core.reports (
            report_id, company_id, report_type, format,
            status, title, filters, generated_by,
            expires_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            report_id,
            company_id,
            report_type,
            format,
            "generating",
            title,
            json.dumps(filters) if filters else None,
            generated_by,
            datetime.now(timezone.utc) + timedelta(days=7),
        ),
    )
    row = await cur.fetchone()
    return _row_to_dict(cur, row)


async def get_report(
    conn: AsyncConnection,
    report_id: str,
) -> dict[str, Any] | None:
    """Fetch a single report by ID."""
    cur = await conn.execute(
        "SELECT * FROM core.reports WHERE report_id = %s",
        (report_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_dict(cur, row)


async def update_status(
    conn: AsyncConnection,
    report_id: str,
    *,
    status: str,
    blob_uuid: str | None = None,
    size_bytes: int | None = None,
    error_message: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Update report status and related fields."""
    set_parts = ["status = %s"]
    params: list[Any] = [status]

    if blob_uuid is not None:
        set_parts.append("blob_uuid = %s")
        params.append(blob_uuid)

    if size_bytes is not None:
        set_parts.append("size_bytes = %s")
        params.append(size_bytes)

    if error_message is not None:
        set_parts.append("error_message = %s")
        params.append(error_message)

    if generated_at is not None:
        set_parts.append("generated_at = %s")
        params.append(generated_at)

    params.append(report_id)

    cur = await conn.execute(
        f"UPDATE core.reports SET {', '.join(set_parts)} "
        f"WHERE report_id = %s RETURNING *",
        params,
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_dict(cur, row)


async def list_reports(
    conn: AsyncConnection,
    company_id: str,
    *,
    page: int = 1,
    per_page: int = 50,
    report_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Paginated list of reports for a company."""
    where_parts = ["company_id = %s"]
    params: list[Any] = [company_id]

    if report_type:
        where_parts.append("report_type = %s")
        params.append(report_type)

    if status:
        where_parts.append("status = %s")
        params.append(status)

    where_clause = " AND ".join(where_parts)
    offset = (page - 1) * per_page

    cur = await conn.execute(
        f"SELECT * FROM core.reports WHERE {where_clause} "
        f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
        params + [per_page, offset],
    )
    rows = await cur.fetchall()
    return [_row_to_dict(cur, r) for r in rows]


async def count_reports(
    conn: AsyncConnection,
    company_id: str,
    *,
    report_type: str | None = None,
    status: str | None = None,
) -> int:
    """Count reports for a company (with optional filters)."""
    where_parts = ["company_id = %s"]
    params: list[Any] = [company_id]

    if report_type:
        where_parts.append("report_type = %s")
        params.append(report_type)

    if status:
        where_parts.append("status = %s")
        params.append(status)

    where_clause = " AND ".join(where_parts)

    cur = await conn.execute(
        f"SELECT COUNT(*) FROM core.reports WHERE {where_clause}",
        params,
    )
    row = await cur.fetchone()
    return row[0] if row else 0


async def cleanup_expired(conn: AsyncConnection) -> int:
    """Delete reports past their expires_at. Returns count deleted."""
    cur = await conn.execute(
        "DELETE FROM core.reports WHERE expires_at IS NOT NULL AND expires_at < NOW()"
    )
    return cur.rowcount or 0
