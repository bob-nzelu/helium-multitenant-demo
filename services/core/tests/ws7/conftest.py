"""
WS7 Test Fixtures

Provides mock pool, heartbeat_client, notification_service, SSE manager,
and audit logger for unit testing Reports & Statistics components.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import CoreConfig


@pytest.fixture
def config():
    """CoreConfig with test defaults."""
    return CoreConfig()


@pytest.fixture
def mock_pool():
    """Mock AsyncConnectionPool with async context manager support."""
    pool = AsyncMock()
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.rowcount = 0
    cursor.description = []
    conn.execute = AsyncMock(return_value=cursor)

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
    """Mock AuditLogger."""
    logger = AsyncMock()
    logger.log = AsyncMock(return_value="mock-audit-id")
    return logger


@pytest.fixture
def mock_notification_service():
    """Mock NotificationService."""
    service = AsyncMock()
    service.send = AsyncMock(return_value="mock-notif-id")
    return service


@pytest.fixture
def mock_heartbeat_client():
    """Mock HeartBeatBlobClient."""
    client = AsyncMock()
    client.upload_blob = AsyncMock(return_value="mock-blob-uuid")

    blob_response = MagicMock()
    blob_response.data = b"mock-blob-content"
    client.fetch_blob = AsyncMock(return_value=blob_response)
    return client
