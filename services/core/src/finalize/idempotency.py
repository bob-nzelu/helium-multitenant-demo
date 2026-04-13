"""
Idempotency Guard — Prevents duplicate finalize executions.

Key format: SHA-256(batch_id + company_id + version_number)
TTL: 24 hours (expired keys garbage-collected on read).

See: WS5_DB_INTEGRITY.md, Tier A1
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)

# Keys expire after 24 hours
IDEMPOTENCY_TTL_HOURS = 24


IDEMP_CHECK = """
    SELECT result_json, created_at, expires_at
    FROM finalize_idempotency
    WHERE idempotency_key = $1
      AND expires_at > CURRENT_TIMESTAMP
"""

IDEMP_INSERT = """
    INSERT INTO finalize_idempotency (
        idempotency_key, batch_id, company_id,
        result_json, created_at, expires_at
    ) VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, $5)
    ON CONFLICT (idempotency_key) DO NOTHING
"""

IDEMP_CLEANUP = """
    DELETE FROM finalize_idempotency
    WHERE expires_at < CURRENT_TIMESTAMP
"""


def compute_idempotency_key(
    batch_id: str,
    company_id: str,
    version_number: int = 1,
) -> str:
    """Compute a deterministic idempotency key.

    Same finalize request always produces the same key.

    Args:
        batch_id: HLX batch identifier.
        company_id: Tenant company identifier.
        version_number: .hlx version number (default 1).

    Returns:
        SHA-256 hex digest.
    """
    payload = f"{batch_id}:{company_id}:{version_number}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def check_idempotency(
    conn: AsyncConnection,
    key: str,
) -> dict[str, Any] | None:
    """Check if a finalize request was already processed.

    Args:
        conn: Active database connection.
        key: Idempotency key from ``compute_idempotency_key``.

    Returns:
        Cached result dict if found, None if not found or expired.
    """
    cur = await conn.execute(IDEMP_CHECK, (key,))
    row = await cur.fetchone()
    if row is None:
        return None

    result_json = row[0]
    if result_json:
        try:
            return json.loads(result_json)
        except json.JSONDecodeError:
            logger.warning("corrupt idempotency result for key=%s", key)
            return None
    return None


async def record_idempotency(
    conn: AsyncConnection,
    key: str,
    batch_id: str,
    company_id: str,
    result: dict[str, Any],
) -> None:
    """Store the finalize result for future replay.

    Args:
        conn: Active database connection.
        key: Idempotency key.
        batch_id: HLX batch identifier.
        company_id: Tenant company identifier.
        result: FinalizeResult.to_dict() to cache.
    """
    expires_at = (
        datetime.utcnow() + timedelta(hours=IDEMPOTENCY_TTL_HOURS)
    ).isoformat()
    result_json = json.dumps(result)

    await conn.execute(
        IDEMP_INSERT,
        (key, batch_id, company_id, result_json, expires_at),
    )
    logger.info("idempotency_key_recorded: key=%s batch=%s", key[:12], batch_id)


async def cleanup_expired(conn: AsyncConnection) -> int:
    """Delete expired idempotency keys.

    Returns:
        Number of keys deleted.
    """
    cur = await conn.execute(IDEMP_CLEANUP)
    count = cur.rowcount or 0
    if count > 0:
        logger.info("idempotency_cleanup: deleted %d expired keys", count)
    return count
