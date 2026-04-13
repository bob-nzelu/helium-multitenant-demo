"""WS3: Preview Generator — branch invoices into categories, build .hlx, store blob."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from uuid6 import uuid7

from helium_formats.hlm.models import HLMFile
from helium_formats.hlx.models import (
    BundleIntegrity,
    HLXManifest,
    HLXStatistics,
    SheetEntry,
)
from helium_formats.hlx.packer import pack_hlx
from helium_formats.hlx.crypto import encrypt_hlx

from src.processing.models import (
    PipelineContext,
    RedFlag,
    ResolvedInvoice,
    ResolvedCustomer,
    ResolvedProduct,
    ResolveResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Branch categories
# ---------------------------------------------------------------------------

SHEET_SUBMISSION = "submission"
SHEET_DUPLICATE = "duplicate"
SHEET_LATE = "late"
SHEET_FOC = "foc"
SHEET_UNUSUAL = "unusual"
SHEET_POSSIBLE_B2B = "possible_b2b"
SHEET_FAILED = "failed"
SHEET_CUSTOMERS = "customers"
SHEET_INVENTORY = "inventory"

# Interaction tiers per sheet
_INTERACTION_TIERS: dict[str, str] = {
    SHEET_SUBMISSION: "primary",
    SHEET_DUPLICATE: "informational",
    SHEET_LATE: "informational",
    SHEET_FOC: "informational",
    SHEET_UNUSUAL: "informational",
    SHEET_POSSIBLE_B2B: "actionable",
    SHEET_FAILED: "actionable",
    SHEET_CUSTOMERS: "informational",
    SHEET_INVENTORY: "informational",
}

_DISPLAY_NAMES: dict[str, str] = {
    SHEET_SUBMISSION: "Invoices for Submission",
    SHEET_DUPLICATE: "Duplicate Invoices",
    SHEET_LATE: "Late Invoices",
    SHEET_FOC: "Free-of-Charge Invoices",
    SHEET_UNUSUAL: "Unusual Invoices",
    SHEET_POSSIBLE_B2B: "Possible B2B Candidates",
    SHEET_FAILED: "Failed Invoices",
    SHEET_CUSTOMERS: "Customers",
    SHEET_INVENTORY: "Inventory",
}

_ICONS: dict[str, str] = {
    SHEET_SUBMISSION: "check_circle",
    SHEET_DUPLICATE: "content_copy",
    SHEET_LATE: "schedule",
    SHEET_FOC: "money_off",
    SHEET_UNUSUAL: "warning",
    SHEET_POSSIBLE_B2B: "business",
    SHEET_FAILED: "error",
    SHEET_CUSTOMERS: "people",
    SHEET_INVENTORY: "inventory_2",
}

# Fields where provenance tracking applies (enrichable by Transforma/HIS)
_PROVENANCE_FIELDS: list[str] = [
    "hsn_code", "buyer_lga_code", "buyer_state_code",
    "seller_lga_code", "seller_state_code", "category", "subcategory",
]

# Threshold: invoice older than 48 hours from now
_LATE_THRESHOLD_HOURS = 48
# FOC threshold
_FOC_THRESHOLD = 0.01


@dataclass
class BranchResult:
    """Output of branching invoices into categories."""

    categories: dict[str, list[dict]] = field(default_factory=dict)
    statistics: dict[str, int] = field(default_factory=dict)


class PreviewGenerator:
    """Generate .hlx archive from pipeline results.

    After Phase 5 (RESOLVE) and Phase 6 (PORTO BELLO), this class:
    1. Branches invoices into 7 categories
    2. Serializes each as .hlm
    3. Builds report.json and metadata.json
    4. Packs into .hlx via helium_formats
    5. Encrypts with tenant company_id
    6. Uploads to HeartBeat blob

    Args:
        blob_client: HeartBeatBlobClient for uploading.
    """

    def __init__(self, blob_client: Any, audit_logger=None):
        self._blob_client = blob_client
        self._audit_logger = audit_logger

    async def generate(
        self,
        context: PipelineContext,
        resolve_result: ResolveResult,
        red_flags: list[RedFlag],
        phase_timings: dict[str, int],
        processing_time_ms: int,
        duplicate_count: int = 0,
        skipped_count: int = 0,
    ) -> str:
        """Generate .hlx and upload to blob store.

        Returns:
            hlx_blob_uuid — the blob UUID of the stored .hlx file.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Step 1: Branch invoices into categories
        branch = self._branch_invoices(resolve_result.invoices, red_flags)

        # WS6: Audit pipeline.branching
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="pipeline.branching",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                metadata={
                    "sheet_counts": {k: len(v) for k, v in branch.categories.items() if v},
                },
            )

        # Step 2: Build .hlm files for each non-empty category
        sheets: dict[str, HLMFile] = {}
        sheet_entries: list[SheetEntry] = []
        sort_order = 1

        for sheet_id in [
            SHEET_SUBMISSION, SHEET_DUPLICATE, SHEET_LATE,
            SHEET_FOC, SHEET_UNUSUAL, SHEET_POSSIBLE_B2B, SHEET_FAILED,
        ]:
            rows = branch.categories.get(sheet_id, [])
            if not rows:
                continue

            columns = self._extract_columns(rows)
            hlm_file = HLMFile(
                hlm_version="2.0",
                data_type="invoice",
                schema_version="2.1.1.0",
                generated_at=now,
                generated_by="core",
                company_id=context.company_id,
                metadata={
                    "total_rows": len(rows),
                    "total_columns": len(columns),
                    "source": "process_preview",
                    "data_uuid": context.data_uuid,
                },
                columns=columns,
                rows=rows,
            )
            sheets[sheet_id] = hlm_file

            sheet_entries.append(SheetEntry(
                id=sheet_id,
                filename=f"sheets/{sheet_id}.hlm",
                display_name=_DISPLAY_NAMES[sheet_id],
                category="failed" if sheet_id == SHEET_FAILED else "output",
                interaction_tier=_INTERACTION_TIERS[sheet_id],
                row_count=len(rows),
                column_count=len(columns),
                icon=_ICONS.get(sheet_id),
                sort_order=sort_order,
                description=f"{_DISPLAY_NAMES[sheet_id]} ({len(rows)} invoices)",
            ))
            sort_order += 1

        # Step 2b: Build entity sheets (customers.hlm + inventory.hlm)
        customers_sheet = self._build_customers_sheet(
            resolve_result.customers, resolve_result.invoices, context, now
        )
        if customers_sheet:
            sheets[SHEET_CUSTOMERS] = customers_sheet[0]
            sheet_entries.append(customers_sheet[1])
            sheet_entries[-1] = SheetEntry(
                id=SHEET_CUSTOMERS,
                filename=f"sheets/{SHEET_CUSTOMERS}.hlm",
                display_name=_DISPLAY_NAMES[SHEET_CUSTOMERS],
                category="entity",
                interaction_tier=_INTERACTION_TIERS[SHEET_CUSTOMERS],
                row_count=len(customers_sheet[0].rows),
                column_count=len(customers_sheet[0].columns),
                icon=_ICONS.get(SHEET_CUSTOMERS),
                sort_order=sort_order,
                description=f"Customers ({len(customers_sheet[0].rows)} records)",
            )
            sort_order += 1

        inventory_sheet = self._build_inventory_sheet(
            resolve_result.inventory, resolve_result.invoices, context, now
        )
        if inventory_sheet:
            sheets[SHEET_INVENTORY] = inventory_sheet[0]
            sheet_entries.append(SheetEntry(
                id=SHEET_INVENTORY,
                filename=f"sheets/{SHEET_INVENTORY}.hlm",
                display_name=_DISPLAY_NAMES[SHEET_INVENTORY],
                category="entity",
                interaction_tier=_INTERACTION_TIERS[SHEET_INVENTORY],
                row_count=len(inventory_sheet[0].rows),
                column_count=len(inventory_sheet[0].columns),
                icon=_ICONS.get(SHEET_INVENTORY),
                sort_order=sort_order,
                description=f"Inventory ({len(inventory_sheet[0].rows)} products)",
            ))
            sort_order += 1

        # Step 3: Compute statistics
        valid_count = len(branch.categories.get(SHEET_SUBMISSION, []))
        failed_count = len(branch.categories.get(SHEET_FAILED, []))
        total_invoices = sum(len(v) for v in branch.categories.values())

        # Overall confidence
        all_invoices = resolve_result.invoices
        if all_invoices:
            overall_confidence = sum(
                getattr(inv, "overall_confidence", getattr(inv, "confidence", 0.0))
                for inv in all_invoices
            ) / len(all_invoices)
        else:
            overall_confidence = 0.0

        stats = HLXStatistics(
            total_invoices=total_invoices,
            valid_count=valid_count,
            failed_count=failed_count,
            duplicate_count=duplicate_count,
            processing_time_ms=processing_time_ms,
            overall_confidence=round(overall_confidence, 4),
        )

        # Step 4: Build manifest
        manifest = HLXManifest(
            hlx_version="1.0",
            data_uuid=context.data_uuid,
            queue_id="",  # Filled by caller if needed
            company_id=context.company_id,
            generated_at=now,
            generated_by="core",
            schema_version="2.1.1.0",
            sheets=sheet_entries,
            statistics=stats,
            bundle_integrity=BundleIntegrity(
                source_data_uuid=context.data_uuid,
                source_type="bulk_upload",
                all_sheets_same_source=True,
            ),
        )

        # Step 5: Build report.json
        report = self._build_report(stats, red_flags, phase_timings, all_invoices, now)

        # Step 6: Build metadata.json
        metadata = self._build_metadata(context, now)

        # Step 7: Pack into .hlx
        hlx_bytes = pack_hlx(manifest, sheets, report, metadata)

        # Step 8: Encrypt
        encrypted = encrypt_hlx(hlx_bytes, context.company_id)

        # Step 9: Upload to blob
        blob_uuid = str(uuid7())
        filename = f"{context.data_uuid}.hlx"
        await self._blob_client.upload_blob(
            blob_uuid=blob_uuid,
            filename=filename,
            data=encrypted,
            content_type="application/x-helium-exchange",
            company_id=context.company_id,
        )

        logger.info(
            "Generated .hlx: %s (%d sheets, %d invoices, %d bytes)",
            blob_uuid, len(sheets), total_invoices, len(encrypted),
        )

        # WS6: Audit hlx.generated, hlx.encrypted, hlx.stored
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="hlx.generated",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                metadata={
                    "hlx_blob_uuid": blob_uuid,
                    "sheet_count": len(sheets),
                    "size_bytes": len(encrypted),
                },
            )
            await self._audit_logger.log(
                event_type="hlx.encrypted",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                metadata={"encryption_method": "company_id"},
            )
            await self._audit_logger.log(
                event_type="hlx.stored",
                entity_type="queue",
                entity_id=context.data_uuid,
                action="PROCESS",
                company_id=context.company_id,
                metadata={"blob_uuid": blob_uuid},
            )

        return blob_uuid

    # -----------------------------------------------------------------------
    # Branching logic
    # -----------------------------------------------------------------------

    def _branch_invoices(
        self, invoices: list[ResolvedInvoice], red_flags: list[RedFlag]
    ) -> BranchResult:
        """Branch invoices into 7 categories based on red flags + rules."""
        categories: dict[str, list[dict]] = {
            SHEET_SUBMISSION: [],
            SHEET_DUPLICATE: [],
            SHEET_LATE: [],
            SHEET_FOC: [],
            SHEET_UNUSUAL: [],
            SHEET_POSSIBLE_B2B: [],
            SHEET_FAILED: [],
        }

        # Index red flags by invoice_index for quick lookup
        flags_by_index: dict[int, list[RedFlag]] = {}
        for rf in red_flags:
            if rf.invoice_index is not None:
                flags_by_index.setdefault(rf.invoice_index, []).append(rf)

        for idx, inv in enumerate(invoices):
            inv_flags = flags_by_index.get(idx, [])
            inv_dict = self._invoice_to_dict(inv, idx)
            category = self._classify_invoice(inv, inv_flags, inv_dict)
            categories[category].append(inv_dict)

        return BranchResult(
            categories={k: v for k, v in categories.items() if v},
            statistics={k: len(v) for k, v in categories.items()},
        )

    def _classify_invoice(
        self,
        inv: ResolvedInvoice,
        flags: list[RedFlag],
        inv_dict: dict,
    ) -> str:
        """Determine which category an invoice belongs to."""
        # Error-severity flags → failed
        has_error = any(f.severity == "error" for f in flags)
        if has_error:
            inv_dict["__STREAM__"] = getattr(inv, "stream_type", None) or "till"
            inv_dict["__ERROR__"] = "; ".join(
                f.message for f in flags if f.severity == "error"
            )
            return SHEET_FAILED

        # Duplicate detection (red flag type)
        if any(f.type in ("duplicate_irn", "duplicate_hash", "duplicate_suspected") for f in flags):
            return SHEET_DUPLICATE

        # Late invoices (issue_date > 48 hours ago)
        if self._is_late(inv):
            return SHEET_LATE

        # Free-of-charge (total ≤ ₦0.01)
        try:
            total = float(inv.total_amount)
            if total <= _FOC_THRESHOLD:
                return SHEET_FOC
        except (ValueError, TypeError):
            pass

        # Unusual (total > 10× average — simplified: flag via red_flags)
        if any(f.type in ("unusual_amount", "unusual_pattern") for f in flags):
            return SHEET_UNUSUAL

        # Possible B2B (B2C invoice but buyer has TIN)
        if self._is_possible_b2b(inv):
            return SHEET_POSSIBLE_B2B

        # All checks passed → submission
        return SHEET_SUBMISSION

    def _is_late(self, inv: ResolvedInvoice) -> bool:
        """Check if invoice issue_date is > 48 hours ago."""
        if not inv.issue_date:
            return False
        try:
            issue = datetime.fromisoformat(inv.issue_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - issue).total_seconds() > _LATE_THRESHOLD_HOURS * 3600
        except (ValueError, TypeError):
            return False

    def _is_possible_b2b(self, inv: ResolvedInvoice) -> bool:
        """Check if B2C invoice has buyer TIN (upgrade candidate)."""
        is_b2c = getattr(inv, "transaction_type", "B2C") == "B2C"
        has_tin = bool(getattr(inv, "buyer_tin", None))
        return is_b2c and has_tin

    # -----------------------------------------------------------------------
    # Serialization helpers
    # -----------------------------------------------------------------------

    def _invoice_to_dict(self, inv: ResolvedInvoice, index: int) -> dict:
        """Convert a ResolvedInvoice to a flat dict for .hlm rows."""
        d: dict[str, Any] = {
            "_row_index": index,
            "invoice_number": inv.invoice_number,
            "helium_invoice_no": inv.helium_invoice_no,
            "direction": inv.direction,
            "document_type": inv.document_type,
            "transaction_type": getattr(inv, "transaction_type", "B2B"),
            "firs_invoice_type_code": inv.firs_invoice_type_code,
            "issue_date": inv.issue_date,
            "due_date": inv.due_date,
            "currency_code": inv.currency_code,
            "total_amount": inv.total_amount,
            "tax_exclusive_amount": inv.tax_exclusive_amount,
            "total_tax_amount": inv.total_tax_amount,
            "seller_business_name": inv.seller_business_name,
            "seller_tin": inv.seller_tin,
            "buyer_business_name": inv.buyer_business_name,
            "buyer_tin": inv.buyer_tin,
            "buyer_address": inv.buyer_address,
            "confidence": getattr(inv, "overall_confidence", getattr(inv, "confidence", 0.0)),
            "workflow_status": "pending",
            "customer_id": getattr(inv, "customer_id", None),
            "line_item_count": len(inv.line_items) if inv.line_items else 0,
        }

        # Per-field provenance (HLX v1.1) — populated by Transforma, mapped through by WS3
        provenance = getattr(inv, "field_provenance", {})
        if provenance:
            d["__provenance__"] = {
                field_name: provenance.get(field_name, "ORIGINAL")
                for field_name in _PROVENANCE_FIELDS
                if field_name in provenance
            }

        return d

    def _extract_columns(self, rows: list[dict]) -> list[dict]:
        """Extract column definitions from row dicts."""
        if not rows:
            return []
        sample = rows[0]
        columns = []
        for k in sample.keys():
            if k == "__provenance__":
                continue  # Not a display column
            col = {
                "name": k,
                "type": "string",
                "display_name": k.replace("_", " ").title(),
            }
            # Mark enrichable fields with provenance_default
            if k in _PROVENANCE_FIELDS:
                col["provenance_default"] = "ORIGINAL"
            columns.append(col)
        return columns

    # -----------------------------------------------------------------------
    # Entity sheet builders (HLX v1.1)
    # -----------------------------------------------------------------------

    def _build_customers_sheet(
        self,
        customers: list[ResolvedCustomer],
        invoices: list[ResolvedInvoice],
        context: PipelineContext,
        now: str,
    ) -> tuple[HLMFile, SheetEntry] | None:
        """Build customers.hlm entity sheet."""
        if not customers:
            return None

        # Cross-reference: invoice_count + total_amount per customer
        cust_stats: dict[str, dict] = {}
        for inv in invoices:
            cid = getattr(inv, "customer_id", None)
            if not cid:
                continue
            if cid not in cust_stats:
                cust_stats[cid] = {"invoice_count": 0, "total_amount": 0.0}
            cust_stats[cid]["invoice_count"] += 1
            try:
                cust_stats[cid]["total_amount"] += float(inv.total_amount)
            except (ValueError, TypeError):
                pass

        rows = []
        for cust in customers:
            stats = cust_stats.get(cust.customer_id, {"invoice_count": 0, "total_amount": 0.0})
            rows.append({
                "customer_id": cust.customer_id,
                "company_name": cust.company_name,
                "tin": cust.tin,
                "rc_number": cust.rc_number,
                "email": cust.email,
                "phone": cust.phone,
                "address": cust.address,
                "city": cust.city,
                "state": cust.state,
                "country": cust.country,
                "customer_type": getattr(cust, "customer_type", "CORPORATE"),  # From Transforma
                "__IS_NEW__": cust.match_type == "NEW",
                "match_type": cust.match_type,
                "match_confidence": cust.match_confidence,
                "invoice_count": stats["invoice_count"],
                "total_amount": round(stats["total_amount"], 2),
            })

        columns = self._extract_columns(rows)
        hlm = HLMFile(
            hlm_version="2.0",
            data_type="customer",
            schema_version="2.1.1.0",
            generated_at=now,
            generated_by="core",
            company_id=context.company_id,
            metadata={
                "total_rows": len(rows),
                "total_columns": len(columns),
                "source": "process_preview",
                "data_uuid": context.data_uuid,
            },
            columns=columns,
            rows=rows,
        )
        # SheetEntry built by caller
        return hlm, SheetEntry(id=SHEET_CUSTOMERS, filename="", display_name="", category="entity",
                               interaction_tier="informational", row_count=len(rows), column_count=len(columns))

    def _build_inventory_sheet(
        self,
        products: list[ResolvedProduct],
        invoices: list[ResolvedInvoice],
        context: PipelineContext,
        now: str,
    ) -> tuple[HLMFile, SheetEntry] | None:
        """Build inventory.hlm entity sheet."""
        if not products:
            return None

        # Cross-reference: invoice_count per product
        prod_stats: dict[str, int] = {}
        for inv in invoices:
            for li in (inv.line_items or []):
                pid = getattr(li, "product_id", None)
                if pid:
                    prod_stats[pid] = prod_stats.get(pid, 0) + 1

        rows = []
        for prod in products:
            rows.append({
                "product_id": prod.product_id,
                "product_name": prod.product_name,
                "helium_sku": prod.helium_sku,
                "customer_sku": prod.customer_sku,
                "hs_code": prod.hs_code,
                "service_code": prod.service_code,
                "category": prod.category,
                "item_type": prod.item_type,
                "vat_treatment": getattr(prod, "vat_treatment", "STANDARD"),  # From Transforma
                "__IS_NEW__": prod.match_type == "NEW",
                "match_type": prod.match_type,
                "match_confidence": prod.match_confidence,
                "invoice_count": prod_stats.get(prod.product_id, 0),
            })

        columns = self._extract_columns(rows)
        hlm = HLMFile(
            hlm_version="2.0",
            data_type="product",
            schema_version="2.1.1.0",
            generated_at=now,
            generated_by="core",
            company_id=context.company_id,
            metadata={
                "total_rows": len(rows),
                "total_columns": len(columns),
                "source": "process_preview",
                "data_uuid": context.data_uuid,
            },
            columns=columns,
            rows=rows,
        )
        return hlm, SheetEntry(id=SHEET_INVENTORY, filename="", display_name="", category="entity",
                               interaction_tier="informational", row_count=len(rows), column_count=len(columns))

    # -----------------------------------------------------------------------
    # Report + Metadata builders
    # -----------------------------------------------------------------------

    def _build_report(
        self,
        stats: HLXStatistics,
        red_flags: list[RedFlag],
        phase_timings: dict[str, int],
        invoices: list,
        now: str,
    ) -> dict:
        """Build report.json contents."""
        rf_list = [
            {
                "type": rf.type,
                "severity": rf.severity,
                "message": rf.message,
                "phase": rf.phase,
                "invoice_index": rf.invoice_index,
                "field": rf.field,
                "suggestion": getattr(rf, "suggested_value", None),
            }
            for rf in red_flags
        ]

        # Red flag summary
        by_type: dict[str, int] = {}
        error_count = warning_count = info_count = 0
        for rf in red_flags:
            by_type[rf.type] = by_type.get(rf.type, 0) + 1
            if rf.severity == "error":
                error_count += 1
            elif rf.severity == "warning":
                warning_count += 1
            else:
                info_count += 1

        # Confidence breakdown
        high = medium = low = 0
        for inv in invoices:
            conf = getattr(inv, "overall_confidence", getattr(inv, "confidence", 0.0))
            if conf >= 0.95:
                high += 1
            elif conf >= 0.90:
                medium += 1
            else:
                low += 1

        return {
            "summary": stats.model_dump(),
            "red_flags": rf_list,
            "red_flag_summary": {
                "error_count": error_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "by_type": by_type,
            },
            "phase_timings": phase_timings,
            "confidence_breakdown": {
                "high_confidence_count": high,
                "medium_confidence_count": medium,
                "low_confidence_count": low,
                "thresholds": {"high": 0.95, "medium": 0.90, "low": 0.0},
            },
            "generated_at": now,
            "pipeline_version": "1.0.0",
        }

    def _build_metadata(self, context: PipelineContext, now: str) -> dict:
        """Build metadata.json contents."""
        return {
            "data_uuid": context.data_uuid,
            "company_id": context.company_id,
            "uploaded_by": context.helium_user_id,
            "x_trace_id": context.trace_id,
            "hlx_id": str(uuid7()),
            "version_number": 1,
            "previous_version_id": None,
            "change_reason": "initial",
            "change_summary": None,
            "pipeline": {
                "phases_executed": [
                    "fetch", "parse", "transform", "enrich", "resolve", "branch"
                ],
                "phases_skipped": [],
            },
            "versions": {
                "core_version": "1.0.0",
                "hlx_version": "1.0",
                "hlm_version": "2.0",
                "schema_version": "2.1.1.0",
            },
            "generated_at": now,
        }
