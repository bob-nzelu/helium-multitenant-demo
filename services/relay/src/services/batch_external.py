"""
Batch External Service (Q37 Gap #3/#4/#7/#8)

Processes a JSON array of ERP invoice records in one POST /api/ingest request.
Each record is validated, VAT-computed (7.5% if absent), deduplicated by
transaction_id, then an IRN + QR are generated using the tenant's
firs_service_id from TenantConfig.service_id.

Dedup key: SHA-256 of the serialised record bytes (same primitive as the
file-level DedupChecker). Falls back to transaction_id if blob write has not
yet been committed (session-cache only check against previously seen
transaction_ids within this batch).

Gap #8 fix: invoice_data["firs_service_id"] = tenant.service_id is injected
before IRNGenerator.generate() so the tenant's real FIRS ID is used instead
of the "A8BM72KQ" hardcoded fallback in irn.py::_inline_irn().
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from uuid6 import uuid7

from ..api.models import (
    BatchDuplicateEntry,
    BatchFailedEntry,
    BatchIngestResponse,
    BatchProcessedEntry,
    BatchSummary,
)
from ..core.irn import IRNGenerator
from ..core.qr import QRGenerator
from ..core.tenant import TenantConfig
from ..services.ingestion import IngestionService

logger = logging.getLogger(__name__)


# ── Internal result container (not the HTTP response model) ──────────────────


@dataclass
class BatchIngestResult:
    """Internal result returned by BatchExternalService.process_batch().

    The route handler maps this to BatchIngestResponse (the HTTP model).
    """

    batch_id: str
    trace_id: str
    processed: List[BatchProcessedEntry] = field(default_factory=list)
    duplicates: List[BatchDuplicateEntry] = field(default_factory=list)
    failed: List[BatchFailedEntry] = field(default_factory=list)

    @property
    def summary(self) -> BatchSummary:
        return BatchSummary(
            total=len(self.processed) + len(self.duplicates) + len(self.failed),
            processed=len(self.processed),
            duplicates=len(self.duplicates),
            failed=len(self.failed),
        )

    @property
    def status(self) -> str:
        if self.failed == [] and self.duplicates == []:
            return "ok"
        if self.processed:
            return "partial"
        return "rejected"

    def to_response(self) -> BatchIngestResponse:
        return BatchIngestResponse(
            status=self.status,
            batch_id=self.batch_id,
            trace_id=self.trace_id,
            summary=self.summary,
            processed=self.processed,
            duplicates=self.duplicates,
            failed=self.failed,
        )


# ── Service ───────────────────────────────────────────────────────────────────


class BatchExternalService:
    """Processes a JSON array of ERP invoice records in one request.

    Dependencies are the same as ExternalService; both share an
    IngestionService for blob writes, and IRNGenerator / QRGenerator for
    per-invoice fiscalisation.
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any,
        irn_generator: IRNGenerator,
        qr_generator: QRGenerator,
    ):
        self._ingestion = ingestion_service
        self._core = core_client
        self._irn = irn_generator
        self._qr = qr_generator

    # ── Public API ────────────────────────────────────────────────────────────

    async def process_batch(
        self,
        records: List[Dict[str, Any]],
        batch_id: str,
        tenant: TenantConfig,
        trace_id: str,
        jwt_token: Optional[str] = None,
    ) -> BatchIngestResult:
        """Process N invoice records, returning a BatchIngestResult.

        Per-record steps:
            1. Validate: transaction_id required, fee_amount required.
            2. VAT: if vat_amount absent → round(fee_amount * 0.075, 2).
            3. Apply tenant field mapping via tenant.get_field().
            4. Dedup: in-batch session set of seen transaction_ids.
            5. Write one blob per record via the shared ingestion pipeline.
            6. Inject firs_service_id = tenant.service_id → IRN (Gap #8).
            7. Generate QR.
            8. Append to processed / duplicates / failed.
        """
        result = BatchIngestResult(batch_id=batch_id, trace_id=trace_id)

        # Session dedup — transaction_ids seen in THIS batch
        seen_txn_ids: Dict[str, Dict[str, str]] = {}  # txn_id → {irn, data_uuid}

        for idx, raw_record in enumerate(records):
            txn_id = _coerce_str(raw_record, "transaction_id", tenant)
            if not txn_id:
                result.failed.append(
                    BatchFailedEntry(
                        transaction_id=f"<record[{idx}]>",
                        error="transaction_id is required",
                        error_code="VALIDATION_FAILED",
                    )
                )
                continue

            fee_amount = _coerce_float(raw_record, "fee_amount", tenant)
            if fee_amount is None:
                result.failed.append(
                    BatchFailedEntry(
                        transaction_id=txn_id,
                        error="fee_amount is required",
                        error_code="VALIDATION_FAILED",
                    )
                )
                continue

            # Step 2 — VAT auto-compute
            vat_amount = _coerce_float(raw_record, "vat_amount", tenant)
            if vat_amount is None:
                vat_amount = round(fee_amount * 0.075, 2)

            # Step 4 — in-batch dedup by transaction_id
            if txn_id in seen_txn_ids:
                prior = seen_txn_ids[txn_id]
                result.duplicates.append(
                    BatchDuplicateEntry(
                        transaction_id=txn_id,
                        message="Already received in a previous batch",
                        duplicate_of={
                            "irn": prior.get("irn", ""),
                            "data_uuid": prior.get("data_uuid", ""),
                            "batch_id": batch_id,
                        },
                    )
                )
                logger.info(
                    f"[{trace_id}] Batch dedup (session) — txn_id={txn_id}"
                )
                continue

            # Step 5 — write one blob per record
            # Serialize the canonical record as the "file" bytes so HeartBeat
            # gets a real blob with a stable hash.
            canonical = _build_canonical(raw_record, txn_id, fee_amount, vat_amount, tenant)
            blob_bytes = json.dumps(canonical, sort_keys=True).encode("utf-8")
            blob_filename = f"{txn_id}.json"

            try:
                ingest_result = await self._ingestion.ingest(
                    files=[(blob_filename, blob_bytes)],
                    api_key=tenant.api_key,
                    trace_id=trace_id,
                    metadata={
                        "queue_mode": "api",
                        "connection_type": "api",
                        "batch_id": batch_id,
                        "transaction_id": txn_id,
                    },
                    jwt_token=jwt_token,
                )
                data_uuid = ingest_result.data_uuid
            except Exception as exc:
                # DuplicateFileError from ingestion pipeline = cross-batch dedup
                error_code = getattr(exc, "error_code", "INGEST_ERROR")
                if error_code == "DUPLICATE_FILE":
                    result.duplicates.append(
                        BatchDuplicateEntry(
                            transaction_id=txn_id,
                            message="Already received in a previous batch",
                            duplicate_of={
                                "irn": "",
                                "data_uuid": getattr(exc, "original_queue_id", "") or "",
                                "batch_id": "",
                            },
                        )
                    )
                    seen_txn_ids[txn_id] = {"irn": "", "data_uuid": ""}
                    logger.info(
                        f"[{trace_id}] Batch dedup (HB) — txn_id={txn_id}: {exc}"
                    )
                else:
                    result.failed.append(
                        BatchFailedEntry(
                            transaction_id=txn_id,
                            error=str(exc),
                            error_code=str(error_code),
                        )
                    )
                    logger.warning(
                        f"[{trace_id}] Blob write failed — txn_id={txn_id}: {exc}"
                    )
                continue

            # Step 6 — IRN: inject tenant's real FIRS service_id (Gap #8)
            invoice_data = dict(canonical)
            invoice_data["firs_service_id"] = tenant.service_id

            try:
                irn = self._irn.generate(invoice_data)
            except Exception as exc:
                result.failed.append(
                    BatchFailedEntry(
                        transaction_id=txn_id,
                        error=f"IRN generation failed: {exc}",
                        error_code="IRN_GENERATION_ERROR",
                    )
                )
                logger.error(
                    f"[{trace_id}] IRN failed — txn_id={txn_id}: {exc}"
                )
                continue

            # Step 7 — QR
            try:
                qr_code = self._qr.generate(irn)
            except Exception as exc:
                result.failed.append(
                    BatchFailedEntry(
                        transaction_id=txn_id,
                        error=f"QR generation failed: {exc}",
                        error_code="QR_GENERATION_ERROR",
                    )
                )
                logger.error(
                    f"[{trace_id}] QR failed — txn_id={txn_id}: {exc}"
                )
                continue

            # Step 8 — record success
            seen_txn_ids[txn_id] = {"irn": irn, "data_uuid": data_uuid}
            result.processed.append(
                BatchProcessedEntry(
                    transaction_id=txn_id,
                    irn=irn,
                    qr_code=qr_code,
                    data_uuid=data_uuid,
                    fee_amount=fee_amount,
                    vat_amount=vat_amount,
                )
            )
            logger.info(
                f"[{trace_id}] Batch processed — txn_id={txn_id}, "
                f"irn={irn[:16]}..., data_uuid={data_uuid}"
            )

        logger.info(
            f"[{trace_id}] Batch complete — batch_id={batch_id}, "
            f"processed={len(result.processed)}, "
            f"duplicates={len(result.duplicates)}, "
            f"failed={len(result.failed)}"
        )
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_field(record: Dict[str, Any], name: str, tenant: TenantConfig) -> Any:
    """Resolve a canonical field name via tenant field mapping."""
    canonical = tenant.get_field(name)
    if canonical != name and canonical in record:
        return record[canonical]
    return record.get(name)


def _coerce_str(record: Dict[str, Any], name: str, tenant: TenantConfig) -> str:
    """Return the field as a stripped string, or empty string if absent/None."""
    val = _resolve_field(record, name, tenant)
    if val is None:
        return ""
    return str(val).strip()


def _coerce_float(record: Dict[str, Any], name: str, tenant: TenantConfig) -> Optional[float]:
    """Return the field as float, or None if absent."""
    val = _resolve_field(record, name, tenant)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _build_canonical(
    record: Dict[str, Any],
    txn_id: str,
    fee_amount: float,
    vat_amount: float,
    tenant: TenantConfig,
) -> Dict[str, Any]:
    """Build a canonical dict from the raw record, with resolved field names."""
    return {
        "transaction_id": txn_id,
        "fee_amount": fee_amount,
        "vat_amount": vat_amount,
        "description": _coerce_str(record, "description", tenant),
        "transaction_date": _coerce_str(record, "transaction_date", tenant),
        "branch": _coerce_str(record, "branch", tenant),
        "buyer_name": _coerce_str(record, "buyer_name", tenant),
        "buyer_tin": _coerce_str(record, "buyer_tin", tenant),
        "buyer_address": _coerce_str(record, "buyer_address", tenant),
        # Preserve invoice_number so IRN generator can pick it up
        "invoice_number": _coerce_str(record, "transaction_id", tenant),
    }


def auto_batch_id() -> str:
    """Generate a batch_id when the caller does not supply one."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"BATCH{ts}"
