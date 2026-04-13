"""
Edit History Repository (WS4)

Shared across all entity types. Per MENTAL_MODEL \u00a74: per-field audit trail.
All three edit_history tables share the same 8-field structure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from psycopg import AsyncConnection

logger = structlog.get_logger()


HISTORY_TABLES: dict[str, str] = {
    "invoice": "invoices.invoice_edit_history",
    "customer": "customers.customer_edit_history",
    "inventory": "inventory.inventory_edit_history",
}

FK_COLUMNS: dict[str, str] = {
    "invoice": "invoice_id",
    "customer": "customer_id",
    "inventory": "product_id",
}


async def write_field_changes(
    conn: AsyncConnection,
    entity_type: str,
    entity_id: str,
    current_record: dict[str, Any],
    new_fields: dict[str, Any],
    changed_by: str,
    change_reason: str | None = None,
) -> list[str]:
    """
    Compare current_record with new_fields and insert one edit_history row
    per changed field. Returns list of field names that were actually changed.

    Converts values to str for old_value/new_value storage (NULL \u2192 None).
    Skips fields where str(old) == str(new).
    """
    table = HISTORY_TABLES[entity_type]
    fk_col = FK_COLUMNS[entity_type]
    now = datetime.now(timezone.utc).isoformat()
    changed_fields: list = []

    for field_name, new_value in new_fields.items():
        old_value = current_record.get(field_name)

        old_str = str(old_value) if old_value is not None else None
        new_str = str(new_value) if new_value is not None else None
        if old_str == new_str:
            continue

        changed_fields.append(field_name)

        await conn.execute(
            f"""
            INSERT INTO {table} ({fk_col}, field_name, old_value, new_value, changed_by, changed_at, change_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (entity_id, field_name, old_str, new_str, changed_by, now, change_reason),
        )

    if changed_fields:
        logger.info(
            "edit_history_written",
            entity_type=entity_type,
            entity_id=entity_id,
            fields_changed=changed_fields,
            changed_by=changed_by,
        )

    return changed_fields
