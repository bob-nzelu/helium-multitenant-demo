"""
Tests for WS6 MetricsCollector — background gauge updates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.observability.metrics import entity_count, queue_depth
from src.observability.metrics_collector import MetricsCollector


class TestMetricsCollector:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, mock_pool):
        collector = MetricsCollector(mock_pool, interval=1)
        await collector.start()
        assert collector._running is True
        assert collector._task is not None
        await collector.stop()
        assert collector._running is False

    @pytest.mark.asyncio
    async def test_tick_updates_queue_depth(self, mock_pool):
        """tick() should query queue status and set gauges."""
        mock_pool._mock_cursor.fetchall = AsyncMock(return_value=[
            ("PENDING", 5),
            ("PROCESSING", 2),
        ])
        collector = MetricsCollector(mock_pool, interval=60)
        await collector._tick()
        assert queue_depth.labels(status="PENDING")._value.get() == 5.0
        assert queue_depth.labels(status="PROCESSING")._value.get() == 2.0

    @pytest.mark.asyncio
    async def test_tick_handles_empty_tables(self, mock_pool):
        """tick() should handle empty result sets gracefully."""
        mock_pool._mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_pool._mock_cursor.fetchone = AsyncMock(return_value=(0,))
        collector = MetricsCollector(mock_pool, interval=60)
        # Should not raise
        await collector._tick()

    @pytest.mark.asyncio
    async def test_tick_survives_db_error(self, mock_pool):
        """tick() must not propagate database errors."""
        mock_pool._mock_conn.execute.side_effect = Exception("DB unreachable")
        collector = MetricsCollector(mock_pool, interval=60)
        # Should NOT raise
        await collector._tick()
