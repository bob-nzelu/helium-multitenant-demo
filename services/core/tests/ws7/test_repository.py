"""
Tests for WS7 Report Repository — CRUD on core.reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.reports import repository


class TestCreateReport:
    @pytest.mark.asyncio
    async def test_create_inserts_and_returns(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.description = [
            type("Desc", (), {"name": n})()
            for n in [
                "report_id", "company_id", "report_type", "format",
                "status", "title", "blob_uuid", "filters",
                "generated_at", "expires_at", "size_bytes",
                "error_message", "generated_by", "created_at", "updated_at",
            ]
        ]
        cursor.fetchone = AsyncMock(return_value=(
            "rpt-1", "comp-1", "compliance", "pdf",
            "generating", "Compliance Report", None, None,
            None, None, None,
            None, "user-1", datetime.now(timezone.utc), datetime.now(timezone.utc),
        ))
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.create_report(
            conn,
            report_id="rpt-1",
            company_id="comp-1",
            report_type="compliance",
            format="pdf",
            generated_by="user-1",
            title="Compliance Report",
        )
        assert result["report_id"] == "rpt-1"
        assert result["status"] == "generating"
        conn.execute.assert_called_once()


class TestGetReport:
    @pytest.mark.asyncio
    async def test_found(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.description = [
            type("Desc", (), {"name": "report_id"})(),
            type("Desc", (), {"name": "status"})(),
        ]
        cursor.fetchone = AsyncMock(return_value=("rpt-1", "ready"))
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.get_report(conn, "rpt-1")
        assert result is not None
        assert result["report_id"] == "rpt-1"
        assert result["status"] == "ready"

    @pytest.mark.asyncio
    async def test_not_found(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.get_report(conn, "nonexistent")
        assert result is None


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_to_ready(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.description = [
            type("Desc", (), {"name": "report_id"})(),
            type("Desc", (), {"name": "status"})(),
            type("Desc", (), {"name": "blob_uuid"})(),
        ]
        cursor.fetchone = AsyncMock(return_value=("rpt-1", "ready", "blob-1"))
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.update_status(
            conn, "rpt-1", status="ready", blob_uuid="blob-1", size_bytes=12345,
        )
        assert result["status"] == "ready"
        assert result["blob_uuid"] == "blob-1"

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.update_status(conn, "nonexistent", status="failed")
        assert result is None


class TestListReports:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.description = []
        cursor.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.list_reports(conn, "comp-1")
        assert result == []


class TestCountReports:
    @pytest.mark.asyncio
    async def test_count(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(5,))
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.count_reports(conn, "comp-1")
        assert result == 5


class TestCleanupExpired:
    @pytest.mark.asyncio
    async def test_cleanup_returns_count(self):
        conn = AsyncMock()
        cursor = AsyncMock()
        cursor.rowcount = 3
        conn.execute = AsyncMock(return_value=cursor)

        result = await repository.cleanup_expired(conn)
        assert result == 3
