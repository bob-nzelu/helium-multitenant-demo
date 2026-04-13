"""Unit tests for WS2 config extensions."""

import os

import pytest

from src.config import CoreConfig


class TestWS2Config:
    def test_defaults(self):
        c = CoreConfig()
        assert c.script_timeout_seconds == 30.0
        assert c.default_due_date_days == 30
        assert c.his_base_url == "http://localhost:8500"
        assert c.his_timeout == 10.0
        assert c.his_max_retries == 3
        assert c.his_concurrent_invoices == 10
        assert c.circuit_failure_threshold == 5
        assert c.circuit_recovery_timeout == 60.0
        assert c.circuit_success_threshold == 2
        assert c.fuzzy_match_threshold == 0.85
        assert c.fuzzy_auto_select_threshold == 0.95
        assert c.max_fuzzy_candidates == 50

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("CORE_FUZZY_MATCH_THRESHOLD", "0.80")
        monkeypatch.setenv("CORE_CIRCUIT_FAILURE_THRESHOLD", "10")
        monkeypatch.setenv("CORE_HIS_TIMEOUT", "15.0")

        c = CoreConfig.from_env()
        assert c.fuzzy_match_threshold == 0.80
        assert c.circuit_failure_threshold == 10
        assert c.his_timeout == 15.0

    def test_from_env_defaults_preserved(self):
        c = CoreConfig.from_env()
        assert c.fuzzy_match_threshold == 0.85
        assert c.script_timeout_seconds == 30.0
