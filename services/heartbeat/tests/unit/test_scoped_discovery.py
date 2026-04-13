"""
Tests for Phase D — Scoped Discovery + Platform Filtering.

Tests cover:
    - registry_handler.discover_all() with/without caller_service
    - registry_handler.discover_service() with/without caller_service
    - registry_handler._filter_endpoints_by_access()
    - platform_handler.get_transforma_config() with/without caller_service
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.handlers.registry_handler import (
    discover_all,
    discover_service,
    _filter_endpoints_by_access,
)
from src.handlers.platform_handler import get_transforma_config


class TestFilterEndpointsByAccess:
    def test_wildcard_returns_all(self):
        endpoints = [
            {"path": "/api/blobs/write"},
            {"path": "/api/audit/log"},
        ]
        with patch("src.database.config_db.get_config_database") as mock_db:
            mock_db.return_value.get_allowed_resources.return_value = ["*"]
            result = _filter_endpoints_by_access("core", endpoints)
        assert len(result) == 2

    def test_specific_paths_filter(self):
        endpoints = [
            {"path": "/api/blobs/write"},
            {"path": "/api/audit/log"},
            {"path": "/api/secret/thing"},
        ]
        with patch("src.database.config_db.get_config_database") as mock_db:
            mock_db.return_value.get_allowed_resources.return_value = [
                "/api/blobs/write",
                "/api/audit/log",
            ]
            result = _filter_endpoints_by_access("relay", endpoints)
        assert len(result) == 2
        paths = {ep["path"] for ep in result}
        assert "/api/secret/thing" not in paths

    def test_no_rules_returns_all(self):
        """Fail open when no rules defined for caller."""
        endpoints = [{"path": "/api/test"}]
        with patch("src.database.config_db.get_config_database") as mock_db:
            mock_db.return_value.get_allowed_resources.return_value = []
            result = _filter_endpoints_by_access("unknown", endpoints)
        assert len(result) == 1

    def test_db_exception_returns_all(self):
        """Fail open when access_control table doesn't exist."""
        endpoints = [{"path": "/api/test"}]
        with patch("src.database.config_db.get_config_database") as mock_db:
            mock_db.side_effect = RuntimeError("not initialized")
            result = _filter_endpoints_by_access("relay", endpoints)
        assert len(result) == 1


class TestDiscoverAll:
    @pytest.mark.asyncio
    async def test_no_caller(self):
        with patch("src.handlers.registry_handler.get_registry_database") as mock_db:
            mock_db.return_value.get_all_instances.return_value = [
                {"service_name": "core", "service_instance_id": "core-001"},
            ]
            mock_db.return_value.get_full_catalog.return_value = [
                {"path": "/health", "service_name": "core"},
            ]

            result = await discover_all(caller_service=None)

        assert "core" in result["services"]
        assert len(result["catalog"]) == 1

    @pytest.mark.asyncio
    async def test_with_caller_filters(self):
        with patch("src.handlers.registry_handler.get_registry_database") as mock_db:
            mock_db.return_value.get_all_instances.return_value = [
                {"service_name": "core", "service_instance_id": "core-001"},
            ]
            mock_db.return_value.get_full_catalog.return_value = [
                {"path": "/api/blobs/write"},
                {"path": "/api/secret"},
            ]

            with patch(
                "src.handlers.registry_handler._filter_endpoints_by_access"
            ) as mock_filter:
                mock_filter.return_value = [{"path": "/api/blobs/write"}]
                result = await discover_all(caller_service="relay")

        assert len(result["catalog"]) == 1


class TestDiscoverService:
    @pytest.mark.asyncio
    async def test_service_found(self):
        with patch("src.handlers.registry_handler.get_registry_database") as mock_db:
            mock_db.return_value.get_instances_by_service.return_value = [
                {"service_instance_id": "core-001"},
            ]
            mock_db.return_value.get_endpoint_catalog.return_value = [
                {"path": "/health", "method": "GET"},
            ]

            result = await discover_service("core")

        assert result["service_name"] == "core"
        assert len(result["instances"]) == 1

    @pytest.mark.asyncio
    async def test_service_not_found(self):
        with patch("src.handlers.registry_handler.get_registry_database") as mock_db:
            mock_db.return_value.get_instances_by_service.return_value = []

            result = await discover_service("ghost")

        assert result["instances"] == []
        assert result["endpoints"] == []


class TestGetTransformaConfig:
    @pytest.mark.asyncio
    async def test_no_caller_returns_all(self):
        with patch("src.handlers.platform_handler.get_config_database") as mock_db:
            mock_db.return_value.get_all_config.return_value = [
                {
                    "config_key": "qr_generator",
                    "config_value": json.dumps({
                        "module_name": "qr_generator",
                        "source_code": "def generate(): pass",
                        "version": "1.0",
                    }),
                },
                {
                    "config_key": "tax_calculator",
                    "config_value": json.dumps({
                        "module_name": "tax_calculator",
                        "source_code": "def calc(): pass",
                        "version": "1.0",
                    }),
                },
                {
                    "config_key": "service_keys",
                    "config_value": json.dumps({
                        "firs_public_key_pem": "KEY",
                        "csid": "CSID",
                    }),
                },
            ]
            mock_db.return_value.get_allowed_resources.return_value = []

            result = await get_transforma_config(caller_service=None)

        assert len(result["modules"]) == 2
        assert result["service_keys"]["csid"] == "CSID"

    @pytest.mark.asyncio
    async def test_relay_gets_filtered(self):
        with patch("src.handlers.platform_handler.get_config_database") as mock_db:
            mock_db.return_value.get_all_config.return_value = [
                {
                    "config_key": "qr_generator",
                    "config_value": json.dumps({"module_name": "qr_generator"}),
                },
                {
                    "config_key": "tax_calculator",
                    "config_value": json.dumps({"module_name": "tax_calculator"}),
                },
                {
                    "config_key": "service_keys",
                    "config_value": json.dumps({"csid": "CSID"}),
                },
            ]
            mock_db.return_value.get_allowed_resources.return_value = [
                "qr_generator",
                "service_keys",
            ]

            result = await get_transforma_config(caller_service="relay")

        # Relay should only get qr_generator + service_keys
        assert len(result["modules"]) == 1
        assert result["modules"][0]["module_name"] == "qr_generator"
        assert result["service_keys"]["csid"] == "CSID"

    @pytest.mark.asyncio
    async def test_core_wildcard_gets_all(self):
        with patch("src.handlers.platform_handler.get_config_database") as mock_db:
            mock_db.return_value.get_all_config.return_value = [
                {
                    "config_key": "qr_generator",
                    "config_value": json.dumps({"module_name": "qr_generator"}),
                },
                {
                    "config_key": "tax_calculator",
                    "config_value": json.dumps({"module_name": "tax_calculator"}),
                },
            ]
            mock_db.return_value.get_allowed_resources.return_value = ["*"]

            result = await get_transforma_config(caller_service="core")

        assert len(result["modules"]) == 2

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self):
        with patch("src.handlers.platform_handler.get_config_database") as mock_db:
            mock_db.return_value.get_all_config.return_value = [
                {
                    "config_key": "bad_entry",
                    "config_value": "not valid json{{{",
                },
                {
                    "config_key": "good_entry",
                    "config_value": json.dumps({"module_name": "good"}),
                },
            ]
            mock_db.return_value.get_allowed_resources.return_value = []

            result = await get_transforma_config(caller_service=None)

        assert len(result["modules"]) == 1
        assert result["modules"][0]["module_name"] == "good"

    @pytest.mark.asyncio
    async def test_access_control_failure_fails_open(self):
        """When access_control lookup fails, return all entries."""
        with patch("src.handlers.platform_handler.get_config_database") as mock_db:
            mock_db.return_value.get_all_config.return_value = [
                {
                    "config_key": "module_a",
                    "config_value": json.dumps({"module_name": "a"}),
                },
            ]
            mock_db.return_value.get_allowed_resources.side_effect = Exception(
                "table missing"
            )

            result = await get_transforma_config(caller_service="relay")

        # Should fail open — return everything
        assert len(result["modules"]) == 1
