"""
Tests for access_control DB operations in config_db.py.

Tests cover:
    - get_access_control (filtered + unfiltered)
    - check_access (exact match, wildcard, default deny)
    - get_allowed_resources
    - set_access_control (insert + upsert)
    - delete_access_control
"""

import pytest

from src.database.config_db import ConfigDatabase


@pytest.fixture
def db_with_acl(config_db):
    """Config DB with access_control table and seed data."""
    with config_db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_control (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name   TEXT NOT NULL,
                resource_type  TEXT NOT NULL,
                resource_key   TEXT NOT NULL,
                access_level   TEXT NOT NULL DEFAULT 'read',
                description    TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                UNIQUE(service_name, resource_type, resource_key)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_access_control_service
            ON access_control(service_name)
        """)
        conn.commit()

    # Seed canonical rules
    config_db.set_access_control(
        "relay", "transforma_module", "qr_generator", "read",
        "Relay gets QR module"
    )
    config_db.set_access_control(
        "relay", "transforma_module", "service_keys", "read",
        "Relay gets FIRS service keys"
    )
    config_db.set_access_control(
        "core", "transforma_module", "*", "read",
        "Core gets all Transforma modules"
    )
    config_db.set_access_control(
        "sdk", "transforma_module", "none", "none",
        "SDK has no Transforma access"
    )

    return config_db


class TestGetAccessControl:
    def test_get_all_for_service(self, db_with_acl):
        entries = db_with_acl.get_access_control("relay")
        assert len(entries) == 2
        keys = {e["resource_key"] for e in entries}
        assert keys == {"qr_generator", "service_keys"}

    def test_get_filtered_by_resource_type(self, db_with_acl):
        entries = db_with_acl.get_access_control("relay", "transforma_module")
        assert len(entries) == 2

    def test_get_empty(self, db_with_acl):
        entries = db_with_acl.get_access_control("unknown_service")
        assert entries == []


class TestCheckAccess:
    def test_exact_match(self, db_with_acl):
        level = db_with_acl.check_access("relay", "transforma_module", "qr_generator")
        assert level == "read"

    def test_wildcard_match(self, db_with_acl):
        level = db_with_acl.check_access("core", "transforma_module", "qr_generator")
        assert level == "read"  # Core has wildcard *

    def test_wildcard_any_key(self, db_with_acl):
        level = db_with_acl.check_access("core", "transforma_module", "anything")
        assert level == "read"

    def test_no_rule_returns_none(self, db_with_acl):
        level = db_with_acl.check_access("unknown", "transforma_module", "qr_generator")
        assert level == "none"

    def test_explicit_none_access(self, db_with_acl):
        level = db_with_acl.check_access("sdk", "transforma_module", "none")
        assert level == "none"

    def test_relay_denied_unknown_module(self, db_with_acl):
        level = db_with_acl.check_access("relay", "transforma_module", "secret_module")
        assert level == "none"


class TestGetAllowedResources:
    def test_relay_allowed(self, db_with_acl):
        allowed = db_with_acl.get_allowed_resources("relay", "transforma_module")
        assert set(allowed) == {"qr_generator", "service_keys"}

    def test_core_wildcard(self, db_with_acl):
        allowed = db_with_acl.get_allowed_resources("core", "transforma_module")
        assert allowed == ["*"]

    def test_sdk_none_excluded(self, db_with_acl):
        allowed = db_with_acl.get_allowed_resources("sdk", "transforma_module")
        assert allowed == []

    def test_unknown_service(self, db_with_acl):
        allowed = db_with_acl.get_allowed_resources("ghost", "transforma_module")
        assert allowed == []


class TestSetAccessControl:
    def test_insert(self, db_with_acl):
        db_with_acl.set_access_control(
            "relay", "endpoint", "/api/blobs/write", "write"
        )
        level = db_with_acl.check_access("relay", "endpoint", "/api/blobs/write")
        assert level == "write"

    def test_upsert(self, db_with_acl):
        db_with_acl.set_access_control(
            "relay", "transforma_module", "qr_generator", "execute"
        )
        level = db_with_acl.check_access("relay", "transforma_module", "qr_generator")
        assert level == "execute"


class TestDeleteAccessControl:
    def test_delete_existing(self, db_with_acl):
        count = db_with_acl.delete_access_control(
            "relay", "transforma_module", "qr_generator"
        )
        assert count == 1

        level = db_with_acl.check_access("relay", "transforma_module", "qr_generator")
        assert level == "none"

    def test_delete_nonexistent(self, db_with_acl):
        count = db_with_acl.delete_access_control("ghost", "x", "y")
        assert count == 0
