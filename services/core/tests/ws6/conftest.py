"""
WS6 Test Fixtures

Provides mock pool, audit_logger, notification_service, and SSE manager
for unit testing WS6 observability components.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import CoreConfig


@pytest.fixture
def config():
    """CoreConfig with test defaults."""
    return CoreConfig(
        metrics_collect_interval=5,
        notification_ttl_hours=24,
        notification_cleanup_interval=60,
    )


@pytest.fixture
def mock_pool():
    """Mock AsyncConnectionPool with async context manager support."""
    pool = AsyncMock()
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.rowcount = 0
    conn.execute = AsyncMock(return_value=cursor)
    conn.executemany = AsyncMock()

    # pool.connection() is an async context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection = MagicMock(return_value=cm)

    pool._mock_conn = conn
    pool._mock_cursor = cursor
    return pool


@pytest.fixture
def mock_sse_manager():
    """Mock SSEConnectionManager."""
    manager = AsyncMock()
    manager.publish = AsyncMock()
    return manager


@pytest.fixture
def mock_audit_logger():
    """Mock AuditLogger with async methods."""
    logger = AsyncMock()
    logger.log = AsyncMock(return_value="mock-audit-id")
    logger.log_batch = AsyncMock(return_value=["id1", "id2"])
    return logger


@pytest.fixture
def mock_notification_service():
    """Mock NotificationService with async methods."""
    service = AsyncMock()
    service.send = AsyncMock(return_value="mock-notif-id")
    service.mark_read = AsyncMock(return_value=True)
    service.list_for_user = AsyncMock(return_value=([], 0))
    service.unread_count = AsyncMock(return_value=0)
    service.cleanup_expired = AsyncMock(return_value=0)
    return service
