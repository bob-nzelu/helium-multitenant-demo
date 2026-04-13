"""
Tests for WS6 NotificationService — create, read, list, SSE push.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.observability.notification_service import NotificationService


class TestNotificationSend:
    @pytest.mark.asyncio
    async def test_send_creates_notification(self, mock_pool, mock_sse_manager):
        service = NotificationService(mock_pool, mock_sse_manager)
        nid = await service.send(
            company_id="comp-1",
            notification_type="business",
            category="upload_complete",
            title="Upload processed",
            body="File xyz.csv processed successfully.",
        )
        assert nid is not None
        mock_pool._mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_pushes_sse_event(self, mock_pool, mock_sse_manager):
        service = NotificationService(mock_pool, mock_sse_manager)
        await service.send(
            company_id="comp-1",
            notification_type="system",
            category="error",
            title="Pipeline failed",
            body="Processing error occurred.",
            priority="urgent",
        )
        mock_sse_manager.publish.assert_called_once()
        event = mock_sse_manager.publish.call_args[0][0]
        assert event.event_type == "notification.created"

    @pytest.mark.asyncio
    async def test_send_fire_and_forget_on_db_error(self, mock_pool, mock_sse_manager):
        mock_pool._mock_conn.execute.side_effect = Exception("DB down")
        service = NotificationService(mock_pool, mock_sse_manager)
        nid = await service.send(
            company_id="comp-1",
            notification_type="system",
            category="error",
            title="Test",
            body="Test body",
        )
        assert nid is None  # Should not raise


class TestNotificationMarkRead:
    @pytest.mark.asyncio
    async def test_mark_read_success(self, mock_pool, mock_sse_manager):
        # First execute returns None (not already read), second inserts
        mock_pool._mock_cursor.fetchone = AsyncMock(return_value=None)
        service = NotificationService(mock_pool, mock_sse_manager)
        success = await service.mark_read("notif-1", "user-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_mark_read_idempotent(self, mock_pool, mock_sse_manager):
        # Already read
        mock_pool._mock_cursor.fetchone = AsyncMock(return_value=(1,))
        service = NotificationService(mock_pool, mock_sse_manager)
        success = await service.mark_read("notif-1", "user-1")
        assert success is True


class TestNotificationList:
    @pytest.mark.asyncio
    async def test_list_for_user_empty(self, mock_pool, mock_sse_manager):
        mock_pool._mock_cursor.fetchone = AsyncMock(return_value=(0,))
        mock_pool._mock_cursor.fetchall = AsyncMock(return_value=[])
        service = NotificationService(mock_pool, mock_sse_manager)
        notifications, total = await service.list_for_user("comp-1", "user-1")
        assert notifications == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_handles_db_error(self, mock_pool, mock_sse_manager):
        mock_pool._mock_conn.execute.side_effect = Exception("DB down")
        service = NotificationService(mock_pool, mock_sse_manager)
        notifications, total = await service.list_for_user("comp-1", "user-1")
        assert notifications == []
        assert total == 0


class TestNotificationUnreadCount:
    @pytest.mark.asyncio
    async def test_unread_count(self, mock_pool, mock_sse_manager):
        mock_pool._mock_cursor.fetchone = AsyncMock(return_value=(5,))
        service = NotificationService(mock_pool, mock_sse_manager)
        count = await service.unread_count("comp-1", "user-1")
        assert count == 5

    @pytest.mark.asyncio
    async def test_unread_count_handles_error(self, mock_pool, mock_sse_manager):
        mock_pool._mock_conn.execute.side_effect = Exception("DB down")
        service = NotificationService(mock_pool, mock_sse_manager)
        count = await service.unread_count("comp-1", "user-1")
        assert count == 0


class TestNotificationCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_expired(self, mock_pool, mock_sse_manager):
        mock_pool._mock_cursor.rowcount = 3
        service = NotificationService(mock_pool, mock_sse_manager)
        deleted = await service.cleanup_expired()
        assert deleted == 3
