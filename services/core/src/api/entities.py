"""
Generic Entity Update + Delete Endpoints (WS4)

PUT  /api/v1/entity/{type}/{id} - Update fields or recover soft-deleted entity.
DELETE /api/v1/entity/{type}/{id} - Soft delete.

Per MENTAL_MODEL #3: Strict field allowlist enforcement.
Per MENTAL_MODEL #4: Per-field edit history.
Per MENTAL_MODEL #7: Soft delete with 24h recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Request

from src.auth.permissions import check_permission, get_user_id
from src.data import (
    customer_repository,
    edit_history_repository,
    inventory_repository,
    invoice_repository,
)
from src.database.pool import get_connection
from src.errors import CoreError, CoreErrorCode
from src.models.entities import EntityDeleteResponse, EntityUpdateRequest
from src.sse.models import SSEEvent
from src.validation.field_allowlists import (
    ENTITY_TABLE_MAP,
    validate_entity_type,
    validate_fields,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["entities"])

RECOVERY_WINDOW_HOURS = 24

PERMISSIONS: dict[str, tuple[str, str]] = {
    "invoice": ("invoice.update", "invoice.delete"),
    "customer": ("customer.update", "customer.delete"),
    "inventory": ("inventory.update", "inventory.delete"),
}

REPOS = {
    "invoice": invoice_repository,
    "customer": customer_repository,
    "inventory": inventory_repository,
}

PK_COLUMNS: dict[str, str] = {
    "invoice": "invoice_id",
    "customer": "customer_id",
    "inventory": "product_id",
}


@router.put("/entity/{entity_type}/{entity_id}")
async def update_entity(
    request: Request,
    entity_type: str,
    entity_id: str,
    body: EntityUpdateRequest,
):
    """Update user-editable fields or recover a soft-deleted entity."""

    validate_entity_type(entity_type)
    update_perm = PERMISSIONS[entity_type][0]
    check_permission(request, update_perm)

    user_id = get_user_id(request)
    repo = REPOS[entity_type]
    pool = request.app.state.pool
    sse_manager = request.app.state.sse_manager

    async with get_connection(pool, "public") as conn:

        current = await repo.get_by_id(conn, entity_id)
        if current is None:
            raise CoreError(
                error_code=CoreErrorCode.ENTITY_NOT_FOUND,
                message=f"{entity_type.title()} {entity_id} not found",
            )

        if body.recover:
            return await _handle_recovery(
                conn, repo, entity_type, entity_id, current, sse_manager,
            )

        field_updates = body.get_field_updates()
        if not field_updates:
            return current

        validate_fields(entity_type, field_updates)

        changed_fields = await edit_history_repository.write_field_changes(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            current_record=current,
            new_fields=field_updates,
            changed_by=user_id,
            change_reason=body.change_reason,
        )

        if not changed_fields:
            return current

        updated = await repo.update_fields(
            conn, entity_id, field_updates, updated_by=user_id,
        )

        pk_col = PK_COLUMNS[entity_type]
        await sse_manager.publish(SSEEvent(
            event_type=f"{entity_type}.updated",
            data={pk_col: entity_id, "fields_changed": changed_fields},
        ))

        logger.info(
            "entity_updated",
            entity_type=entity_type,
            entity_id=entity_id,
            fields_changed=changed_fields,
            user_id=user_id,
        )

        return updated


@router.delete("/entity/{entity_type}/{entity_id}")
async def delete_entity(
    request: Request,
    entity_type: str,
    entity_id: str,
):
    """Soft-delete an entity."""

    validate_entity_type(entity_type)
    delete_perm = PERMISSIONS[entity_type][1]
    check_permission(request, delete_perm)

    user_id = get_user_id(request)
    repo = REPOS[entity_type]
    pool = request.app.state.pool
    sse_manager = request.app.state.sse_manager

    async with get_connection(pool, "public") as conn:

        current = await repo.get_by_id(conn, entity_id)
        if current is None:
            raise CoreError(
                error_code=CoreErrorCode.ENTITY_NOT_FOUND,
                message=f"{entity_type.title()} {entity_id} not found",
            )

        if current.get("deleted_at"):
            deleted_at = current["deleted_at"]
            recovery_until = _compute_recovery_until(deleted_at)
            raise CoreError(
                error_code=CoreErrorCode.ENTITY_DELETED,
                message=f"{entity_type.title()} {entity_id} is already deleted",
                details=[{
                    "deleted_at": str(deleted_at),
                    "recovery_until": str(recovery_until),
                }],
            )

        if entity_type == "invoice":
            result = await repo.soft_delete(conn, entity_id, deleted_by=user_id)
        else:
            result = await repo.soft_delete(conn, entity_id)

    if result is None:
        raise CoreError(
            error_code=CoreErrorCode.ENTITY_NOT_FOUND,
            message=f"{entity_type.title()} {entity_id} not found",
        )

    deleted_at = result["deleted_at"]
    recovery_until = _compute_recovery_until(deleted_at)

    pk_col = PK_COLUMNS[entity_type]
    await sse_manager.publish(SSEEvent(
        event_type=f"{entity_type}.deleted",
        data={pk_col: entity_id},
    ))

    logger.info(
        "entity_deleted",
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
    )

    return EntityDeleteResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        deleted_at=str(deleted_at),
        recovery_until=str(recovery_until),
    )


async def _handle_recovery(conn, repo, entity_type, entity_id, current, sse_manager):
    """Handle soft-delete recovery within 24h window."""
    deleted_at = current.get("deleted_at")

    if not deleted_at:
        raise CoreError(
            error_code=CoreErrorCode.INVALID_FIELD_VALUE,
            message=f"{entity_type.title()} {entity_id} is not deleted \u2014 nothing to recover",
        )

    if isinstance(deleted_at, str):
        try:
            dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
    elif isinstance(deleted_at, datetime):
        dt = deleted_at
    else:
        dt = datetime.now(timezone.utc)

    recovery_deadline = dt + timedelta(hours=RECOVERY_WINDOW_HOURS)
    now = datetime.now(timezone.utc)

    if now > recovery_deadline:
        raise CoreError(
            error_code=CoreErrorCode.RECOVERY_EXPIRED,
            message=f"Recovery window expired for {entity_type} {entity_id}",
            details=[{
                "deleted_at": str(deleted_at),
                "recovery_until": recovery_deadline.isoformat(),
                "expired_at": recovery_deadline.isoformat(),
            }],
        )

    recovered = await repo.recover(conn, entity_id)

    pk_col = PK_COLUMNS[entity_type]
    await sse_manager.publish(SSEEvent(
        event_type=f"{entity_type}.updated",
        data={pk_col: entity_id, "fields_changed": ["deleted_at"]},
    ))

    logger.info(
        "entity_recovered",
        entity_type=entity_type,
        entity_id=entity_id,
    )

    return recovered


def _compute_recovery_until(deleted_at) -> str:
    if isinstance(deleted_at, str):
        try:
            dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
    elif isinstance(deleted_at, datetime):
        dt = deleted_at
    else:
        dt = datetime.now(timezone.utc)

    return (dt + timedelta(hours=RECOVERY_WINDOW_HOURS)).isoformat()
