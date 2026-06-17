"""
StatusService — Q37 Gap #5, POST /api/status orchestration.

HB-side data (result, received_at, batch_id) is live now.
Core-side data (firs_status, invoice_number) is a null stub until Core builds
the external_transaction_id lookup column (Bob's ruling 2026-06-17).

Result derivation rule (per spec §4):
    failed      → HB file_entries.status == 'error'  (or Core workflow='ERROR')
    duplicate   → dedup path matched (HB returned is_duplicate=True)
    processed   → irn is present / HB status == 'processed' / 'finalized'
    pending     → otherwise
"""

import logging
from typing import Any, Dict, List, Optional

from ..api.models import StatusEntry, StatusResponse

logger = logging.getLogger(__name__)


def _derive_result(hb_record: Dict[str, Any], core_record: Optional[Dict[str, Any]]) -> str:
    """Derive the canonical middleware result from HB + Core records.

    Priority order:
      1. failed  — HB status is error-like OR Core returns ERROR workflow
      2. duplicate — HB marks the record as a dedup hit
      3. processed — HB record has an IRN (finalized) or is processed/finalized
      4. pending  — everything else
    """
    hb_status = str(hb_record.get("status") or "").lower()
    is_duplicate = bool(hb_record.get("is_duplicate"))
    irn = hb_record.get("irn") or (core_record or {}).get("irn")

    # Core workflow error (null-safe; Core is a stub returning None today)
    core_workflow = str((core_record or {}).get("workflow_status") or "").upper()

    if hb_status in ("error", "failed") or core_workflow == "ERROR":
        return "failed"
    if is_duplicate:
        return "duplicate"
    if irn or hb_status in ("processed", "finalized"):
        return "processed"
    return "pending"


def _build_entry(
    hb_record: Dict[str, Any],
    core_record: Optional[Dict[str, Any]],
) -> StatusEntry:
    """Build one StatusEntry by merging an HB blob record with an optional Core invoice record."""
    result = _derive_result(hb_record, core_record)

    # Prefer Core's IRN if present; fall back to HB's
    irn = (core_record or {}).get("irn") or hb_record.get("irn") or None

    return StatusEntry(
        transaction_id=hb_record.get("transaction_id") or None,
        irn=irn,
        batch_id=hb_record.get("batch_id") or None,
        invoice_number=(core_record or {}).get("invoice_number") or None,
        result=result,
        firs_status=(core_record or {}).get("firs_status") or None,
        received_at=hb_record.get("received_at") or None,
        processed_at=hb_record.get("processed_at") or (core_record or {}).get("processed_at") or None,
    )


class StatusService:
    """
    Orchestrates POST /api/status queries.

    Calls HeartBeatClient for blob/batch status, CoreClient for invoice
    status (null stub today), and merges them into a flat results[].

    Unknown ids → empty results[], never an error (per spec).
    """

    def __init__(self, heartbeat: Any, core: Any):
        self._hb = heartbeat
        self._core = core

    async def query(
        self,
        *,
        transaction_id: Optional[str],
        irn: Optional[str],
        batch_id: Optional[str],
        tenant_id: str,
    ) -> StatusResponse:
        """
        Look up status by exactly one of: transaction_id, irn, batch_id.

        Returns StatusResponse(results=[]) for an unknown id (never 404).

        Args:
            transaction_id: ERP external transaction reference.
            irn: Invoice Reference Number.
            batch_id: Batch submission id (may return multiple entries).
            tenant_id: Resolved tenant from CallerContext.
        """
        # 1. Fetch HB records ────────────────────────────────────────────────
        hb_records: List[Dict[str, Any]] = []
        try:
            if batch_id:
                hb_records = await self._hb.get_blob_status_by_batch(
                    batch_id, tenant_id
                )
            elif transaction_id:
                rec = await self._hb.get_blob_status_by_transaction_id(
                    transaction_id, tenant_id
                )
                if rec is not None:
                    hb_records = [rec]
            elif irn:
                rec = await self._hb.get_blob_status_by_irn(irn, tenant_id)
                if rec is not None:
                    hb_records = [rec]
        except Exception as exc:
            # HB unreachable → graceful empty result (never 5xx to caller)
            logger.warning(
                "StatusService: HB lookup failed — tenant=%s batch_id=%s "
                "transaction_id=%s irn=%s: %s",
                tenant_id,
                batch_id,
                transaction_id,
                irn,
                exc,
            )
            return StatusResponse(results=[])

        if not hb_records:
            return StatusResponse(results=[])

        # 2. For each HB record, enrich with Core invoice status (null stub) ─
        entries: List[StatusEntry] = []
        for hb_record in hb_records:
            # Core lookup by the record's own transaction_id or IRN.
            record_txn_id = hb_record.get("transaction_id") or transaction_id
            record_irn = hb_record.get("irn") or irn
            core_record: Optional[Dict[str, Any]] = None
            try:
                core_record = await self._core.get_invoice_status(
                    transaction_id=record_txn_id,
                    irn=record_irn,
                    tenant_id=tenant_id,
                )
            except Exception as exc:
                # Core unavailable → graceful null; never block the HB data
                logger.debug(
                    "StatusService: Core lookup failed (graceful) — "
                    "transaction_id=%s irn=%s: %s",
                    record_txn_id,
                    record_irn,
                    exc,
                )

            entries.append(_build_entry(hb_record, core_record))

        logger.info(
            "StatusService: query complete — tenant=%s selector=%s count=%d",
            tenant_id,
            batch_id or transaction_id or irn or "(none)",
            len(entries),
        )
        return StatusResponse(results=entries)
