"""
WS3 Orchestrator — Scheduled Jobs tests.

Covers stale_processing_detector (marks PROCESSING entries > 15 min as FAILED)
and preview_cleanup (marks PREVIEW_READY entries > 7 days as EXPIRED).

`get_connection` is an @asynccontextmanager — it returns an AsyncContextManager
directly when called (not a coroutine). We mock it with a synchronous MagicMock
that returns an object with async __aenter__/__aexit__.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.jobs import (
    PREVIEW_RETENTION_DAYS,
    STALE_PROCESSING_MINUTES,
    preview_cleanup,
    stale_processing_detector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now():
    return datetime.now(timezone.utc)


def _make_conn(rows=None):
    """Build a mock async connection that returns `rows` on fetchall()."""
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock(return_value=cursor)
    return conn


class _AsyncCM:
    """Minimal async context manager wrapping a mock connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


def _ctx_factory(conn):
    """Return a synchronous callable that returns an _AsyncCM each call."""
    def _get_conn(*args, **kwargs):
        return _AsyncCM(conn)
    return _get_conn


# ---------------------------------------------------------------------------
# stale_processing_detector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_detector_no_stale_entries():
    """If no rows returned, no UPDATE is executed."""
    conn = _make_conn(rows=[])

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await stale_processing_detector(MagicMock())

    # execute called once for SELECT, never for UPDATE
    assert conn.execute.call_count == 1


@pytest.mark.asyncio
async def test_stale_detector_marks_stale_as_failed():
    """Entries older than threshold → UPDATE to FAILED."""
    stale_time = _utc_now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 5)
    rows = [("q-001", "d-001", stale_time)]
    conn = _make_conn(rows=rows)

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await stale_processing_detector(MagicMock())

    # SELECT + UPDATE (one per stale entry)
    assert conn.execute.call_count == 2
    update_sql = conn.execute.call_args_list[1][0][0]
    assert "FAILED" in update_sql


@pytest.mark.asyncio
async def test_stale_detector_multiple_stale_entries():
    """Multiple stale entries → one UPDATE per entry."""
    stale_time = _utc_now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 10)
    rows = [
        ("q-001", "d-001", stale_time),
        ("q-002", "d-002", stale_time),
        ("q-003", "d-003", stale_time),
    ]
    conn = _make_conn(rows=rows)

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await stale_processing_detector(MagicMock())

    # 1 SELECT + 3 UPDATEs
    assert conn.execute.call_count == 4


@pytest.mark.asyncio
async def test_stale_detector_emits_sse_when_manager_provided():
    """Stale entries with SSE manager → SSE event emitted per entry."""
    stale_time = _utc_now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 5)
    rows = [("q-001", "d-001", stale_time)]
    conn = _make_conn(rows=rows)

    sse_manager = AsyncMock()
    sse_manager.publish = AsyncMock()

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await stale_processing_detector(MagicMock(), sse_manager=sse_manager)

    sse_manager.publish.assert_called_once()


@pytest.mark.asyncio
async def test_stale_detector_no_sse_when_manager_none():
    """If sse_manager is None, no SSE attempt made and no error raised."""
    stale_time = _utc_now() - timedelta(minutes=STALE_PROCESSING_MINUTES + 5)
    rows = [("q-001", "d-001", stale_time)]
    conn = _make_conn(rows=rows)

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        # Should not raise even without sse_manager
        await stale_processing_detector(MagicMock(), sse_manager=None)


# ---------------------------------------------------------------------------
# preview_cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_cleanup_no_expired_entries():
    """If no rows, no UPDATE is executed."""
    conn = _make_conn(rows=[])

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await preview_cleanup(MagicMock())

    assert conn.execute.call_count == 1  # Only SELECT


@pytest.mark.asyncio
async def test_preview_cleanup_marks_expired():
    """PREVIEW_READY entries older than 7 days → EXPIRED."""
    rows = [("q-001", "d-001")]
    conn = _make_conn(rows=rows)

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await preview_cleanup(MagicMock())

    assert conn.execute.call_count == 2
    update_sql = conn.execute.call_args_list[1][0][0]
    assert "EXPIRED" in update_sql


@pytest.mark.asyncio
async def test_preview_cleanup_multiple_expired():
    """Multiple expired entries → one UPDATE each."""
    rows = [("q-001", "d-001"), ("q-002", "d-002")]
    conn = _make_conn(rows=rows)

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await preview_cleanup(MagicMock())

    assert conn.execute.call_count == 3  # 1 SELECT + 2 UPDATEs


@pytest.mark.asyncio
async def test_preview_cleanup_uses_correct_threshold():
    """Query uses PREVIEW_READY status in the SELECT SQL."""
    conn = _make_conn(rows=[])

    with patch("src.database.pool.get_connection", side_effect=_ctx_factory(conn)):
        await preview_cleanup(MagicMock())

    select_sql = conn.execute.call_args_list[0][0][0]
    assert "PREVIEW_READY" in select_sql
