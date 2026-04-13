"""
Tests for WS6 AuditLogger — fire-and-forget audit trail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability.audit_logger import AuditLogger, _json_or_none


# ── Helper tests ───────────────────────────────────────────────────────────


class TestJsonOrNone:
    def test_none_returns_none(self):
        assert _json_or_none(None) is None

    def test_dict_returns_json_string(self):
        result = _json_or_none({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_empty_dict_returns_json(self):
        assert _json_or_none({}) == "{}"


# ── AuditLogger.log() tests ───────────────────────────────────────────────


class TestAuditLoggerLog:
    @pytest.mark.asyncio
    async def test_log_single_event(self, mock_pool):
        logger = AuditLogger(mock_pool)
        audit_id = await logger.log(
            event_type="invoice.created",
            entity_type="invoice",
            action="CREATE",
            company_id="comp-1",
            entity_id="inv-1",
        )
        assert audit_id is not None
        mock_pool._mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_with_before_after_state(self, mock_pool):
        logger = AuditLogger(mock_pool)
        audit_id = await logger.log(
            event_type="invoice.updated",
            entity_type="invoice",
            action="UPDATE",
            company_id="comp-1",
            entity_id="inv-1",
            before_state={"total": "100"},
            after_state={"total": "200"},
            changed_fields=["total"],
        )
        assert audit_id is not None
        # Verify the execute call included JSON-serialized states
        call_args = mock_pool._mock_conn.execute.call_args
        params = call_args[0][1]
        assert '"total"' in params[9]  # before_state JSON
        assert '"200"' in params[10]  # after_state JSON
        assert params[11] == ["total"]  # changed_fields

    @pytest.mark.asyncio
    async def test_log_with_metadata(self, mock_pool):
        logger = AuditLogger(mock_pool)
        audit_id = await logger.log(
            event_type="system.startup",
            entity_type="system",
            action="PROCESS",
            metadata={"version": "1.0.0"},
        )
        assert audit_id is not None

    @pytest.mark.asyncio
    async def test_log_fire_and_forget_on_db_error(self, mock_pool):
        """Audit failures must not propagate."""
        mock_pool._mock_conn.execute.side_effect = Exception("DB down")
        logger = AuditLogger(mock_pool)

        # Should NOT raise
        audit_id = await logger.log(
            event_type="invoice.created",
            entity_type="invoice",
            action="CREATE",
        )
        assert audit_id is None

    @pytest.mark.asyncio
    async def test_log_generates_uuid7(self, mock_pool):
        logger = AuditLogger(mock_pool)
        audit_id = await logger.log(
            event_type="test",
            entity_type="system",
            action="PROCESS",
        )
        assert audit_id is not None
        # UUIDv7 is 36 chars with dashes
        assert len(audit_id) == 36
        assert audit_id.count("-") == 4


# ── AuditLogger.log_batch() tests ─────────────────────────────────────────


class TestAuditLoggerBatch:
    @pytest.mark.asyncio
    async def test_log_batch_events(self, mock_pool):
        logger = AuditLogger(mock_pool)
        events = [
            {
                "event_type": "finalize.started",
                "entity_type": "queue",
                "action": "FINALIZE",
                "company_id": "comp-1",
            },
            {
                "event_type": "finalize.completed",
                "entity_type": "queue",
                "action": "FINALIZE",
                "company_id": "comp-1",
            },
        ]
        ids = await logger.log_batch(events)
        assert len(ids) == 2
        mock_pool._mock_conn.executemany.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_batch_empty_list(self, mock_pool):
        logger = AuditLogger(mock_pool)
        ids = await logger.log_batch([])
        assert ids == []
        mock_pool._mock_conn.executemany.assert_not_called()

    @pytest.mark.asyncio
    async def test_log_batch_fire_and_forget_on_error(self, mock_pool):
        mock_pool._mock_conn.executemany.side_effect = Exception("DB down")
        logger = AuditLogger(mock_pool)
        ids = await logger.log_batch([
            {"event_type": "test", "entity_type": "system", "action": "PROCESS"},
        ])
        assert ids == []
