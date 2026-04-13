"""
Tests for managed_services DB operations in registry.py.

Tests cover:
    - register_managed_service (insert + upsert)
    - get_managed_services (all + auto_start_only)
    - get_managed_service (single lookup)
    - update_service_status (with/without PID)
    - mark_service_started / mark_service_stopped
    - increment_restart_count / reset_restart_count
    - get_startup_order
"""

import json
import pytest

from src.database.registry import RegistryDatabase


@pytest.fixture
def db_with_managed(registry_db):
    """Registry DB with managed_services table seeded with test data."""
    # The managed_services table is created by migration 004.
    # We need to create it manually for testing since the schema SQL
    # may not include it yet.
    with registry_db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS managed_services (
                service_name        TEXT PRIMARY KEY,
                instance_id         TEXT NOT NULL,
                executable_path     TEXT NOT NULL,
                working_directory   TEXT NOT NULL,
                arguments           TEXT,
                environment         TEXT,
                startup_priority    INTEGER NOT NULL DEFAULT 1,
                auto_start          BOOLEAN NOT NULL DEFAULT 1,
                auto_restart        BOOLEAN NOT NULL DEFAULT 1,
                restart_policy      TEXT NOT NULL DEFAULT 'immediate_3',
                health_endpoint     TEXT,
                current_pid         INTEGER,
                current_status      TEXT NOT NULL DEFAULT 'stopped',
                last_started_at     TEXT,
                last_stopped_at     TEXT,
                restart_count       INTEGER NOT NULL DEFAULT 0,
                last_restart_at     TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
        """)
        conn.commit()
    return registry_db


class TestRegisterManagedService:
    def test_insert(self, db_with_managed):
        result = db_with_managed.register_managed_service(
            service_name="core",
            instance_id="core-001",
            executable_path="/usr/bin/python",
            working_directory="/opt/helium/core",
            arguments=["-m", "uvicorn", "main:app"],
            environment={"PORT": "8000"},
            startup_priority=1,
            health_endpoint="http://localhost:8000/health",
        )
        assert result == 1

        svc = db_with_managed.get_managed_service("core")
        assert svc is not None
        assert svc["service_name"] == "core"
        assert svc["startup_priority"] == 1
        assert json.loads(svc["arguments"]) == ["-m", "uvicorn", "main:app"]
        assert json.loads(svc["environment"]) == {"PORT": "8000"}
        assert svc["current_status"] == "stopped"
        assert svc["restart_count"] == 0

    def test_upsert(self, db_with_managed):
        db_with_managed.register_managed_service(
            service_name="core",
            instance_id="core-001",
            executable_path="/usr/bin/python",
            working_directory="/opt/old",
            startup_priority=1,
        )
        db_with_managed.register_managed_service(
            service_name="core",
            instance_id="core-002",
            executable_path="/usr/bin/python3",
            working_directory="/opt/new",
            startup_priority=2,
        )

        svc = db_with_managed.get_managed_service("core")
        assert svc["instance_id"] == "core-002"
        assert svc["working_directory"] == "/opt/new"
        assert svc["startup_priority"] == 2

    def test_null_optional_fields(self, db_with_managed):
        db_with_managed.register_managed_service(
            service_name="edge",
            instance_id="edge-001",
            executable_path="/usr/bin/python",
            working_directory="/opt",
        )
        svc = db_with_managed.get_managed_service("edge")
        assert svc["arguments"] is None
        assert svc["environment"] is None
        assert svc["health_endpoint"] is None


class TestGetManagedServices:
    def test_all_services(self, db_with_managed):
        db_with_managed.register_managed_service(
            "core", "c1", "/py", "/opt", startup_priority=1
        )
        db_with_managed.register_managed_service(
            "relay", "r1", "/py", "/opt", startup_priority=2
        )
        db_with_managed.register_managed_service(
            "edge", "e1", "/py", "/opt", startup_priority=3, auto_start=False
        )

        all_svcs = db_with_managed.get_managed_services()
        assert len(all_svcs) == 3
        # Should be ordered by priority
        assert all_svcs[0]["service_name"] == "core"
        assert all_svcs[1]["service_name"] == "relay"
        assert all_svcs[2]["service_name"] == "edge"

    def test_auto_start_only(self, db_with_managed):
        db_with_managed.register_managed_service(
            "core", "c1", "/py", "/opt", startup_priority=1, auto_start=True
        )
        db_with_managed.register_managed_service(
            "edge", "e1", "/py", "/opt", startup_priority=3, auto_start=False
        )

        auto_svcs = db_with_managed.get_managed_services(auto_start_only=True)
        assert len(auto_svcs) == 1
        assert auto_svcs[0]["service_name"] == "core"


class TestServiceStatus:
    def test_update_status_with_pid(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")
        db_with_managed.update_service_status("core", "healthy", pid=1234)

        svc = db_with_managed.get_managed_service("core")
        assert svc["current_status"] == "healthy"
        assert svc["current_pid"] == 1234

    def test_update_status_without_pid(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")
        db_with_managed.update_service_status("core", "unhealthy")

        svc = db_with_managed.get_managed_service("core")
        assert svc["current_status"] == "unhealthy"

    def test_mark_started(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")
        db_with_managed.mark_service_started("core", 5678)

        svc = db_with_managed.get_managed_service("core")
        assert svc["current_status"] == "starting"
        assert svc["current_pid"] == 5678
        assert svc["last_started_at"] is not None

    def test_mark_stopped(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")
        db_with_managed.mark_service_started("core", 5678)
        db_with_managed.mark_service_stopped("core")

        svc = db_with_managed.get_managed_service("core")
        assert svc["current_status"] == "stopped"
        assert svc["current_pid"] is None
        assert svc["last_stopped_at"] is not None


class TestRestartCount:
    def test_increment(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")

        db_with_managed.increment_restart_count("core")
        svc = db_with_managed.get_managed_service("core")
        assert svc["restart_count"] == 1
        assert svc["last_restart_at"] is not None

        db_with_managed.increment_restart_count("core")
        svc = db_with_managed.get_managed_service("core")
        assert svc["restart_count"] == 2

    def test_reset(self, db_with_managed):
        db_with_managed.register_managed_service("core", "c1", "/py", "/opt")
        db_with_managed.increment_restart_count("core")
        db_with_managed.increment_restart_count("core")
        db_with_managed.reset_restart_count("core")

        svc = db_with_managed.get_managed_service("core")
        assert svc["restart_count"] == 0


class TestStartupOrder:
    def test_ordered_by_priority(self, db_with_managed):
        db_with_managed.register_managed_service(
            "edge", "e1", "/py", "/opt", startup_priority=3
        )
        db_with_managed.register_managed_service(
            "core", "c1", "/py", "/opt", startup_priority=1
        )
        db_with_managed.register_managed_service(
            "relay", "r1", "/py", "/opt", startup_priority=2
        )
        db_with_managed.register_managed_service(
            "manual", "m1", "/py", "/opt", startup_priority=0, auto_start=False
        )

        order = db_with_managed.get_startup_order()
        # Only auto_start=True services
        assert len(order) == 3
        assert order[0]["service_name"] == "core"
        assert order[1]["service_name"] == "relay"
        assert order[2]["service_name"] == "edge"
