"""
External Service (API Flow) — Demo Build

Accepts a JSON array (one batch per call), processes each record individually:
  1. Validate required fields (transaction_id required; vat_amount optional)
  2. Dedup within batch + across previous calls (in-memory, resets on restart)
  3. Ingest record blob into HeartBeat
  4. Generate IRN + QR per record
  5. Return BatchResult with processed / duplicates / failed arrays

IRN format:
    {cleaned_transaction_id}-ABMFB-{YYYYMMDD}
    cleaned = strip all non-alphanumeric chars, uppercase

QR data format:
    NRS:{irn}:{file_hash_8chars}
"""

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.tenant import TenantConfig
from ..errors import DuplicateFileError, RateLimitExceededError
from ..models.ubl import UBLInvoice
from .batch_store import BatchStore
from .ingestion import IngestionService

logger = logging.getLogger(__name__)


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class BatchRecordResult:
    """A successfully processed record."""
    transaction_id: str
    irn: str = ""
    qr_code: str = ""
    data_uuid: str = ""
    vat_amount: Optional[float] = None
    vat_computation: str = ""       # "calculated" | "exact"
    fee_amount: Optional[float] = None


@dataclass
class BatchDuplicateResult:
    """A record that was already received."""
    transaction_id: str
    message: str = "Already received"
    duplicate_of: Optional[Dict] = None     # original txn details


@dataclass
class BatchFailedResult:
    """A record that failed processing."""
    transaction_id: str
    error: str = ""


@dataclass
class BatchResult:
    """Result of a full batch ingestion."""
    batch_id: str
    trace_id: str = ""
    source: str = ""                # "Demo API" | "Dashboard"
    source_id: str = ""             # API key or "internal-test-console"
    processed: List[BatchRecordResult] = dc_field(default_factory=list)
    duplicates: List[BatchDuplicateResult] = dc_field(default_factory=list)
    failed: List[BatchFailedResult] = dc_field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.duplicates or self.failed)

    @property
    def http_status(self) -> int:
        """200 = all clean; 207 = partial; 422 = all rejected."""
        if not self.processed and (self.duplicates or self.failed):
            return 422
        return 207 if self.has_issues else 200

    @property
    def response_status(self) -> str:
        if not self.processed and (self.duplicates or self.failed):
            return "rejected"
        return "partial" if self.has_issues else "ok"

    @property
    def summary(self) -> Dict:
        return {
            "total":      len(self.processed) + len(self.duplicates) + len(self.failed),
            "processed":  len(self.processed),
            "duplicates": len(self.duplicates),
            "failed":     len(self.failed),
        }

    def to_dict(self) -> Dict:
        return {
            "status":     self.response_status,
            "batch_id":   self.batch_id,
            "trace_id":   self.trace_id,
            "source":     self.source,
            "source_id":  self.source_id,
            "summary":    self.summary,
            "processed": [
                {
                    "transaction_id": r.transaction_id,
                    "irn":            r.irn,
                    "qr_code":        r.qr_code,
                    "data_uuid":      r.data_uuid,
                    "fee_amount":     r.fee_amount,
                    "vat_amount":     r.vat_amount,
                    "vat_computation": r.vat_computation,
                }
                for r in self.processed
            ],
            "duplicates": [
                {
                    "transaction_id": r.transaction_id,
                    "message":        r.message,
                    **({"duplicate_of": r.duplicate_of} if r.duplicate_of else {}),
                }
                for r in self.duplicates
            ],
            "failed": [
                {
                    "transaction_id": r.transaction_id,
                    "error":          r.error,
                }
                for r in self.failed
            ],
        }


# ── IRN / QR builders ─────────────────────────────────────────────────────────

def _build_irn(transaction_id: str, service_id: str) -> str:
    """
    Build IRN from transaction_id and tenant service_id.

    Steps:
        1. Strip all non-alphanumeric characters, uppercase
        2. Append service ID and today's date (YYYYMMDD)

    Example:
        ("TXN20260307ONT300001", "ABMFB") → "TXN20260307ONT300001-ABMFB-20260410"
        ("TXN20260307ONT300001", "ABBEY") → "TXN20260307ONT300001-ABBEY-20260410"
    """
    cleaned  = re.sub(r"[^A-Za-z0-9]", "", transaction_id).upper()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{cleaned}-{service_id}-{date_str}"


def _build_qr(irn: str, file_hash: str) -> str:
    """
    QR data: NRS:{irn}:{file_hash_8chars}

    Returns base64-encoded placeholder PNG.
    Production Relay generates a real scannable QR via the Transforma pipeline.
    """
    qr_data = f"NRS:{irn}:{file_hash[:8].upper()}"
    logger.debug(f"QR data: {qr_data}")

    TINY_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return f"data:image/png;base64,{base64.b64encode(TINY_PNG).decode()}"


# ── Service ───────────────────────────────────────────────────────────────────

class ExternalService:
    """
    External API flow: parse batch → validate → dedup → ingest → IRN/QR → return.
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any = None,
        config: Any = None,
        redis_client: Any = None,
        batch_store: Optional["BatchStore"] = None,
    ):
        self._ingestion   = ingestion_service
        self._core        = core_client
        self._config      = config
        self._redis       = redis_client
        self._batch_store = batch_store
        # In-memory dedup — maps (tenant_id, txn_id) → original record details
        self._processed_txns: Dict[Tuple[str, str], Dict] = {}

    async def process_batch(
        self,
        batch_file: Tuple[str, bytes],
        batch_id: str,
        tenant: "TenantConfig",
        trace_id: str = "",
        source: str = "Demo API",
        source_id: str = "",
    ) -> BatchResult:
        """
        Process a JSON array batch.

        Args:
            batch_file: (filename, raw_bytes) of the uploaded JSON file.
            batch_id:   Batch identifier (supplied as a form field).
            tenant:     Resolved tenant config (from API key).
            trace_id:   Request trace ID.
            source:     "Demo API" or "Dashboard".
            source_id:  API key or "internal-test-console".

        Returns:
            BatchResult with processed / duplicates / failed per record.
        """
        _, file_data = batch_file

        if not source_id:
            source_id = tenant.api_key

        # ── Parse ──────────────────────────────────────────────────────────
        try:
            records = json.loads(file_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Batch file is not valid JSON: {e}")

        if records is None:
            records = []
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise ValueError("Batch file must be a JSON array or object")

        result = BatchResult(
            batch_id=batch_id, trace_id=trace_id,
            source=source, source_id=source_id,
        )
        seen_in_batch: Set[str] = set()

        # ── UBL FORMAT: Parse and flatten records if tenant uses UBL ──────
        if tenant.is_ubl:
            flat_records = []
            for raw_record in records:
                try:
                    ubl = UBLInvoice.model_validate(raw_record)
                    flat = ubl.extract_flat_record()
                    # Preserve the original UBL payload for storage
                    flat["_ubl_payload"] = raw_record
                    flat_records.append(flat)
                except Exception as e:
                    txn_id = raw_record.get("invoice_type_code") or raw_record.get("invoiceTypeCode") or "(unknown)"
                    result.failed.append(BatchFailedResult(
                        transaction_id=str(txn_id),
                        error=f"UBL validation failed: {e}",
                    ))
            records = flat_records
            logger.info(
                f"[{trace_id}] UBL mode — parsed {len(records)} records "
                f"(failed={len(result.failed)})"
            )

        # ── Field names (from tenant config) ──────────────────────────────
        f_txn_id     = tenant.get_field("transaction_id")
        f_fee_amount = tenant.get_field("fee_amount")
        f_vat_amount = tenant.get_field("vat_amount")

        logger.info(
            f"[{trace_id}] Batch start — batch_id={batch_id} "
            f"records={len(records)} field_txn_id={f_txn_id} field_vat_amount={f_vat_amount}"
        )

        # ── Process each record ────────────────────────────────────────────
        for record in records:
            txn_id = str(record.get(f_txn_id, "")).strip()

            # Validate: transaction_id
            if not txn_id:
                result.failed.append(BatchFailedResult(
                    transaction_id="(missing)",
                    error=f"Required field '{f_txn_id}' is missing or empty",
                ))
                continue

            # ── VAT amount: optional — auto-apply 7.5% if absent; warn if provided but mismatches
            vat_val = record.get(f_vat_amount)
            fee_val = record.get(f_fee_amount)
            vat_computation = "exact"   # default: customer provided it

            if vat_val is None:
                if fee_val is not None:
                    computed = round(float(fee_val) * 0.075, 2)
                    record[f_vat_amount] = computed
                    vat_val = computed
                    vat_computation = "calculated"
                    logger.info(
                        f"[{trace_id}] VAT auto-applied txn={txn_id}: "
                        f"{computed} (7.5% of {fee_val})"
                    )
                else:
                    vat_computation = ""  # no fee, no vat — nothing to compute
            else:
                if fee_val is not None:
                    expected  = round(float(fee_val) * 0.075, 2)
                    actual    = float(vat_val)
                    tolerance = max(expected * 0.01, 0.01)
                    if abs(actual - expected) > tolerance:
                        logger.warning(
                            f"[{trace_id}] VAT mismatch txn={txn_id}: "
                            f"provided={actual} expected={expected} (7.5% of {fee_val}) — accepted"
                        )

            # Dedup: within this batch
            if txn_id in seen_in_batch:
                # The first occurrence in this batch may already be in seen_in_batch
                # but not yet in _processed_txns (it's still being processed)
                result.duplicates.append(BatchDuplicateResult(
                    transaction_id=txn_id,
                    message="Duplicate within this batch",
                ))
                continue
            seen_in_batch.add(txn_id)

            # Dedup: across previous batches (in-memory, per-tenant)
            dedup_key = (tenant.tenant_id, txn_id)
            if dedup_key in self._processed_txns:
                original = self._processed_txns[dedup_key]
                result.duplicates.append(BatchDuplicateResult(
                    transaction_id=txn_id,
                    message="Already received in a previous batch",
                    duplicate_of=original,
                ))
                continue

            # ── Ingest ──────────────────────────────────────────────────────
            record_bytes = json.dumps(record).encode("utf-8")
            meta = {
                "transaction_id":  txn_id,
                "batch_id":        batch_id,
                "queue_mode":      "api",
                "connection_type": "api",
            }

            try:
                ingest_result = await self._ingestion.ingest(
                    files=[(f"{txn_id}.json", record_bytes)],
                    api_key=tenant.api_key,
                    trace_id=trace_id,
                    metadata=meta,
                )
            except DuplicateFileError:
                result.duplicates.append(BatchDuplicateResult(
                    transaction_id=txn_id,
                    message="Already received (identical content)",
                ))
                continue
            except RateLimitExceededError:
                result.failed.append(BatchFailedResult(
                    transaction_id=txn_id,
                    error="Demo daily quota reached (500 calls/day)",
                ))
                continue
            except Exception as e:
                logger.error(f"[{trace_id}] Ingest failed for {txn_id}: {e}")
                result.failed.append(BatchFailedResult(
                    transaction_id=txn_id,
                    error=f"Processing error: {e}",
                ))
                continue

            # ── IRN + QR ────────────────────────────────────────────────────
            irn     = _build_irn(txn_id, tenant.service_id)
            qr_code = _build_qr(irn, ingest_result.file_hash)
            now_iso = datetime.now(timezone.utc).isoformat()

            # Store original record details for future duplicate lookups
            self._processed_txns[dedup_key] = {
                "transaction_id": txn_id,
                "irn":            irn,
                "data_uuid":      ingest_result.data_uuid,
                "batch_id":       batch_id,
                "trace_id":       trace_id,
                "source":         source,
                "source_id":      source_id,
                "timestamp":      now_iso,
            }

            result.processed.append(BatchRecordResult(
                transaction_id=txn_id,
                irn=irn,
                qr_code=qr_code,
                data_uuid=ingest_result.data_uuid,
                fee_amount=float(fee_val) if fee_val is not None else None,
                vat_amount=float(vat_val) if vat_val is not None else None,
                vat_computation=vat_computation,
            ))

            logger.info(f"[{trace_id}] OK — txn={txn_id} irn={irn}")

        logger.info(
            f"[{trace_id}] Batch complete — batch_id={batch_id} "
            f"processed={len(result.processed)} "
            f"duplicates={len(result.duplicates)} "
            f"failed={len(result.failed)}"
        )

        # ── Publish to dashboard (BatchStore → SSE) ────────────────────
        if self._batch_store is not None:
            await self._batch_store.add({
                **result.to_dict(),
                "http_status":  result.http_status,
                "timestamp":    datetime.now(timezone.utc).isoformat(),
                "tenant_id":    tenant.tenant_id,
                "tenant_name":  tenant.name,
            }, tenant_id=tenant.tenant_id)

        return result
