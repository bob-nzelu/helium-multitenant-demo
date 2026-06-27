"""
Invoice Creator — minimal-but-valid invoice ENTITY row writer.

Phase-2 substrate. The deployed ``invoices.invoices`` table is the
authoritative entity store the tracker and flows 6/9/10 read from, but
nothing was INSERTing rows. This module is the single lean writer used by:

  1. Ingestion (``src/ingestion/router.py::process_entry``) — creates a
     pre-submission OUTBOUND row the moment a doc lands in Core, so the
     invoice EXISTS before finalize and shows in the tracker.
  2. Finalize  (``src/finalize/lean_router.py``) — resolves the row by
     ``ref``/``doc_ref`` (queue_id / invoice_id) and flips it to
     ``TRANSMITTED`` with the real IRN, or creates-if-absent for direct
     finalize calls that never went through ingestion.
  3. Inbound seeding (``src/finalize/inbound_creator`` path) — creates
     INBOUND ``PENDING_REVIEW`` rows for flow 6.

Design notes (grounded in the DEPLOYED ``database/schemas/invoices.sql``):
  - ``invoice_id``, ``helium_invoice_no``, ``invoice_number`` are NOT NULL.
  - ``irn`` is ``UNIQUE NOT NULL`` — there is no real IRN pre-finalize, so
    we write a deterministic placeholder ``PENDING-<invoice_id>`` that
    finalize overwrites with the real FIRS IRN.
  - ``queue_id`` is UNIQUE — it is the join key finalize uses to find the
    pre-created row (``find_by_ref`` matches queue_id OR invoice_id OR irn).
  - ``company_id`` is NOT NULL.
  - Reference fields (reference_irn / reference_invoice_id) live in the
    separate ``invoices.invoice_references`` table, NOT on ``invoices``.

All SQL uses fully-qualified ``invoices.*`` names (matches invoice_repository)
and psycopg ``%s`` placeholders. Callers pass an open AsyncConnection.
"""

from __future__ import annotations
import os

import logging
from datetime import date, datetime, timezone
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Json

logger = logging.getLogger(__name__)

INVOICES_TABLE = "invoices.invoices"
REFERENCES_TABLE = "invoices.invoice_references"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clean_number(raw: str | None, fallback: str) -> str:
    """Derive an alphanumeric invoice_number (IRN-safe) from raw text."""
    if raw:
        cleaned = "".join(c for c in raw if c.isalnum())[:48]
        if cleaned:
            return cleaned
    cleaned = "".join(c for c in fallback if c.isalnum())[:48]
    return cleaned or "DOC"


def derive_invoice_number(original_filename: str | None, queue_id: str) -> str:
    """invoice_number from the filename stem, else a queue-id-derived stub."""
    stem = original_filename or ""
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return _clean_number(stem, queue_id.replace("-", ""))


_DEFAULT_COMPANY_ID = os.getenv("CORE_DEFAULT_COMPANY_ID", "sika-nigeria")


async def find_by_ref(
    conn: AsyncConnection, ref: str
) -> dict[str, Any] | None:
    """Resolve an existing invoice row by ANY of its stable identifiers.

    ``ref`` (the finalize ``ref``/``doc_ref``) may be a queue_id (the most
    common — ingestion set queue_id = invoice_id), an invoice_id, a
    document_id/blob_uuid, or even an irn. We match the columns finalize
    callers realistically carry, newest first.

    Returns the matched row as a dict (all columns) or None.
    """
    if not ref:
        return None
    cur = await conn.execute(
        f"""
        SELECT * FROM {INVOICES_TABLE}
        WHERE deleted_at IS NULL
          AND (queue_id = %s OR invoice_id = %s OR blob_uuid = %s OR irn = %s)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (ref, ref, ref, ref),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def create_ingest_invoice(
    conn: AsyncConnection,
    *,
    queue_id: str,
    company_id: str,
    blob_uuid: str | None = None,
    data_uuid: str | None = None,
    batch_id: str | None = None,
    original_filename: str | None = None,
    uploaded_by: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """INSERT a minimal pre-submission OUTBOUND invoice row at ingest time.

    workflow_status='COMMITTED', payment_status='UNPAID',
    transmission_status='NOT_REQUIRED', direction='OUTBOUND',
    document_type='COMMERCIAL_INVOICE'.

    Idempotent on queue_id (ON CONFLICT DO NOTHING) so re-processing a queue
    entry never duplicates. Returns the row dict on insert, the existing row
    on conflict, or None on (logged, non-fatal) failure.
    """
    invoice_id = queue_id  # queue_id is a uuid7 — globally unique, stable link
    helium_invoice_no = f"HLX-{queue_id.replace('-', '')[:20].upper()}"
    invoice_number = derive_invoice_number(original_filename, queue_id)
    placeholder_irn = f"PENDING-{queue_id}"
    issue_date = date.today().isoformat()

    try:
        cur = await conn.execute(
            f"""
            INSERT INTO {INVOICES_TABLE} (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, transaction_type,
                issue_date,
                document_currency_code, tax_currency_code,
                subtotal, tax_amount, total_amount,
                workflow_status, transmission_status, payment_status,
                company_id,
                queue_id, batch_id, blob_uuid, original_filename,
                source, source_id,
                invoice_trace_id, user_trace_id,
                created_by, helium_user_id
            ) VALUES (
                %s, %s, %s, %s,
                'OUTBOUND', 'COMMERCIAL_INVOICE', 'B2B',
                %s,
                'NGN', 'NGN',
                0, 0, 0,
                'COMMITTED', 'NOT_REQUIRED', 'UNPAID',
                %s,
                %s, %s, %s, %s,
                'core_ingest', %s,
                %s, %s,
                %s, %s
            )
            ON CONFLICT (queue_id) DO NOTHING
            RETURNING *
            """,
            (
                invoice_id, helium_invoice_no, invoice_number, placeholder_irn,
                issue_date,
                company_id,
                queue_id, batch_id, blob_uuid, original_filename,
                data_uuid,
                trace_id, trace_id,
                uploaded_by, uploaded_by,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            # Conflict: row already exists for this queue_id — fetch & return.
            return await find_by_ref(conn, queue_id)
        cols = [desc.name for desc in cur.description]
        logger.info("ingest_invoice_created queue_id=%s invoice_id=%s", queue_id, invoice_id)
        return dict(zip(cols, row))
    except Exception as exc:  # non-fatal — ingestion must not die on this
        logger.warning("create_ingest_invoice_failed queue_id=%s: %s", queue_id, exc)
        return None


async def create_finalize_invoice(
    conn: AsyncConnection,
    *,
    ref: str,
    company_id: str | None,
    invoice_number: str,
    irn: str,
    issue_date: str,
    total_amount: float = 0.0,
    tax_amount: float = 0.0,
    seller_tin: str | None = None,
    seller_name: str | None = None,
    buyer_tin: str | None = None,
    buyer_name: str | None = None,
    direction: str = "OUTBOUND",
    document_type: str = "COMMERCIAL_INVOICE",
    transaction_type: str = "B2B",
    currency_code: str = "NGN",
    blob_uuid: str | None = None,
    trace_id: str | None = None,
    qr_code_data: Any = None,
) -> dict[str, Any] | None:
    """Create-if-absent path for a DIRECT finalize (never ingested).

    Inserts an already-TRANSMITTED row carrying the real IRN. Used by
    lean_router only when ``find_by_ref`` returns None. invoice_id/queue_id
    are keyed on ``ref`` so a later duplicate finalize is idempotent.
    """
    invoice_id = ref
    company_id = company_id or _DEFAULT_COMPANY_ID
    helium_invoice_no = f"HLX-{ref.replace('-', '')[:20].upper() or 'DIRECT'}"
    now = _now_iso()
    try:
        cur = await conn.execute(
            f"""
            INSERT INTO {INVOICES_TABLE} (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, transaction_type,
                issue_date,
                document_currency_code, tax_currency_code,
                subtotal, tax_amount, total_amount,
                workflow_status, transmission_status, payment_status,
                transmission_date, finalized_at,
                company_id,
                queue_id, blob_uuid,
                seller_tin, seller_name, buyer_tin, buyer_name,
                qr_code_data,
                source, invoice_trace_id, user_trace_id
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s,
                %s, %s, %s,
                'TRANSMITTED', 'TRANSMITTED', 'UNPAID',
                %s, %s,
                %s,
                %s, %s,
                %s, %s, %s, %s,
                %s,
                'core_finalize', %s, %s
            )
            ON CONFLICT (queue_id) DO NOTHING
            RETURNING *
            """,
            (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, transaction_type,
                issue_date,
                currency_code, currency_code,
                total_amount - tax_amount, tax_amount, total_amount,
                now, now,
                company_id,
                ref, blob_uuid,
                seller_tin, seller_name, buyer_tin, buyer_name,
                Json(qr_code_data) if qr_code_data is not None else None,
                trace_id, trace_id,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            return await find_by_ref(conn, ref)
        cols = [desc.name for desc in cur.description]
        logger.info("finalize_invoice_created ref=%s irn=%s", ref, irn)
        return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("create_finalize_invoice_failed ref=%s: %s", ref, exc)
        return None


async def mark_transmitted(
    conn: AsyncConnection,
    *,
    invoice_pk: int,
    irn: str,
    total_amount: float | None = None,
    tax_amount: float | None = None,
    qr_code_data: Any = None,
) -> dict[str, Any] | None:
    """Flip an existing (pre-created) invoice row to TRANSMITTED + real IRN.

    Called by finalize after Edge STUB_ACCEPTED, on the row resolved via
    ``find_by_ref``. Sets workflow_status='TRANSMITTED', the real irn,
    transmission_status='TRANSMITTED', transmission_date + finalized_at.
    Optionally backfills amounts when the placeholder row had zeros.

    The workflow_status change fires the deployed audit trigger
    (fn_audit_workflow_status -> invoice_history). SSE is the handler's job.
    """
    now = _now_iso()
    set_parts = [
        "workflow_status = 'TRANSMITTED'",
        "transmission_status = 'TRANSMITTED'",
        "irn = %s",
        "transmission_date = %s",
        "finalized_at = %s",
    ]
    params: list[Any] = [irn, now, now]
    if total_amount is not None:
        set_parts.append("total_amount = %s")
        params.append(total_amount)
        set_parts.append("subtotal = %s")
        params.append((total_amount or 0) - (tax_amount or 0))
    if tax_amount is not None:
        set_parts.append("tax_amount = %s")
        params.append(tax_amount)
    if qr_code_data is not None:
        # qr_code_data column is jsonb — wrap so psycopg sends a proper json type.
        set_parts.append("qr_code_data = %s")
        params.append(Json(qr_code_data))
    params.append(invoice_pk)

    try:
        cur = await conn.execute(
            f"UPDATE {INVOICES_TABLE} SET {', '.join(set_parts)} WHERE id = %s RETURNING *",
            params,
        )
        row = await cur.fetchone()
        if row is None:
            return None
        cols = [desc.name for desc in cur.description]
        return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("mark_transmitted_failed pk=%s: %s", invoice_pk, exc)
        return None


async def get_by_invoice_id(
    conn: AsyncConnection, invoice_id: str
) -> dict[str, Any] | None:
    """Fetch a live (non-deleted) invoice row by its business invoice_id."""
    if not invoice_id:
        return None
    cur = await conn.execute(
        f"SELECT * FROM {INVOICES_TABLE} WHERE invoice_id = %s AND deleted_at IS NULL LIMIT 1",
        (invoice_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cols = [desc.name for desc in cur.description]
    return dict(zip(cols, row))


async def create_credit_note(
    conn: AsyncConnection,
    *,
    company_id: str | None,
    invoice_number: str,
    irn: str,
    issue_date: str,
    original_invoice_id: str,
    total_amount: float = 0.0,
    tax_amount: float = 0.0,
    seller_tin: str | None = None,
    seller_name: str | None = None,
    buyer_tin: str | None = None,
    buyer_name: str | None = None,
    direction: str = "OUTBOUND",
    currency_code: str = "NGN",
    blob_uuid: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """INSERT a linked CREDIT_NOTE row (flow 9 reversal) already TRANSMITTED.

    document_type='CREDIT_NOTE'. invoice_id/queue_id are derived from the
    original so a duplicate reversal is idempotent (ON CONFLICT DO NOTHING).
    The reference link itself is written separately via ``add_reference``.
    """
    invoice_id = f"CN-{original_invoice_id}"
    helium_invoice_no = f"HLX-CN-{invoice_id.replace('-', '')[:20].upper()}"
    now = _now_iso()
    try:
        cur = await conn.execute(
            f"""
            INSERT INTO {INVOICES_TABLE} (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, transaction_type,
                issue_date,
                document_currency_code, tax_currency_code,
                subtotal, tax_amount, total_amount,
                workflow_status, transmission_status, payment_status,
                transmission_date, finalized_at,
                company_id,
                queue_id, blob_uuid,
                seller_tin, seller_name, buyer_tin, buyer_name,
                source, invoice_trace_id, user_trace_id
            ) VALUES (
                %s, %s, %s, %s,
                %s, 'CREDIT_NOTE', 'B2B',
                %s,
                %s, %s,
                %s, %s, %s,
                'TRANSMITTED', 'TRANSMITTED', 'UNPAID',
                %s, %s,
                %s,
                %s, %s,
                %s, %s, %s, %s,
                'core_reversal', %s, %s
            )
            ON CONFLICT (queue_id) DO NOTHING
            RETURNING *
            """,
            (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction,
                issue_date,
                currency_code, currency_code,
                total_amount - tax_amount, tax_amount, total_amount,
                now, now,
                company_id,
                invoice_id, blob_uuid,
                seller_tin, seller_name, buyer_tin, buyer_name,
                trace_id, trace_id,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            return await get_by_invoice_id(conn, invoice_id)
        cols = [desc.name for desc in cur.description]
        logger.info("credit_note_created invoice_id=%s original=%s", invoice_id, original_invoice_id)
        return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("create_credit_note_failed original=%s: %s", original_invoice_id, exc)
        return None


async def add_reference(
    conn: AsyncConnection,
    *,
    invoice_pk: int,
    reference_type: str,
    reference_invoice_id: str | None = None,
    reference_irn: str | None = None,
    reference_issue_date: str | None = None,
) -> bool:
    """INSERT an invoices.invoice_references link row.

    ``invoice_pk`` is the integer PK (invoices.id) of the NEW invoice (e.g. the
    credit note); the FK column ``invoice_references.invoice_id`` is BIGINT ->
    invoices(id), NOT the business invoice_id text. reference_invoice_id /
    reference_irn carry the ORIGINAL invoice's business id / irn.
    """
    try:
        await conn.execute(
            f"""
            INSERT INTO {REFERENCES_TABLE} (
                invoice_id, reference_type, reference_invoice_id,
                reference_irn, reference_issue_date
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                invoice_pk, reference_type, reference_invoice_id,
                reference_irn, reference_issue_date,
            ),
        )
        return True
    except Exception as exc:
        logger.warning("add_reference_failed pk=%s: %s", invoice_pk, exc)
        return False


async def create_inbound_invoice(
    conn: AsyncConnection,
    *,
    company_id: str,
    invoice_number: str,
    irn: str | None = None,
    issue_date: str | None = None,
    total_amount: float = 0.0,
    tax_amount: float = 0.0,
    seller_tin: str | None = None,
    seller_name: str | None = None,
    buyer_tin: str | None = None,
    buyer_name: str | None = None,
    document_type: str = "COMMERCIAL_INVOICE",
    transaction_type: str = "B2B",
    currency_code: str = "NGN",
    inbound_payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """INSERT a direction='INBOUND', inbound_status='PENDING_REVIEW' row.

    Gives flow 6 (accept/reject inbound) rows to act on. The supplier is the
    seller; the tenant (company_id) is the buyer. Uses a unique synthetic
    invoice_id/queue_id derived from invoice_number so re-seeding is safe.
    """
    import uuid

    unique = uuid.uuid4().hex[:12]
    clean_no = _clean_number(invoice_number, unique)
    invoice_id = f"IN-{clean_no}-{unique}"
    helium_invoice_no = f"HLX-IN-{clean_no[:14].upper()}-{unique[:6].upper()}"
    # irn is UNIQUE NOT NULL — inbound docs carry the supplier's IRN if known,
    # else a unique placeholder so the NOT NULL/UNIQUE constraints hold.
    real_irn = irn or f"INBOUND-{invoice_id}"
    issue = issue_date or date.today().isoformat()
    now = _now_iso()

    import json as _json
    payload_json = _json.dumps(inbound_payload) if inbound_payload else None

    try:
        cur = await conn.execute(
            f"""
            INSERT INTO {INVOICES_TABLE} (
                invoice_id, helium_invoice_no, invoice_number, irn,
                direction, document_type, transaction_type,
                issue_date,
                document_currency_code, tax_currency_code,
                subtotal, tax_amount, total_amount,
                workflow_status, transmission_status, payment_status,
                inbound_status, inbound_received_at, inbound_payload_json,
                company_id,
                queue_id,
                seller_tin, seller_name, buyer_tin, buyer_name,
                source, invoice_trace_id, user_trace_id
            ) VALUES (
                %s, %s, %s, %s,
                'INBOUND', %s, %s,
                %s,
                %s, %s,
                %s, %s, %s,
                'COMMITTED', 'NOT_REQUIRED', 'UNPAID',
                'PENDING_REVIEW', %s, %s,
                %s,
                %s,
                %s, %s, %s, %s,
                'core_inbound', %s, %s
            )
            ON CONFLICT (queue_id) DO NOTHING
            RETURNING *
            """,
            (
                invoice_id, helium_invoice_no, clean_no, real_irn,
                document_type, transaction_type,
                issue,
                currency_code, currency_code,
                total_amount - tax_amount, tax_amount, total_amount,
                now, payload_json,
                company_id,
                invoice_id,
                seller_tin, seller_name, buyer_tin, buyer_name,
                trace_id, trace_id,
            ),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        cols = [desc.name for desc in cur.description]
        logger.info("inbound_invoice_created invoice_id=%s", invoice_id)
        return dict(zip(cols, row))
    except Exception as exc:
        logger.warning("create_inbound_invoice_failed: %s", exc)
        return None
