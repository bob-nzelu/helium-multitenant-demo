"""
Tests for ``src.observability.counters`` (CSSV1 R9 — alarming-counter
infrastructure for the ``/metrics`` route).

Covers ``inc()`` / ``get_all()`` / ``reset()`` semantics, label
normalisation (sorted-tuple keying so ``{a:1,b:2}`` and ``{b:2,a:1}``
collapse to one counter), and the integration with the ``/metrics``
exposition.
"""

from __future__ import annotations

import pytest

from src.observability import counters


@pytest.fixture(autouse=True)
def _reset_counters():
    """Each test starts with a clean counter dict."""
    counters.reset()
    yield
    counters.reset()


class TestInc:
    def test_inc_no_labels_starts_at_one(self):
        counters.inc("test_counter")
        out = list(counters.get_all())
        assert out == [("test_counter", {}, 1)]

    def test_inc_multiple_increments_to_n(self):
        for _ in range(5):
            counters.inc("test_counter")
        out = list(counters.get_all())
        assert out == [("test_counter", {}, 5)]

    def test_inc_with_labels_keyed_by_label_combo(self):
        counters.inc("ep", labels={"endpoint": "introspect"})
        counters.inc("ep", labels={"endpoint": "introspect"})
        counters.inc("ep", labels={"endpoint": "fetch_config"})

        result = {(name, tuple(sorted(lbls.items()))): v for name, lbls, v in counters.get_all()}
        assert result[("ep", (("endpoint", "introspect"),))] == 2
        assert result[("ep", (("endpoint", "fetch_config"),))] == 1

    def test_label_order_does_not_matter(self):
        """``{a:1,b:2}`` and ``{b:2,a:1}`` must collapse to the same counter."""
        counters.inc("c", labels={"a": "1", "b": "2"})
        counters.inc("c", labels={"b": "2", "a": "1"})
        out = list(counters.get_all())
        assert len(out) == 1
        _, _, value = out[0]
        assert value == 2

    def test_different_label_value_creates_new_counter(self):
        counters.inc("c", labels={"endpoint": "a"})
        counters.inc("c", labels={"endpoint": "b"})
        out = list(counters.get_all())
        assert len(out) == 2

    def test_label_values_coerced_to_str(self):
        counters.inc("c", labels={"code": 200})
        out = list(counters.get_all())
        assert out == [("c", {"code": "200"}, 1)]


class TestGetAll:
    def test_empty_returns_empty_iterable(self):
        assert list(counters.get_all()) == []

    def test_returns_snapshot_independent_of_internal_state(self):
        """Mutating the dict mid-iteration must not crash callers."""
        counters.inc("a")
        snapshot = list(counters.get_all())
        counters.inc("b")
        # snapshot still references the original 1-tuple
        assert snapshot == [("a", {}, 1)]

    def test_labels_returned_as_dict_not_tuple(self):
        counters.inc("c", labels={"k": "v"})
        for _, lbls, _ in counters.get_all():
            assert isinstance(lbls, dict)
            assert lbls == {"k": "v"}


class TestReset:
    def test_reset_wipes_all(self):
        counters.inc("a")
        counters.inc("b", labels={"x": "y"})
        counters.reset()
        assert list(counters.get_all()) == []


class TestCounterHelpRegistry:
    """The pre-declared ``COUNTER_HELP`` shapes the ``/metrics`` exposition."""

    def test_includes_bearer_removed(self):
        assert "relay_bearer_removed_received_total" in counters.COUNTER_HELP

    def test_includes_amqp_publish(self):
        assert "relay_amqp_publish_total" in counters.COUNTER_HELP

    def test_includes_lock_acquire(self):
        assert "relay_lock_acquire_duration_seconds" in counters.COUNTER_HELP

    def test_includes_status_orchestration(self):
        assert "relay_status_orchestration_duration_seconds" in counters.COUNTER_HELP

    def test_help_entries_are_2_tuples(self):
        for name, entry in counters.COUNTER_HELP.items():
            assert isinstance(entry, tuple) and len(entry) == 2, (
                f"COUNTER_HELP[{name!r}] must be (help_text, type_str)"
            )
            help_text, type_str = entry
            assert isinstance(help_text, str) and help_text
            assert type_str in {"counter", "gauge", "histogram"}, (
                f"unknown Prometheus type {type_str!r} for {name}"
            )
