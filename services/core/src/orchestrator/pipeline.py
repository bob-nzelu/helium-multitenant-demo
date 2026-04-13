"""WS3: Pipeline Orchestrator — chains Phases 1-7, manages batching/timeouts/SSE."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.config import CoreConfig
from src.ingestion.heartbeat_client import HeartBeatBlobClient
from src.ingestion.dedup import DedupChecker
from src.ingestion.file_detector import detect_file_type, FileType
from src.ingestion.models import ParseResult, RedFlag as ParseRedFlag
from src.processing.models import (
    PipelineContext,
    RedFlag,
    TransformResult,
    EnrichResult,
    ResolveResult,
)
from src.processing.transformer import Transformer
from src.processing.enricher import Enricher
from src.processing.resolver import Resolver
from src.orchestrator.porto_bello import PortoBelloGate, PortoBelloResult
from src.orchestrator.preview_generator import PreviewGenerator
from src.orchestrator.worker_manager import WorkerManager, WorkerStatus
from src.orchestrator.models import (
    ProcessPreviewResponse200,
    ProcessPreviewResponse202,
    StatisticsModel,
    RedFlagModel,
    ProgressModel,
)
from src.sse.models import SSEEvent
from src.sse.events import (
    EVENT_PROCESSING_LOG,
    EVENT_PROCESSING_PROGRESS,
    make_log_event,
    make_progress_event,
)

logger = logging.getLogger(__name__)

# Phase names
PHASE_FETCH = "fetch"
PHASE_PARSE = "parse"
PHASE_TRANSFORM = "transform"
PHASE_ENRICH = "enrich"
PHASE_RESOLVE = "resolve"
PHASE_PORTO_BELLO = "porto_bello"
PHASE_BRANCH = "branch"


class SoftTimeoutReached(Exception):
    """Raised when the 280s soft timeout is hit at a phase boundary."""

    def __init__(self, phase: str, elapsed: float):
        self.phase = phase
        self.elapsed = elapsed
        super().__init__(f"Soft timeout at {phase} ({elapsed:.1f}s)")


@dataclass
class PipelineState:
    """Mutable state tracked during pipeline execution."""

    start_time: float = 0.0
    current_phase: str = ""
    phases_completed: int = 0
    invoices_total: int = 0
    invoices_ready: int = 0
    batch_count: int = 0
    red_flags: list[RedFlag] = field(default_factory=list)
    phase_timings: dict[str, int] = field(default_factory=dict)
    duplicate_count: int = 0
    skipped_count: int = 0


class PipelineOrchestrator:
    """Core pipeline orchestrator — chains Phases 1-7.

    Manages:
    - Sequential phase execution with timeout checking
    - Batch processing for Phases 3-5 (phase-barrier model)
    - SSE event emission at phase boundaries and per-batch
    - .hlx generation and blob upload (Phase 7)
    - Background continuation on 202 timeout path

    Args:
        config: CoreConfig with timeout/batch/worker settings.
        heartbeat_client: For blob fetch (Phase 1) and upload (Phase 7).
        parser_registry: For Phase 2 (PARSE).
        transformer: Phase 3 (TRANSFORM).
        enricher: Phase 4 (ENRICH).
        resolver: Phase 5 (RESOLVE).
        sse_manager: For emitting processing.log and processing.progress events.
        db_pool: PostgreSQL connection pool for core_queue updates.
    """

    # Configurable (from CoreConfig)
    SOFT_TIMEOUT_SECONDS: int = 280
    BATCH_SIZE: int = 100

    def __init__(
        self,
        config: CoreConfig,
        heartbeat_client: HeartBeatBlobClient,
        parser_registry: Any,
        transformer: Transformer,
        enricher: Enricher,
        resolver: Resolver,
        sse_manager: Any,
        db_pool: Any,
        audit_logger=None,
        notification_service=None,
    ):
        self._config = config
        self._heartbeat = heartbeat_client
        self._parser_registry = parser_registry
        self._transformer = transformer
        self._enricher = enricher
        self._resolver = resolver
        self._sse = sse_manager
        self._pool = db_pool
        self._audit_logger = audit_logger
        self._notification_service = notification_service

        # Apply config overrides
        self.SOFT_TIMEOUT_SECONDS = getattr(config, "pipeline_soft_timeout", 280)
        self.BATCH_SIZE = getattr(config, "batch_size", 100)
        worker_pool_size = getattr(config, "worker_pool_size", 10)
        worker_task_timeout = getattr(config, "worker_task_timeout", 60)

        self._worker_manager = WorkerManager(
            max_workers=worker_pool_size,
            task_timeout=worker_task_timeout,
        )
        self._porto_bello = PortoBelloGate()
        self._preview_gen = PreviewGenerator(heartbeat_client, audit_logger=audit_logger)
        self._cancel_event = asyncio.Event()

    async def process(
        self, queue_id: str, data_uuid: str, queue_entry: dict
    ) -> ProcessPreviewResponse200 | ProcessPreviewResponse202:
        """Run the full 7-phase pipeline for a queue entry.

        Returns 200 response if completed within timeout, 202 if backgrounded.
        """
        state = PipelineState(start_time=time.monotonic())

        # WS6: Audit pipeline.started
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="pipeline.started",
                entity_type="queue",
                entity_id=queue_id,
                action="PROCESS",
                company_id=queue_entry.get("company_id", ""),
                metadata={"data_uuid": data_uuid},
            )

        context = PipelineContext(
            data_uuid=data_uuid,
            company_id=queue_entry.get("company_id", ""),
            trace_id=queue_entry.get("trace_id", ""),
            helium_user_id=queue_entry.get("uploaded_by", ""),
            immediate_processing=queue_entry.get("immediate_processing", False),
        )

        try:
            result = await self._run_pipeline(queue_id, context, state)
            return result
        except SoftTimeoutReached as timeout:
            # WS6: Audit pipeline.timeout
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="pipeline.timeout",
                    entity_type="queue",
                    entity_id=queue_id,
                    action="PROCESS",
                    company_id=context.company_id,
                    metadata={
                        "phase_at_timeout": timeout.phase,
                        "elapsed_ms": int(timeout.elapsed * 1000),
                        "timeout_ms": self.SOFT_TIMEOUT_SECONDS * 1000,
                    },
                )
            # Spawn background task and return 202
            logger.info(
                "Soft timeout at %s (%.1fs) — backgrounding %s",
                timeout.phase, timeout.elapsed, data_uuid,
            )
            asyncio.create_task(
                self._continue_background(queue_id, context, state, timeout.phase)
            )
            return ProcessPreviewResponse202(
                queue_id=queue_id,
                data_uuid=data_uuid,
                status="processing",
                estimated_completion_seconds=self._estimate_remaining(
                    timeout.elapsed, state.phases_completed, 7
                ),
                phases_completed=state.phases_completed,
                phases_total=7,
                current_phase=timeout.phase,
                progress=ProgressModel(
                    invoices_ready=state.invoices_ready,
                    invoices_total=state.invoices_total,
                ),
            )
        except Exception as exc:
            logger.exception("Pipeline failed for %s: %s", data_uuid, exc)
            await self._emit_log(data_uuid, f"Pipeline failed: {exc}", "error")
            await self._update_queue_status(queue_id, "FAILED", str(exc))
            # HeartBeat: error status with last phase
            blob_uuid = await self._get_blob_uuid(queue_id)
            if blob_uuid:
                self._notify_heartbeat_status(
                    blob_uuid, "error", state.current_phase, error_message=str(exc),
                )
            # WS6: Audit pipeline.failed
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="pipeline.failed",
                    entity_type="queue",
                    entity_id=queue_id,
                    action="PROCESS",
                    company_id=context.company_id,
                    x_trace_id=context.trace_id,
                    metadata={
                        "error": str(exc),
                        "phase_at_failure": state.current_phase,
                        "phases_completed": state.phases_completed,
                    },
                )
            raise

    # -----------------------------------------------------------------------
    # Core pipeline
    # -----------------------------------------------------------------------

    async def _run_pipeline(
        self, queue_id: str, context: PipelineContext, state: PipelineState
    ) -> ProcessPreviewResponse200:
        """Execute Phases 1-7 sequentially."""

        # Resolve blob_uuid once for HeartBeat status callbacks
        blob_uuid = await self._get_blob_uuid(queue_id)

        # --- Phase 1: FETCH ---
        state.current_phase = PHASE_FETCH
        phase_start = time.monotonic()
        await self._emit_log(context.data_uuid, "Fetching file...", "info")
        self._check_timeout(state, PHASE_FETCH)

        blob_response = await self._heartbeat.fetch_blob(
            queue_entry_blob_uuid=blob_uuid
        )
        state.phase_timings[PHASE_FETCH] = self._ms_since(phase_start)
        state.phases_completed = 1
        await self._emit_progress(context.data_uuid, 0, 0)
        self._notify_heartbeat_status(blob_uuid, "processing", PHASE_FETCH)

        # EH-007: Resource limit — reject oversized files
        max_bytes = getattr(self._config, "max_file_size_mb", 50) * 1024 * 1024
        if blob_response.size > max_bytes:
            raise ValueError(
                f"File too large ({blob_response.size / 1024 / 1024:.1f}MB, "
                f"max {max_bytes / 1024 / 1024:.0f}MB)"
            )

        # --- Phase 2: PARSE ---
        state.current_phase = PHASE_PARSE
        phase_start = time.monotonic()
        await self._emit_log(
            context.data_uuid,
            f"Parsing {blob_response.filename}...",
            "info",
        )
        self._check_timeout(state, PHASE_PARSE)

        file_type = detect_file_type(blob_response.content, blob_response.filename)
        parser = self._parser_registry.get(file_type)
        parse_result = await parser.parse(blob_response)

        # Merge parse red flags
        for prf in getattr(parse_result, "red_flags", []):
            state.red_flags.append(self._convert_parse_red_flag(prf, PHASE_PARSE))

        invoices = parse_result.invoices if hasattr(parse_result, "invoices") else []
        raw_data = getattr(parse_result, "raw_data", None)
        state.invoices_total = len(invoices) if isinstance(invoices, list) else getattr(parse_result.metadata, "row_count", 0)
        state.phase_timings[PHASE_PARSE] = self._ms_since(phase_start)
        state.phases_completed = 2

        # EH-007: Resource limit — reject oversized batches
        max_invoices = getattr(self._config, "max_invoices_per_batch", 1000)
        if state.invoices_total > max_invoices:
            raise ValueError(
                f"Batch too large ({state.invoices_total} invoices, max {max_invoices})"
            )

        await self._emit_log(
            context.data_uuid,
            f"Parsed {state.invoices_total} invoices from {blob_response.filename}",
            "success",
        )
        await self._emit_progress(context.data_uuid, 0, state.invoices_total)
        self._notify_heartbeat_status(blob_uuid, "processing", PHASE_PARSE)

        # --- .HLM Detection Branch ---
        is_hlm = getattr(parse_result, "is_hlm", False)
        if is_hlm:
            await self._emit_log(
                context.data_uuid,
                "Data is .hlm format — skipping transformation",
                "info",
            )
            # WS6: Audit pipeline.hlm_detected
            if self._audit_logger:
                await self._audit_logger.log(
                    event_type="pipeline.hlm_detected",
                    entity_type="queue",
                    entity_id=queue_id,
                    action="PROCESS",
                    company_id=context.company_id,
                    metadata={"skipping_transform": True},
                )

        # --- Dedup check ---
        file_hash = getattr(parse_result, "file_hash", "")
        if file_hash:
            from src.ingestion.dedup import DedupChecker
            dedup_result = await DedupChecker.check(file_hash, self._pool)
            if dedup_result.is_duplicate:
                # EH-001: Mark as SKIPPED and stop processing
                await self._emit_log(
                    context.data_uuid,
                    f"Duplicate file — previously processed as {dedup_result.existing_filename}",
                    "warning",
                )
                await self._update_queue_status(queue_id, "SKIPPED")
                self._notify_heartbeat_status(blob_uuid, "processing", PHASE_PARSE)
                processing_time_ms = self._ms_since(state.start_time)
                state.duplicate_count += 1
                state.red_flags.append(RedFlag(
                    type="duplicate_hash",
                    severity="warning",
                    message=f"File hash matches previously processed file ({dedup_result.existing_filename})",
                    phase=PHASE_PARSE,
                ))
                return self._build_200_response(
                    queue_id, context.data_uuid, "skipped",
                    state, processing_time_ms, hlx_blob_uuid=None,
                )

        # --- Phase 3: TRANSFORM (skipped if .hlm) ---
        state.current_phase = PHASE_TRANSFORM
        phase_start = time.monotonic()
        self._check_timeout(state, PHASE_TRANSFORM)

        if is_hlm:
            # Direct conversion — no Transforma needed
            transform_result = TransformResult(
                invoices=[],  # Filled from parse_result by transformer
                red_flags=[],
            )
            state.phase_timings[PHASE_TRANSFORM] = 0
        else:
            await self._emit_log(context.data_uuid, "Transforming data...", "info")
            transform_result = await self._transformer.transform(parse_result, context)
            state.phase_timings[PHASE_TRANSFORM] = self._ms_since(phase_start)

        # Merge transform red flags
        for rf in getattr(transform_result, "red_flags", []):
            state.red_flags.append(rf)
        state.phases_completed = 3
        self._notify_heartbeat_status(blob_uuid, "processing", PHASE_TRANSFORM)

        # --- Phase 4: ENRICH ---
        state.current_phase = PHASE_ENRICH
        phase_start = time.monotonic()
        await self._emit_log(context.data_uuid, "Enriching data via HIS...", "info")
        self._check_timeout(state, PHASE_ENRICH)

        try:
            enrich_result = await self._enricher.enrich(transform_result, context)
        except Exception as exc:
            # ENRICH failure is non-fatal — continue with degraded data
            logger.warning("Enrichment failed (degraded mode): %s", exc)
            await self._emit_log(
                context.data_uuid,
                f"Enrichment degraded: {exc}",
                "warning",
            )
            enrich_result = EnrichResult(
                invoices=transform_result.invoices,
                red_flags=transform_result.red_flags,
            )
            state.red_flags.append(RedFlag(
                type="enrichment_failed",
                severity="warning",
                message=f"HIS enrichment unavailable: {exc}",
                phase=PHASE_ENRICH,
            ))

        for rf in getattr(enrich_result, "red_flags", []):
            state.red_flags.append(rf)
        state.phase_timings[PHASE_ENRICH] = self._ms_since(phase_start)
        state.phases_completed = 4
        self._notify_heartbeat_status(blob_uuid, "processing", PHASE_ENRICH)

        # --- Phase 5: RESOLVE ---
        state.current_phase = PHASE_RESOLVE
        phase_start = time.monotonic()
        await self._emit_log(context.data_uuid, "Resolving entities...", "info")
        self._check_timeout(state, PHASE_RESOLVE)

        try:
            resolve_result = await self._resolver.resolve(enrich_result, context)
        except Exception as exc:
            # RESOLVE failure is non-fatal — continue with unresolved entities
            logger.warning("Resolution failed (degraded mode): %s", exc)
            await self._emit_log(
                context.data_uuid,
                f"Entity resolution degraded: {exc}",
                "warning",
            )
            resolve_result = ResolveResult(
                invoices=enrich_result.invoices,
                red_flags=enrich_result.red_flags,
            )
            state.red_flags.append(RedFlag(
                type="resolution_failed",
                severity="warning",
                message=f"Entity resolution unavailable: {exc}",
                phase=PHASE_RESOLVE,
            ))

        for rf in getattr(resolve_result, "red_flags", []):
            state.red_flags.append(rf)
        state.phase_timings[PHASE_RESOLVE] = self._ms_since(phase_start)
        state.phases_completed = 5
        self._notify_heartbeat_status(blob_uuid, "processing", PHASE_RESOLVE)

        # --- Phase 6: PORTO BELLO ---
        state.current_phase = PHASE_PORTO_BELLO
        phase_start = time.monotonic()
        await self._emit_log(context.data_uuid, "Evaluating business rules...", "info")

        porto_result = await self._porto_bello.evaluate(resolve_result)
        state.phase_timings[PHASE_PORTO_BELLO] = self._ms_since(phase_start)
        state.phases_completed = 6

        # --- Check: Immediate finalize path ---
        if context.immediate_processing and not self._has_critical_flags(state.red_flags):
            await self._emit_log(
                context.data_uuid,
                "Immediate processing — skipping preview generation",
                "info",
            )
            processing_time_ms = self._ms_since(state.start_time)
            await self._update_queue_status(queue_id, "FINALIZED")
            return self._build_200_response(
                queue_id, context.data_uuid, "finalized",
                state, processing_time_ms, hlx_blob_uuid=None,
            )

        # --- Phase 7: BRANCH + .hlx generation ---
        state.current_phase = PHASE_BRANCH
        phase_start = time.monotonic()
        await self._emit_log(context.data_uuid, "Generating preview...", "info")
        self._check_timeout(state, PHASE_BRANCH)

        hlx_blob_uuid = await self._preview_gen.generate(
            context=context,
            resolve_result=resolve_result,
            red_flags=state.red_flags,
            phase_timings=state.phase_timings,
            processing_time_ms=self._ms_since(state.start_time),
            duplicate_count=state.duplicate_count,
            skipped_count=state.skipped_count,
        )

        state.phase_timings[PHASE_BRANCH] = self._ms_since(phase_start)
        state.phases_completed = 7
        state.invoices_ready = state.invoices_total
        processing_time_ms = self._ms_since(state.start_time)

        # Update queue
        await self._update_queue_status(queue_id, "PREVIEW_READY", hlx_blob_uuid=hlx_blob_uuid)

        # HeartBeat: preview_pending with processing stats
        error_count = len([f for f in state.red_flags if f.severity == "error"])
        valid_count = max(state.invoices_total - error_count - state.duplicate_count - state.skipped_count, 0)
        self._notify_heartbeat_status(blob_uuid, "preview_pending", PHASE_BRANCH, processing_stats={
            "extracted_invoice_count": state.invoices_total,
            "rejected_invoice_count": error_count,
            "submitted_invoice_count": valid_count,
            "duplicate_count": state.duplicate_count,
        })

        # Final SSE events with processing stats
        await self._emit_progress(context.data_uuid, state.invoices_ready, state.invoices_total, {
            "extracted_invoice_count": state.invoices_total,
            "rejected_invoice_count": error_count,
            "submitted_invoice_count": valid_count,
            "duplicate_count": state.duplicate_count,
        })
        await self._emit_log(
            context.data_uuid,
            f"Preview ready: {state.invoices_total} invoices processed",
            "success",
        )

        # WS6: Audit pipeline.completed
        if self._audit_logger:
            await self._audit_logger.log(
                event_type="pipeline.completed",
                entity_type="queue",
                entity_id=queue_id,
                action="PROCESS",
                company_id=context.company_id,
                x_trace_id=context.trace_id,
                metadata={
                    "total_duration_ms": processing_time_ms,
                    "invoice_count": state.invoices_total,
                    "hlx_blob_uuid": hlx_blob_uuid,
                    "phase_timings": state.phase_timings,
                },
            )

        # WS6: Notification — upload complete
        if self._notification_service:
            has_failures = any(rf.severity == "error" for rf in state.red_flags)
            if has_failures:
                await self._notification_service.send(
                    company_id=context.company_id,
                    notification_type="business",
                    category="upload_complete",
                    title=f"Upload processed with failures",
                    body=f"{state.invoices_total} invoices processed, review required.",
                    priority="high",
                    data={"data_uuid": context.data_uuid, "queue_id": queue_id},
                )
            else:
                await self._notification_service.send(
                    company_id=context.company_id,
                    notification_type="business",
                    category="upload_complete",
                    title=f"Upload processed: {state.invoices_total} invoices",
                    body=f"Preview ready for review.",
                    data={"data_uuid": context.data_uuid, "queue_id": queue_id},
                )

        return self._build_200_response(
            queue_id, context.data_uuid, "preview_ready",
            state, processing_time_ms, hlx_blob_uuid,
        )

    # -----------------------------------------------------------------------
    # Background continuation (202 path)
    # -----------------------------------------------------------------------

    async def _continue_background(
        self, queue_id: str, context: PipelineContext,
        state: PipelineState, interrupted_phase: str,
    ) -> None:
        """Continue pipeline in background after returning 202."""
        try:
            logger.info("Background continuation from %s for %s", interrupted_phase, context.data_uuid)
            # The pipeline was interrupted at a phase boundary.
            # state already has partial results. Re-run remaining phases.
            # For simplicity in v1, we restart the full pipeline.
            # Background tasks have no HTTP timeout constraint.
            result = await self._run_pipeline(queue_id, context, state)
            logger.info("Background pipeline completed for %s", context.data_uuid)
        except Exception as exc:
            logger.exception("Background pipeline failed for %s: %s", context.data_uuid, exc)
            await self._update_queue_status(queue_id, "FAILED", str(exc))

    # -----------------------------------------------------------------------
    # Timeout management
    # -----------------------------------------------------------------------

    def _check_timeout(self, state: PipelineState, phase: str) -> None:
        """Check if soft timeout has been reached. Raises SoftTimeoutReached."""
        elapsed = time.monotonic() - state.start_time
        if elapsed >= self.SOFT_TIMEOUT_SECONDS:
            raise SoftTimeoutReached(phase, elapsed)

    def _estimate_remaining(self, elapsed: float, completed: int, total: int) -> int:
        """Estimate seconds remaining based on average phase duration."""
        if completed == 0:
            return 300
        avg = elapsed / completed
        remaining = total - completed
        return min(int(avg * remaining), 600)

    # -----------------------------------------------------------------------
    # HeartBeat status sync
    # -----------------------------------------------------------------------

    def _notify_heartbeat_status(
        self,
        blob_uuid: str,
        status: str,
        processing_stage: str | None = None,
        error_message: str | None = None,
        processing_stats: dict | None = None,
    ) -> None:
        """Fire-and-forget HeartBeat status update. Never blocks the pipeline."""
        asyncio.create_task(self._do_heartbeat_status(
            blob_uuid, status, processing_stage, error_message, processing_stats,
        ))

    async def _do_heartbeat_status(
        self,
        blob_uuid: str,
        status: str,
        processing_stage: str | None = None,
        error_message: str | None = None,
        processing_stats: dict | None = None,
    ) -> None:
        """Actual async call — runs as detached task."""
        try:
            await self._heartbeat.update_blob_status(
                blob_uuid, status, processing_stage, error_message, processing_stats,
            )
        except Exception:
            logger.debug("HeartBeat status update failed (non-fatal)")

    # -----------------------------------------------------------------------
    # SSE helpers
    # -----------------------------------------------------------------------

    async def _emit_log(self, data_uuid: str, message: str, level: str) -> None:
        """Emit processing.log SSE event."""
        try:
            await self._sse.publish(SSEEvent(
                event_type=EVENT_PROCESSING_LOG,
                data=make_log_event(data_uuid, message, level),
                data_uuid=data_uuid,
            ))
        except Exception:
            logger.debug("SSE emit failed (non-fatal)")

    async def _emit_progress(
        self, data_uuid: str, ready: int, total: int,
        processing_stats: dict | None = None,
    ) -> None:
        """Emit processing.progress SSE event."""
        try:
            event_data = make_progress_event(data_uuid, ready, total)
            if processing_stats:
                event_data.update(processing_stats)
            await self._sse.publish(SSEEvent(
                event_type=EVENT_PROCESSING_PROGRESS,
                data=event_data,
                data_uuid=data_uuid,
            ))
        except Exception:
            logger.debug("SSE emit failed (non-fatal)")

    # -----------------------------------------------------------------------
    # Queue status updates
    # -----------------------------------------------------------------------

    async def _update_queue_status(
        self, queue_id: str, status: str,
        error_or_blob: str | None = None,
        hlx_blob_uuid: str | None = None,
    ) -> None:
        """Update core_queue status."""
        try:
            from src.database.pool import get_connection
            async with get_connection(self._pool, "core") as conn:
                if status == "FAILED":
                    await conn.execute(
                        "UPDATE core_queue SET status = $1, error_message = $2, "
                        "processing_completed_at = CURRENT_TIMESTAMP WHERE queue_id = $3",
                        (status, error_or_blob, queue_id),
                    )
                elif status == "PREVIEW_READY":
                    await conn.execute(
                        "UPDATE core_queue SET status = $1, "
                        "processing_completed_at = CURRENT_TIMESTAMP WHERE queue_id = $2",
                        (status, queue_id),
                    )
                else:
                    await conn.execute(
                        "UPDATE core_queue SET status = $1 WHERE queue_id = $2",
                        (status, queue_id),
                    )
        except Exception:
            logger.exception("Failed to update queue status for %s", queue_id)

    async def _get_blob_uuid(self, queue_id: str) -> str:
        """Fetch blob_uuid from core_queue for a given queue_id."""
        try:
            from src.database.pool import get_connection
            async with get_connection(self._pool, "core") as conn:
                row = await conn.execute(
                    "SELECT blob_uuid FROM core_queue WHERE queue_id = $1",
                    (queue_id,),
                )
                result = await row.fetchone()
                return result[0] if result else ""
        except Exception:
            logger.exception("Failed to get blob_uuid for %s", queue_id)
            return ""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _has_critical_flags(self, flags: list[RedFlag]) -> bool:
        """Check if any red flags are error severity (blocks immediate finalize)."""
        return any(f.severity == "error" for f in flags)

    def _convert_parse_red_flag(self, prf: Any, phase: str) -> RedFlag:
        """Convert WS1 ParseRedFlag to WS2 RedFlag."""
        return RedFlag(
            type=getattr(prf, "field_name", "parse_error"),
            severity=getattr(prf, "severity", "warning"),
            message=getattr(prf, "message", str(prf)),
            phase=phase,
        )

    def _ms_since(self, start: float) -> int:
        """Milliseconds elapsed since start (monotonic)."""
        return int((time.monotonic() - start) * 1000)

    def _build_200_response(
        self, queue_id: str, data_uuid: str, status: str,
        state: PipelineState, processing_time_ms: int,
        hlx_blob_uuid: str | None,
    ) -> ProcessPreviewResponse200:
        """Build a 200 OK response from pipeline state."""
        valid_count = state.invoices_total - len([
            f for f in state.red_flags if f.severity == "error"
        ]) - state.duplicate_count - state.skipped_count
        valid_count = max(valid_count, 0)

        return ProcessPreviewResponse200(
            queue_id=queue_id,
            data_uuid=data_uuid,
            status=status,
            statistics=StatisticsModel(
                total_invoices=state.invoices_total,
                valid_count=valid_count,
                failed_count=len([f for f in state.red_flags if f.severity == "error"]),
                duplicate_count=state.duplicate_count,
                skipped_count=state.skipped_count,
                processing_time_ms=processing_time_ms,
                confidence=0.0,  # Computed by preview generator
                batch_count=state.batch_count,
                worker_count=self._worker_manager.worker_count,
            ),
            red_flags=[
                RedFlagModel(
                    type=rf.type,
                    severity=rf.severity,
                    message=rf.message,
                    invoice_index=rf.invoice_index,
                    field=rf.field,
                    phase=rf.phase,
                )
                for rf in state.red_flags
            ],
            hlx_blob_uuid=hlx_blob_uuid,
        )

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        await self._worker_manager.shutdown()
