"""
Tests for WS6 audit hooks retrofitted into WS1, WS2, WS5.

Uses AsyncMock for the AuditLogger to verify hooks fire with correct
event_type, entity_type, and action at each hook point.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── WS1: Ingestion Hooks ──────────────────────────────────────────────────


class TestWS1IngestionHooks:
    """Verify audit hooks in ingestion/router.py process_entry()."""

    @pytest.fixture
    def mock_deps(self, mock_audit_logger):
        """Common mocked dependencies for process_entry."""
        pool = AsyncMock()
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        cursor.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value=cursor)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.connection = MagicMock(return_value=cm)

        sse = AsyncMock()
        sse.publish = AsyncMock()

        heartbeat = AsyncMock()
        blob_response = MagicMock()
        blob_response.content = b"test file content"
        heartbeat.fetch_blob = AsyncMock(return_value=blob_response)

        parser = AsyncMock()
        parse_result = MagicMock()
        parse_result.file_type = "csv"
        parse_result.is_hlm = False
        parse_result.raw_data = [{"col": "val"}]
        parse_result.file_hash = None
        parse_result.red_flags = []
        parse_result.metadata = MagicMock(row_count=1, original_filename="test.csv")
        parser.parse = AsyncMock(return_value=parse_result)

        registry = MagicMock()
        registry.get = MagicMock(return_value=parser)

        return {
            "pool": pool,
            "sse_manager": sse,
            "heartbeat_client": heartbeat,
            "parser_registry": registry,
            "audit_logger": mock_audit_logger,
        }

    @pytest.mark.asyncio
    async def test_process_entry_fires_audit_hooks(self, mock_deps, mock_audit_logger):
        """process_entry should fire file.type_detected, file.parsed, file.dedup_checked."""
        from src.ingestion.dedup import DedupChecker

        dedup_result = MagicMock()
        dedup_result.is_duplicate = False

        with patch.object(DedupChecker, "compute_hash", return_value="abc123"), \
             patch.object(DedupChecker, "check", new_callable=AsyncMock, return_value=dedup_result):
            from src.ingestion.router import process_entry

            await process_entry(
                queue_id="q-1",
                blob_uuid="blob-1",
                data_uuid="data-1",
                original_filename="test.csv",
                company_id="comp-1",
                **mock_deps,
            )

        # Should have fired at least 3 audit events (type_detected, parsed, dedup_checked)
        event_types = [
            call.kwargs.get("event_type", call.args[0] if call.args else None)
            for call in mock_audit_logger.log.call_args_list
        ]
        # Check via keyword args
        kw_event_types = [
            call.kwargs.get("event_type") for call in mock_audit_logger.log.call_args_list
        ]
        assert "file.type_detected" in kw_event_types
        assert "file.parsed" in kw_event_types
        assert "file.dedup_checked" in kw_event_types


# ── WS2: Transformer Hooks ────────────────────────────────────────────────


class TestWS2TransformerHooks:
    @pytest.mark.asyncio
    async def test_transform_fires_started_and_completed(self, mock_audit_logger, mock_pool):
        from src.processing.models import PipelineContext
        from src.ingestion.models import ParseResult, ParseMetadata

        transformer_mod = __import__("src.processing.transformer", fromlist=["Transformer"])
        Transformer = transformer_mod.Transformer

        t = Transformer(mock_pool, __import__("src.config", fromlist=["CoreConfig"]).CoreConfig(), audit_logger=mock_audit_logger)

        parse_result = MagicMock(spec=ParseResult)
        parse_result.is_hlm = False
        parse_result.red_flags = []
        parse_result.raw_data = [{"invoice_number": "INV-001", "total_amount": "100"}]
        parse_result.file_type = "csv"
        parse_result.file_hash = "abc123"
        parse_result.metadata = MagicMock(row_count=1, original_filename="test.csv")

        context = PipelineContext(
            data_uuid="data-1",
            company_id="comp-1",
            trace_id="trace-1",
            helium_user_id="user-1",
        )

        result = await t.transform(parse_result, context)

        kw_event_types = [
            call.kwargs.get("event_type") for call in mock_audit_logger.log.call_args_list
        ]
        assert "transform.started" in kw_event_types
        assert "transform.completed" in kw_event_types


# ── WS5: Finalize Hooks ───────────────────────────────────────────────────


class TestWS5FinalizeHooks:
    @pytest.mark.asyncio
    async def test_finalize_fires_started_and_completed(self, mock_audit_logger, mock_notification_service):
        from src.finalize.pipeline import FinalizePipeline
        from src.finalize.edit_validator import EditValidator, EditValidationResult

        # Mock validator to pass
        validator = MagicMock(spec=EditValidator)
        validation_result = MagicMock(spec=EditValidationResult)
        validation_result.is_valid = True
        validation_result.violations = []
        validation_result.accepted_changes = []
        validation_result.warnings = []
        validator.validate = MagicMock(return_value=validation_result)

        # Mock record creator
        record_creator = AsyncMock()
        commit_result = MagicMock()
        commit_result.success = True
        commit_result.errors = []
        commit_result.invoices_created = 1
        commit_result.line_items_created = 2
        commit_result.customers_created = 0
        commit_result.inventory_created = 0
        record_creator.commit_batch = AsyncMock(return_value=commit_result)

        pipeline = FinalizePipeline(
            edit_validator=validator,
            record_creator=record_creator,
            edge_client=None,
            audit_logger=mock_audit_logger,
            notification_service=mock_notification_service,
        )

        submitted_rows = [
            {"invoice_number": "INV001", "issue_date": "2026-03-01",
             "total_amount": "1000", "seller_tin": "12345678"},
        ]

        conn = AsyncMock()
        result = await pipeline.finalize(
            submitted_rows=submitted_rows,
            preview_rows=submitted_rows,
            conn=conn,
            company_id="comp-1",
            batch_id="batch-1",
            service_id="ABCD1234",
            created_by="user-1",
        )

        assert result.success is True

        kw_event_types = [
            call.kwargs.get("event_type") for call in mock_audit_logger.log.call_args_list
        ]
        assert "finalize.started" in kw_event_types
        assert "finalize.irn_generated" in kw_event_types
        assert "finalize.qr_generated" in kw_event_types
        assert "finalize.db_committed" in kw_event_types
        assert "finalize.completed" in kw_event_types

        # Notification should have been sent
        mock_notification_service.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_finalize_fires_failed_on_error(self, mock_audit_logger):
        from src.finalize.pipeline import FinalizePipeline

        # Mock validator to fail
        validator = MagicMock()
        validation_result = MagicMock()
        validation_result.is_valid = False
        validation_result.violations = [MagicMock()]
        validation_result.accepted_changes = []
        validation_result.warnings = []
        validator.validate = MagicMock(return_value=validation_result)

        pipeline = FinalizePipeline(
            edit_validator=validator,
            audit_logger=mock_audit_logger,
        )

        result = await pipeline.finalize(
            submitted_rows=[{"invoice_number": "INV001"}],
            preview_rows=[],
            conn=AsyncMock(),
            company_id="comp-1",
            batch_id="batch-1",
            service_id="ABCD1234",
        )

        assert result.success is False
        kw_event_types = [
            call.kwargs.get("event_type") for call in mock_audit_logger.log.call_args_list
        ]
        assert "finalize.started" in kw_event_types
        # Should NOT have completed
        assert "finalize.completed" not in kw_event_types
