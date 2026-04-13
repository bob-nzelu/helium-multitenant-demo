"""
Finalize Audit Logger — Records every pipeline step for traceability.

Writes to ``finalize_audit_log`` table. Each finalize attempt produces
multiple rows (one per step: VALIDATE, IRN_GENERATE, COMMIT, EDGE_SUBMIT).

See: WS5_DB_INTEGRITY.md, Tier A3
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg import AsyncConnection

logger = logging.getLogger(__name__)


# Step names (used as ``action`` column values)
VALIDATE = "VALIDATE"
IRN_GENERATE = "IRN_GENERATE"
QR_GENERATE = "QR_GENERATE"
COMMIT = "COMMIT"
EDGE_SUBMIT = "EDGE_SUBMIT"

# Status values
STARTED = "STARTED"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"


AUDIT_INSERT = """
    INSERT INTO finalize_audit_log (
        batch_id, company_id, idempotency_key,
        action, status, detail, invoice_count,
        created_at
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_TIMESTAMP)
"""


class AuditLogger:
    """Logs finalize pipeline steps to the audit table."""

    def __init__(self, conn: AsyncConnection):
        self._conn = conn

    async def log(
        self,
        batch_id: str,
        company_id: str,
        action: str,
        status: str,
        detail: str | None = None,
        invoice_count: int | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        """Write a single audit log entry.

        Args:
            batch_id: HLX batch identifier.
            company_id: Tenant company identifier.
            action: Pipeline step (VALIDATE, COMMIT, etc.).
            status: Step outcome (STARTED, SUCCEEDED, FAILED).
            detail: Optional error message or summary.
            invoice_count: Number of invoices involved.
            idempotency_key: Idempotency key if applicable.
        """
        try:
            await self._conn.execute(
                AUDIT_INSERT,
                (
                    batch_id,
                    company_id,
                    idempotency_key,
                    action,
                    status,
                    detail,
                    invoice_count,
                ),
            )
        except Exception as e:
            # Audit logging must never crash the pipeline
            logger.error(
                "audit_log_write_failed: batch=%s action=%s error=%s",
                batch_id, action, e,
            )
