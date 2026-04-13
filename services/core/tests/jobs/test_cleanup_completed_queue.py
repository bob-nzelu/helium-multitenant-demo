"""
Tests for cleanup_completed_queue_entries job.

Verifies:
- Entries older than 24h with PREVIEW_READY/FINALIZED status are deleted
- Entries younger than 24h are preserved
- PENDING/PROCESSING entries are never deleted regardless of age
- FAILED entries are not touched (separate job handles those)
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.jobs.cleanup_completed_queue import cleanup_completed_queue_entries


def _mock_pool_with_rows(rows):
    """Create a mock pool that returns the given rows from DELETE RETURNING."""
    mock_result = AsyncMock()
    mock_result.fetchall = AsyncMock(return_value=rows)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock()

    mock_pool = MagicMock()
    return mock_pool, mock_conn


@pytest.mark.asyncio
class TestCleanupCompletedQueue:

    @patch("src.database.pool.get_connection")
    async def test_deletes_expired_entries(self, mock_get_conn):
        mock_conn = AsyncMock()
        mock_result = AsyncMock()
        mock_result.fetchall = AsyncMock(return_value=[
            ("q-001", "PREVIEW_READY"),
            ("q-002", "FINALIZED"),
            ("q-003", "FINALIZED"),
        ])
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_get_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_get_conn.return_value.__aexit__ = AsyncMock()

        await cleanup_completed_queue_entries(MagicMock())

        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "PREVIEW_READY" in sql
        assert "FINALIZED" in sql
        assert "24 hours" in sql

    @patch("src.database.pool.get_connection")
    async def test_noop_when_nothing_expired(self, mock_get_conn):
        mock_conn = AsyncMock()
        mock_result = AsyncMock()
        mock_result.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_get_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_get_conn.return_value.__aexit__ = AsyncMock()

        await cleanup_completed_queue_entries(MagicMock())

        mock_conn.execute.assert_called_once()

    @patch("src.database.pool.get_connection")
    async def test_sql_never_targets_pending_or_processing(self, mock_get_conn):
        mock_conn = AsyncMock()
        mock_result = AsyncMock()
        mock_result.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_get_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_get_conn.return_value.__aexit__ = AsyncMock()

        await cleanup_completed_queue_entries(MagicMock())

        sql = mock_conn.execute.call_args[0][0]
        assert "PENDING" not in sql
        assert "PROCESSING" not in sql
        assert "FAILED" not in sql

    @patch("src.database.pool.get_connection")
    async def test_survives_db_error(self, mock_get_conn):
        mock_get_conn.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("DB connection lost")
        )
        mock_get_conn.return_value.__aexit__ = AsyncMock()

        # Should not raise
        await cleanup_completed_queue_entries(MagicMock())
