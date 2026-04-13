"""
Finalize Pipeline — Orchestrates the full finalization flow.

Routes incoming requests based on flags:
  - preview=True  → Run full Transforma pipeline, output preview .hlx
  - finalized=True → Skip Transforma, validate edits, commit to DB, queue to Edge

See: HLX_FORMAT.md v1.1 Section 9 (The 9-Step Flow)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection

from src.finalize.edit_validator import EditValidator, EditValidationResult
from src.finalize.edge_client import EdgeClient, EdgeSubmitResult
from src.finalize.errors import (
    FinalizeError,
    EditValidationError,
    IRNGenerationError,
    RecordCommitError,
    EdgeSubmitError,
)
from src.finalize.irn_generator import generate_irn, IRNError
from src.finalize.qr_generator import QRInput, generate_qr_code, QRError
from src.finalize.record_creator import RecordCreator, CommitResult

logger = logging.getLogger(__name__)


@dataclass
class FinalizeResult:
    """Full result of the finalize pipeline."""

    success: bool = False
    validation: EditValidationResult | None = None
    commit: CommitResult | None = None
    edge: EdgeSubmitResult | None = None
    irn_count: int = 0
    qr_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "irn_count": self.irn_count,
            "qr_count": self.qr_count,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        if self.commit:
            d["commit"] = self.commit.to_dict()
        if self.edge:
            d["edge"] = {
                "batch_id": self.edge.batch_id,
                "accepted": self.edge.accepted,
                "rejected": self.edge.rejected,
            }
        if self.validation:
            d["validation"] = {
                "is_valid": self.validation.is_valid,
                "violation_count": len(self.validation.violations),
                "accepted_change_count": len(self.validation.accepted_changes),
            }
        return d


class FinalizePipeline:
    """Orchestrates the complete finalization flow.

    Steps (when finalized=True):
        1. Retrieve preview .hlx from HeartBeat
        2. Diff submitted .hlm against preview .hlx (EditValidator)
        3. Generate IRN for each invoice
        4. Generate QR code for each invoice
        5. Commit records to DB (RecordCreator)
        6. Queue to Edge for FIRS submission
    """

    def __init__(
        self,
        edit_validator: EditValidator | None = None,
        record_creator: RecordCreator | None = None,
        edge_client: EdgeClient | None = None,
        heartbeat_client=None,
        his_feedback_client=None,
        audit_logger=None,
        notification_service=None,
    ):
        self.edit_validator = edit_validator or EditValidator()
        self.record_creator = record_creator or RecordCreator()
        self.edge_client = edge_client
        self._heartbeat = heartbeat_client
        self._his_feedback = his_feedback_client
        self._audit_logger = audit_logger
        self._notification_service = notification_service

    async def finalize(
        self,
        submitted_rows: list[dict[str, Any]],
        preview_rows: list[dict[str, Any]],
        conn: AsyncConnection,
        company_id: str,
        batch_id: str,
        service_id: str,
        direction: str = "OUTBOUND",
        created_by: str | None = None,
        blob_uuid: str | None = None,
        corrections: list | None = None,
    ) -> FinalizeResult:
        """Run the full finalization pipeline.

        Args:
            submitted_rows: Finalized .hlm data from Float SDK.
            preview_rows: Preview .hlx data from HeartBeat.
            conn: Active database connection.
            company_id: Tenant company identifier.
            batch_id: HLX batch identifier.
            service_id: 8-char FIRS service code (from tenant config).
            direction: Invoice direction (OUTBOUND/INBOUND).
            created_by: helium_user_id of the user who finalized.

        Returns:
            FinalizeResult with full pipeline status.
        """
        result = FinalizeResult()

        # WS6: Audit finalize.started
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="finalize.started",
                entity_type="queue",
                entity_id=batch_id,
                action="FINALIZE",
                actor_id=created_by,
                company_id=company_id,
                metadata={
                    "invoice_count": len(submitted_rows),
                    "direction": direction,
                },
            )

        # Step 1: Validate edits
        try:
            validation = self.edit_validator.validate(
                submitted_rows, preview_rows, direction
            )
            result.validation = validation
            result.warnings.extend(validation.warnings)

            if not validation.is_valid:
                result.errors.append(
                    f"Edit validation failed: {len(validation.violations)} violation(s)"
                )
                return result

            logger.info(
                "edit_validation_passed",
                accepted_changes=len(validation.accepted_changes),
                batch_id=batch_id,
            )
        except Exception as e:
            result.errors.append(f"Edit validation error: {e}")
            return result

        # Step 2: Generate IRN + QR for each invoice
        try:
            self._generate_irns(submitted_rows, service_id, result)

            # WS6: Audit finalize.irn_generated
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="finalize.irn_generated",
                    entity_type="queue",
                    entity_id=batch_id,
                    action="FINALIZE",
                    company_id=company_id,
                    metadata={"irn_count": result.irn_count},
                )

            self._generate_qr_codes(submitted_rows, result)

            # WS6: Audit finalize.qr_generated
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="finalize.qr_generated",
                    entity_type="queue",
                    entity_id=batch_id,
                    action="FINALIZE",
                    company_id=company_id,
                    metadata={"qr_count": result.qr_count},
                )
        except (IRNGenerationError, FinalizeError) as e:
            # WS6: Audit finalize.failed
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="finalize.failed",
                    entity_type="queue",
                    entity_id=batch_id,
                    action="FINALIZE",
                    company_id=company_id,
                    metadata={"error": str(e), "failed_at_step": "irn_qr_generation"},
                )
            result.errors.append(str(e))
            return result

        # Step 3: Commit to DB
        try:
            commit = await self.record_creator.commit_batch(
                conn, submitted_rows, company_id, batch_id, created_by
            )
            result.commit = commit

            if not commit.success:
                result.errors.extend(commit.errors)
                return result

            logger.info(
                "records_committed",
                invoices=commit.invoices_created,
                line_items=commit.line_items_created,
                customers_new=commit.customers_created,
                inventory_new=commit.inventory_created,
                batch_id=batch_id,
            )

            # WS6: Audit finalize.db_committed
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="finalize.db_committed",
                    entity_type="queue",
                    entity_id=batch_id,
                    action="FINALIZE",
                    company_id=company_id,
                    metadata={
                        "invoices": commit.invoices_created,
                        "line_items": commit.line_items_created,
                        "customers": commit.customers_created,
                        "inventory": commit.inventory_created,
                    },
                )
        except Exception as e:
            # WS6: Audit finalize.failed
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="finalize.failed",
                    entity_type="queue",
                    entity_id=batch_id,
                    action="FINALIZE",
                    company_id=company_id,
                    metadata={"error": str(e), "failed_at_step": "db_commit"},
                )
            result.errors.append(f"Record commit error: {e}")
            # HeartBeat: mark as error
            if self._heartbeat and blob_uuid:
                try:
                    await self._heartbeat.update_blob_status(
                        blob_uuid, "error", error_message=f"Finalize failed: {e}",
                    )
                except Exception:
                    logger.debug("HeartBeat error status failed (non-fatal)")
            return result

        # Step 4: HIS Intelligence Feedback (non-fatal)
        if self._his_feedback and corrections:
            try:
                from src.finalize.models import CONFIDENCE_WEIGHTS
                # Group corrections by source for appropriate weighting
                by_source: dict[str, list[dict]] = {}
                for c in corrections:
                    src_key = c.get("source", "hlx_review") if isinstance(c, dict) else getattr(c, "source", "hlx_review")
                    by_source.setdefault(src_key, []).append(
                        c if isinstance(c, dict) else c.model_dump()
                    )

                for source, group in by_source.items():
                    weight = CONFIDENCE_WEIGHTS.get(source, 0.40)
                    await self._his_feedback.submit_corrections(
                        corrections=group,
                        company_id=company_id,
                        source=source,
                        confidence_weight=weight,
                        context={"batch_id": batch_id},
                    )
            except Exception as e:
                result.warnings.append(f"HIS feedback failed (non-fatal): {e}")
                logger.warning("his_feedback_error", error=str(e), batch_id=batch_id)

        # Step 5: Queue to Edge
        if self.edge_client:
            try:
                submissions = EdgeClient.build_submissions(submitted_rows)
                edge_result = await self.edge_client.submit_batch(
                    batch_id, submissions, company_id
                )
                result.edge = edge_result

                if not edge_result.success:
                    result.warnings.extend(edge_result.errors)
                    logger.warning(
                        "edge_partial_failure",
                        accepted=edge_result.accepted,
                        rejected=edge_result.rejected,
                        batch_id=batch_id,
                    )
                else:
                    logger.info(
                        "edge_submitted",
                        accepted=edge_result.accepted,
                        batch_id=batch_id,
                    )
            except Exception as e:
                # Edge failure is non-fatal — invoices are committed,
                # Edge retry can happen later
                result.warnings.append(f"Edge submission failed (non-fatal): {e}")
                logger.warning("edge_submit_error", error=str(e), batch_id=batch_id)

        # WS6: Audit finalize.edge_queued (if edge was attempted)
        if self.edge_client and result.edge and self._audit_logger:
            await self._audit_logger.log(
                event_type="finalize.edge_queued",
                entity_type="queue",
                entity_id=batch_id,
                action="TRANSMIT",
                company_id=company_id,
                metadata={
                    "accepted": result.edge.accepted,
                    "rejected": result.edge.rejected,
                },
            )

        result.success = True

        # HeartBeat: mark as finalized with stats
        if self._heartbeat and blob_uuid:
            try:
                await self._heartbeat.update_blob_status(
                    blob_uuid, "finalized", processing_stats={
                        "extracted_invoice_count": len(submitted_rows),
                        "submitted_invoice_count": commit.invoices_created if commit else 0,
                        "rejected_invoice_count": len(result.errors),
                        "duplicate_count": 0,
                    },
                )
            except Exception:
                logger.debug("HeartBeat finalize status failed (non-fatal)")

        # WS6: Audit finalize.completed
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="finalize.completed",
                entity_type="queue",
                entity_id=batch_id,
                action="FINALIZE",
                company_id=company_id,
                actor_id=created_by,
                metadata={
                    "invoice_count": len(submitted_rows),
                    "irn_count": result.irn_count,
                    "qr_count": result.qr_count,
                },
            )

        # WS6: Send notification
        if self._notification_service:
            await self._notification_service.send(
                company_id=company_id,
                notification_type="business",
                category="finalize_complete",
                title=f"{len(submitted_rows)} invoices submitted to FIRS",
                body=f"Batch {batch_id}: {result.irn_count} IRNs generated, "
                     f"{result.qr_count} QR codes generated.",
                recipient_id=created_by,
                data={"batch_id": batch_id, "invoice_count": len(submitted_rows)},
            )

        return result

    def _generate_irns(
        self,
        rows: list[dict[str, Any]],
        service_id: str,
        result: FinalizeResult,
    ) -> None:
        """Generate IRN for each invoice row, mutating rows in-place."""
        for idx, row in enumerate(rows):
            invoice_number = row.get("invoice_number", "")
            issue_date = row.get("issue_date", "")

            # Clean invoice_number for IRN (remove non-alnum chars)
            clean_number = "".join(c for c in invoice_number if c.isalnum())
            if not clean_number:
                raise IRNGenerationError(
                    f"Row {idx}: invoice_number '{invoice_number}' has no "
                    "alphanumeric characters for IRN generation"
                )

            try:
                irn = generate_irn(clean_number, service_id, issue_date)
                row["irn"] = irn
                result.irn_count += 1
            except IRNError as e:
                raise IRNGenerationError(
                    f"Row {idx}: IRN generation failed: {e}"
                ) from e

    def _generate_qr_codes(
        self,
        rows: list[dict[str, Any]],
        result: FinalizeResult,
    ) -> None:
        """Generate QR code for each invoice row, mutating rows in-place."""
        for idx, row in enumerate(rows):
            try:
                qr_input = QRInput(
                    irn=row.get("irn", ""),
                    invoice_number=row.get("invoice_number", ""),
                    total_amount=row.get("total_amount", 0),
                    issue_date=row.get("issue_date", ""),
                    seller_tin=row.get("seller_tin", ""),
                )
                qr_data = generate_qr_code(qr_input)
                row["qr_code_data"] = qr_data
                result.qr_count += 1
            except QRError as e:
                # QR failure is non-fatal — log warning but continue
                result.warnings.append(f"Row {idx}: QR generation failed: {e}")
                logger.warning("qr_generation_failed", row_idx=idx, error=str(e))
