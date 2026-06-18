"""
StatusService — POST /api/status orchestration (L30 / DATA_MODEL_CANONICAL §6).

The external status surface consolidates two stores:

  * HeartBeat ``blob.file_transactions`` — the PRE-INVOICE phase
    (``pending`` / ``acknowledged`` / ``not_an_invoice``), keyed by
    ``transaction_id`` or ``batch_id``. HB is blind to invoice semantics (§8),
    so it cannot resolve ``irn`` / ``invoice_number``.
  * Core ``invoices`` — the INVOICE-LEVEL phase (IRN, workflow + transmission
    state), keyed by ``external_transaction_id`` / ``invoice_number`` / ``irn``
    / ``batch_id``. Core is the ONLY resolver of ``irn`` and ``invoice_number``.

Relay orchestrates the two calls and merges by ``transaction_id`` into the flat
L29 ``results[]`` shape.

Selector routing (exactly one is supplied):
    transaction_id → HB (by txn) + Core (by external_transaction_id)
    batch_id       → HB (txns in batch) + Core (invoices in batch), merged by txn
    invoice_number → Core (by number); then HB backfill by the txn Core returns
    irn            → Core (by irn);    then HB backfill by the txn Core returns

Both backends are NEEDS-* stubs today (HB file_transactions + Core lookup are
each the other seats' chips in the 2026-06-17 fan-out), so a live query returns
``results=[]`` until they ship. The merge below is the contract they light up.

result derivation (reconciles the L29 "3-way vocab collision"):
    failed         → HB txn status error-like OR Core workflow_status == 'ERROR'
    duplicate      → HB marked the txn a dedup hit
    not_an_invoice → HB file_transactions.status == 'not_an_invoice'
    processed      → HB 'acknowledged' OR a Core invoice exists (IRN minted)
    pending        → otherwise (incl. HB 'pending')
"""

import logging
from typing import Any, Dict, List, Optional

from ..api.models import StatusEntry, StatusResponse

logger = logging.getLogger(__name__)


def _txn_key(hb_record: Optional[Dict[str, Any]], core_record: Optional[Dict[str, Any]]) -> str:
    """The transaction_id that joins an HB row to a Core invoice row.

    HB ``file_transactions.transaction_id`` ≡ Core
    ``invoices.external_transaction_id`` (the join key, §6 / §3 catalogue).
    """
    if hb_record and hb_record.get("transaction_id"):
        return str(hb_record["transaction_id"])
    if core_record:
        return str(
            core_record.get("external_transaction_id")
            or core_record.get("transaction_id")
            or ""
        )
    return ""


def _derive_result(
    hb_record: Optional[Dict[str, Any]],
    core_record: Optional[Dict[str, Any]],
) -> str:
    """Merge the HB transaction status + Core invoice state into the L29 vocab."""
    hb = hb_record or {}
    core = core_record or {}

    hb_status = str(hb.get("status") or "").lower()
    is_duplicate = bool(hb.get("is_duplicate"))
    core_workflow = str(core.get("workflow_status") or "").upper()
    irn = core.get("irn") or hb.get("irn")

    # 1. failed wins — an error anywhere in the chain
    if hb_status in ("error", "failed") or core_workflow == "ERROR":
        return "failed"
    # 2. dedup hit
    if is_duplicate:
        return "duplicate"
    # 3. extraction classified the record as not an invoice (terminal)
    if hb_status == "not_an_invoice":
        return "not_an_invoice"
    # 4. acknowledged by Core (invoice exists / IRN minted)
    if hb_status == "acknowledged" or irn:
        return "processed"
    # 5. seeded but not yet extracted
    return "pending"


def _build_entry(
    hb_record: Optional[Dict[str, Any]],
    core_record: Optional[Dict[str, Any]],
    *,
    batch_id_hint: Optional[str] = None,
) -> StatusEntry:
    """Build one StatusEntry by merging an HB transaction row + Core invoice row."""
    hb = hb_record or {}
    core = core_record or {}

    return StatusEntry(
        transaction_id=_txn_key(hb_record, core_record) or None,
        irn=core.get("irn") or hb.get("irn") or None,
        batch_id=hb.get("batch_id") or core.get("batch_id") or batch_id_hint or None,
        invoice_number=core.get("invoice_number") or None,
        result=_derive_result(hb_record, core_record),
        # firs_status comes from invoices.transmission_status (null pre-transmit).
        firs_status=core.get("firs_status") or core.get("transmission_status") or None,
        received_at=hb.get("received_at") or hb.get("created_at") or None,
        processed_at=hb.get("processed_at") or hb.get("updated_at") or core.get("processed_at") or None,
    )


class StatusService:
    """Orchestrates POST /api/status — HB (pre-invoice) + Core (invoice) merge.

    Unknown ids → empty results[], never an error. A backend that raises is
    swallowed (graceful partial); the other backend's data still returns.
    """

    def __init__(self, heartbeat: Any, core: Any):
        self._hb = heartbeat
        self._core = core

    async def query(
        self,
        *,
        transaction_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        invoice_number: Optional[str] = None,
        irn: Optional[str] = None,
        tenant_id: str = "",
    ) -> StatusResponse:
        """Look up status by exactly one selector. Returns a flat results[]."""
        if batch_id:
            entries = await self._query_batch(batch_id, tenant_id)
        elif transaction_id:
            entries = await self._query_transaction(transaction_id, tenant_id)
        elif invoice_number:
            entries = await self._query_core_only(
                tenant_id, invoice_number=invoice_number
            )
        elif irn:
            entries = await self._query_core_only(tenant_id, irn=irn)
        else:
            entries = []

        logger.info(
            "StatusService: query complete — tenant=%s selector=%s count=%d",
            tenant_id,
            batch_id or transaction_id or invoice_number or irn or "(none)",
            len(entries),
        )
        return StatusResponse(results=entries)

    # ── Selector handlers ───────────────────────────────────────────────────

    async def _query_batch(self, batch_id: str, tenant_id: str) -> List[StatusEntry]:
        """batch_id: union HB file_transactions + Core invoices, merge by txn."""
        hb_rows = await self._safe(
            self._hb.get_transactions_by_batch(batch_id, tenant_id),
            default=[],
            what=f"HB get_transactions_by_batch({batch_id})",
        ) or []
        core_rows = await self._safe(
            self._core.get_invoices_by_batch(batch_id, tenant_id),
            default=[],
            what=f"Core get_invoices_by_batch({batch_id})",
        ) or []

        # Index Core invoices by their join key (external_transaction_id).
        core_by_txn: Dict[str, Dict[str, Any]] = {}
        for inv in core_rows:
            key = str(inv.get("external_transaction_id") or inv.get("transaction_id") or "")
            if key:
                core_by_txn[key] = inv

        entries: List[StatusEntry] = []
        seen: set = set()
        for hb_row in hb_rows:
            key = str(hb_row.get("transaction_id") or "")
            seen.add(key)
            entries.append(_build_entry(hb_row, core_by_txn.get(key), batch_id_hint=batch_id))

        # Core invoices with no matching HB row (e.g. internal uploads HB never
        # seeded) still belong in the batch answer.
        for key, inv in core_by_txn.items():
            if key not in seen:
                entries.append(_build_entry(None, inv, batch_id_hint=batch_id))

        return entries

    async def _query_transaction(
        self, transaction_id: str, tenant_id: str
    ) -> List[StatusEntry]:
        """transaction_id: HB by txn + Core by external_transaction_id."""
        hb_row = await self._safe(
            self._hb.get_transaction_by_id(transaction_id, tenant_id),
            default=None,
            what=f"HB get_transaction_by_id({transaction_id})",
        )
        core_row = await self._safe(
            self._core.get_invoice_status(transaction_id=transaction_id, tenant_id=tenant_id),
            default=None,
            what=f"Core get_invoice_status(txn={transaction_id})",
        )
        if hb_row is None and core_row is None:
            return []
        return [_build_entry(hb_row, core_row)]

    async def _query_core_only(
        self,
        tenant_id: str,
        *,
        invoice_number: Optional[str] = None,
        irn: Optional[str] = None,
    ) -> List[StatusEntry]:
        """invoice_number / irn: Core resolves (HB blind), then HB backfill.

        HB cannot answer these selectors directly, but once Core returns the
        invoice we know its ``external_transaction_id`` and can backfill the
        pre-invoice transaction status from HB for a complete entry.
        """
        core_row = await self._safe(
            self._core.get_invoice_status(
                irn=irn, invoice_number=invoice_number, tenant_id=tenant_id
            ),
            default=None,
            what=f"Core get_invoice_status(irn={irn}, inv_no={invoice_number})",
        )
        if core_row is None:
            return []  # HB is blind to irn/invoice_number — nothing to fall back to

        txn = str(core_row.get("external_transaction_id") or core_row.get("transaction_id") or "")
        hb_row = None
        if txn:
            hb_row = await self._safe(
                self._hb.get_transaction_by_id(txn, tenant_id),
                default=None,
                what=f"HB backfill get_transaction_by_id({txn})",
            )
        return [_build_entry(hb_row, core_row)]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _safe(awaitable: Any, *, default: Any, what: str) -> Any:
        """Await a backend call, returning ``default`` on any failure (graceful)."""
        try:
            return await awaitable
        except Exception as exc:  # noqa: BLE001 — never 5xx the status surface
            logger.warning("StatusService: %s failed (graceful): %s", what, exc)
            return default
