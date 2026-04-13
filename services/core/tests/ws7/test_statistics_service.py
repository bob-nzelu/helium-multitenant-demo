"""
Tests for WS7 StatisticsService — period resolution, cache, section handlers.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.reports.models import StatisticsPeriod
from src.reports.statistics_service import (
    StatisticsCache,
    resolve_period,
)


class TestResolvePeriod:
    def test_custom_dates_override(self):
        d_from, d_to = resolve_period(
            StatisticsPeriod.MONTH,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 6, 30),
        )
        assert d_from == date(2026, 1, 1)
        assert d_to == date(2026, 6, 30)

    def test_today(self):
        d_from, d_to = resolve_period(StatisticsPeriod.TODAY)
        assert d_from == d_to == date.today()

    def test_week_starts_monday(self):
        d_from, d_to = resolve_period(StatisticsPeriod.WEEK)
        assert d_from.weekday() == 0  # Monday
        assert d_to == date.today()

    def test_month_starts_first(self):
        d_from, d_to = resolve_period(StatisticsPeriod.MONTH)
        assert d_from.day == 1
        assert d_from.month == date.today().month
        assert d_to == date.today()

    def test_quarter(self):
        d_from, d_to = resolve_period(StatisticsPeriod.QUARTER)
        assert d_from.day == 1
        assert d_from.month in (1, 4, 7, 10)
        assert d_to == date.today()

    def test_year(self):
        d_from, d_to = resolve_period(StatisticsPeriod.YEAR)
        assert d_from == date(date.today().year, 1, 1)

    def test_all(self):
        d_from, d_to = resolve_period(StatisticsPeriod.ALL)
        assert d_from == date(2020, 1, 1)


class TestStatisticsCache:
    def test_set_and_get(self):
        cache = StatisticsCache(ttl=60)
        cache.set("key1", {"value": 42})
        result = cache.get("key1")
        assert result == {"value": 42}

    def test_cache_miss(self):
        cache = StatisticsCache(ttl=60)
        assert cache.get("nonexistent") is None

    def test_cache_expiry(self):
        cache = StatisticsCache(ttl=0)  # Immediate expiry
        cache.set("key1", {"value": 42})
        # TTL=0, so monotonic time diff > 0 → expired
        import time
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_invalidate_clears_all(self):
        cache = StatisticsCache(ttl=60)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.invalidate()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_overwrite(self):
        cache = StatisticsCache(ttl=60)
        cache.set("key", {"v": 1})
        cache.set("key", {"v": 2})
        assert cache.get("key") == {"v": 2}
