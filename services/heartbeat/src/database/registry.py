"""
HeartBeat Registry Database Module

Manages SQLite connection for registry.db (service discovery + API keys).
Thread-safe connection management with context managers.

Database: registry.db (created by registry_schema.sql)
Tables: service_instances, service_endpoint_catalog, api_credentials,
        key_rotation_log, service_config (5 tables total)

HeartBeat is the SOLE GATEKEEPER — all other services call HeartBeat's
HTTP API. No service reads registry.db directly.
"""

import json
import sqlite3
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from threading import Lock


logger = logging.getLogger(__name__)


class RegistryDatabase:
    """
    SQLite database connection manager for service registry.

    Thread-safe singleton pattern. Provides helpers for service
    registration, endpoint discovery, credential management, and config.
    """

    _instance = None
    _lock = Lock()

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize_database()

    def _initialize_database(self):
        """Initialize database with schema if it doesn't exist."""
        db_exists = os.path.exists(self.db_path)

        if not db_exists:
            logger.info(f"Creating new registry database at {self.db_path}")
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            conn = sqlite3.connect(self.db_path)

            schema_path = os.path.join(
                os.path.dirname(self.db_path),
                "registry_schema.sql"
            )
            if os.path.exists(schema_path):
                with open(schema_path, 'r') as f:
                    conn.executescript(f.read())
                logger.info("Registry schema created successfully")

            seed_path = os.path.join(
                os.path.dirname(self.db_path),
                "registry_seed.sql"
            )
            if os.path.exists(seed_path):
                with open(seed_path, 'r') as f:
                    conn.executescript(f.read())
                logger.info("Registry seed data loaded successfully")

            conn.commit()
            conn.close()
        else:
            logger.info(f"Using existing registry database at {self.db_path}")

    @contextmanager
    def get_connection(self):
        """Get a thread-safe database connection with dict row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def execute_query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return list of dicts."""
        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def execute_insert(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT and return lastrowid."""
        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid

    def execute_update(self, sql: str, params: tuple = ()) -> int:
        """Execute an UPDATE/DELETE and return rowcount."""
        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    # ── Service Instance Operations ────────────────────────────────────

    def register_instance(
        self,
        instance_id: str,
        service_name: str,
        display_name: str,
        base_url: str,
        health_url: Optional[str] = None,
        websocket_url: Optional[str] = None,
        version: str = "2.0.0",
        tier: str = "test",
    ) -> int:
        """
        Register or update a service instance (upsert).

        Returns rowcount (1 if inserted/updated).
        """
        now = datetime.now(timezone.utc).isoformat()
        sql = """
            INSERT INTO service_instances (
                service_instance_id, service_name, display_name,
                base_url, health_url, websocket_url,
                version, tier, is_active,
                registered_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(service_instance_id) DO UPDATE SET
                service_name = excluded.service_name,
                display_name = excluded.display_name,
                base_url = excluded.base_url,
                health_url = excluded.health_url,
                websocket_url = excluded.websocket_url,
                version = excluded.version,
                tier = excluded.tier,
                is_active = 1,
                updated_at = excluded.updated_at
        """
        with self.get_connection() as conn:
            cursor = conn.execute(sql, (
                instance_id, service_name, display_name,
                base_url, health_url, websocket_url,
                version, tier, now, now,
            ))
            conn.commit()
            return cursor.rowcount

    def register_endpoints(
        self,
        instance_id: str,
        endpoints: List[Dict[str, Any]],
    ) -> int:
        """
        Replace all endpoints for an instance.

        Deletes existing endpoints then inserts new ones (atomic).
        Each endpoint dict: {method, path, description, requires_auth}
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM service_endpoint_catalog WHERE service_instance_id = ?",
                (instance_id,),
            )
            count = 0
            for ep in endpoints:
                conn.execute(
                    """INSERT INTO service_endpoint_catalog
                       (service_instance_id, method, path, description, requires_auth, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        instance_id,
                        ep.get("method", "GET"),
                        ep["path"],
                        ep.get("description", ""),
                        ep.get("requires_auth", True),
                        now,
                    ),
                )
                count += 1
            conn.commit()
            return count

    def get_instances_by_service(
        self, service_name: str, active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Get all instances of a service."""
        if active_only:
            return self.execute_query(
                "SELECT * FROM service_instances WHERE service_name = ? AND is_active = 1",
                (service_name,),
            )
        return self.execute_query(
            "SELECT * FROM service_instances WHERE service_name = ?",
            (service_name,),
        )

    def get_all_instances(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all service instances."""
        if active_only:
            return self.execute_query(
                "SELECT * FROM service_instances WHERE is_active = 1 ORDER BY service_name"
            )
        return self.execute_query(
            "SELECT * FROM service_instances ORDER BY service_name"
        )

    def get_instance(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific instance by ID."""
        rows = self.execute_query(
            "SELECT * FROM service_instances WHERE service_instance_id = ?",
            (instance_id,),
        )
        return rows[0] if rows else None

    def get_endpoint_catalog(
        self, service_name: str
    ) -> List[Dict[str, Any]]:
        """
        Get all endpoints for a service (across all active instances).

        Returns list of {instance_id, base_url, method, path, description}.
        """
        return self.execute_query(
            """SELECT
                   si.service_instance_id, si.base_url, si.service_name,
                   ec.method, ec.path, ec.description, ec.requires_auth
               FROM service_endpoint_catalog ec
               JOIN service_instances si ON ec.service_instance_id = si.service_instance_id
               WHERE si.service_name = ? AND si.is_active = 1
               ORDER BY ec.method, ec.path""",
            (service_name,),
        )

    def get_full_catalog(self) -> List[Dict[str, Any]]:
        """Get the full endpoint catalog for all active services."""
        return self.execute_query(
            """SELECT
                   si.service_instance_id, si.service_name, si.base_url,
                   si.health_url, si.websocket_url, si.version,
                   ec.method, ec.path, ec.description, ec.requires_auth
               FROM service_endpoint_catalog ec
               JOIN service_instances si ON ec.service_instance_id = si.service_instance_id
               WHERE si.is_active = 1
               ORDER BY si.service_name, ec.method, ec.path"""
        )

    def update_health_status(
        self, instance_id: str, status: str
    ) -> int:
        """Update health status for an instance."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE service_instances
               SET last_health_status = ?, last_health_check_at = ?, updated_at = ?
               WHERE service_instance_id = ?""",
            (status, now, now, instance_id),
        )

    def deactivate_instance(self, instance_id: str) -> int:
        """Mark an instance as inactive."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            "UPDATE service_instances SET is_active = 0, updated_at = ? WHERE service_instance_id = ?",
            (now, instance_id),
        )

    # ── Credential Operations ──────────────────────────────────────────

    def get_credential_by_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """Look up credential by API key."""
        rows = self.execute_query(
            "SELECT * FROM api_credentials WHERE api_key = ?",
            (api_key,),
        )
        return rows[0] if rows else None

    def get_credentials_for_service(
        self, service_name: str
    ) -> List[Dict[str, Any]]:
        """Get all credentials for a service (WITHOUT secret hashes)."""
        return self.execute_query(
            """SELECT credential_id, api_key, service_name, issued_to,
                      permissions, status, expires_at, last_used_at,
                      last_rotated_at, created_at, updated_at
               FROM api_credentials
               WHERE service_name = ?
               ORDER BY created_at DESC""",
            (service_name,),
        )

    def create_credential(
        self,
        credential_id: str,
        api_key: str,
        api_secret_hash: str,
        service_name: str,
        issued_to: str,
        permissions: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
    ) -> int:
        """Insert a new credential. Returns lastrowid."""
        now = datetime.now(timezone.utc).isoformat()
        perms_json = json.dumps(permissions) if permissions else "[]"
        return self.execute_insert(
            """INSERT INTO api_credentials (
                   credential_id, api_key, api_secret_hash,
                   service_name, issued_to, permissions, status,
                   expires_at, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (
                credential_id, api_key, api_secret_hash,
                service_name, issued_to, perms_json,
                expires_at, now, now,
            ),
        )

    def rotate_credential(
        self,
        credential_id: str,
        new_api_key: str,
        new_api_secret_hash: str,
    ) -> int:
        """Update a credential with new key and secret hash."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE api_credentials
               SET api_key = ?, api_secret_hash = ?,
                   last_rotated_at = ?, updated_at = ?
               WHERE credential_id = ?""",
            (new_api_key, new_api_secret_hash, now, now, credential_id),
        )

    def update_credential_status(
        self, credential_id: str, status: str
    ) -> int:
        """Update credential status (active/revoked/etc.)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            "UPDATE api_credentials SET status = ?, updated_at = ? WHERE credential_id = ?",
            (status, now, credential_id),
        )

    def update_credential_last_used(self, api_key: str) -> int:
        """Stamp last_used_at on successful authentication."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            "UPDATE api_credentials SET last_used_at = ? WHERE api_key = ?",
            (now, api_key),
        )

    def log_key_rotation(
        self,
        credential_id: str,
        action: str,
        performed_by: str,
        old_key_prefix: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """Record a key lifecycle event in the immutable audit trail."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_insert(
            """INSERT INTO key_rotation_log
               (credential_id, action, performed_by, old_key_prefix, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (credential_id, action, performed_by, old_key_prefix, reason, now),
        )

    # ── Managed Services Operations ──────────────────────────────────

    def register_managed_service(
        self,
        service_name: str,
        instance_id: str,
        executable_path: str,
        working_directory: str,
        arguments: Optional[List[str]] = None,
        environment: Optional[Dict[str, str]] = None,
        startup_priority: int = 1,
        auto_start: bool = True,
        auto_restart: bool = True,
        restart_policy: str = "immediate_3",
        health_endpoint: Optional[str] = None,
    ) -> int:
        """
        Register or update a managed service entry (upsert).

        Typically called by the Installer at install time, or by admin API.
        """
        now = datetime.now(timezone.utc).isoformat()
        args_json = json.dumps(arguments) if arguments else None
        env_json = json.dumps(environment) if environment else None

        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO managed_services (
                       service_name, instance_id, executable_path, working_directory,
                       arguments, environment, startup_priority,
                       auto_start, auto_restart, restart_policy, health_endpoint,
                       current_status, restart_count, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stopped', 0, ?, ?)
                   ON CONFLICT(service_name) DO UPDATE SET
                       instance_id = excluded.instance_id,
                       executable_path = excluded.executable_path,
                       working_directory = excluded.working_directory,
                       arguments = excluded.arguments,
                       environment = excluded.environment,
                       startup_priority = excluded.startup_priority,
                       auto_start = excluded.auto_start,
                       auto_restart = excluded.auto_restart,
                       restart_policy = excluded.restart_policy,
                       health_endpoint = excluded.health_endpoint,
                       updated_at = excluded.updated_at""",
                (
                    service_name, instance_id, executable_path, working_directory,
                    args_json, env_json, startup_priority,
                    int(auto_start), int(auto_restart), restart_policy, health_endpoint,
                    now, now,
                ),
            )
            conn.commit()
            return cursor.rowcount

    def get_managed_services(
        self, auto_start_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all managed services, ordered by startup_priority.

        If auto_start_only=True, only returns services with auto_start=1.
        """
        if auto_start_only:
            return self.execute_query(
                """SELECT * FROM managed_services
                   WHERE auto_start = 1
                   ORDER BY startup_priority, service_name"""
            )
        return self.execute_query(
            "SELECT * FROM managed_services ORDER BY startup_priority, service_name"
        )

    def get_managed_service(
        self, service_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single managed service by name."""
        rows = self.execute_query(
            "SELECT * FROM managed_services WHERE service_name = ?",
            (service_name,),
        )
        return rows[0] if rows else None

    def update_service_status(
        self,
        service_name: str,
        status: str,
        pid: Optional[int] = None,
    ) -> int:
        """
        Update current_status and optionally current_pid.

        Status values: stopped, starting, healthy, degraded, unhealthy, crash_loop, updating.
        """
        now = datetime.now(timezone.utc).isoformat()
        if pid is not None:
            return self.execute_update(
                """UPDATE managed_services
                   SET current_status = ?, current_pid = ?, updated_at = ?
                   WHERE service_name = ?""",
                (status, pid, now, service_name),
            )
        return self.execute_update(
            """UPDATE managed_services
               SET current_status = ?, updated_at = ?
               WHERE service_name = ?""",
            (status, now, service_name),
        )

    def mark_service_started(self, service_name: str, pid: int) -> int:
        """Mark a service as started with its PID."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE managed_services
               SET current_status = 'starting', current_pid = ?,
                   last_started_at = ?, updated_at = ?
               WHERE service_name = ?""",
            (pid, now, now, service_name),
        )

    def mark_service_stopped(self, service_name: str) -> int:
        """Mark a service as stopped (clear PID)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE managed_services
               SET current_status = 'stopped', current_pid = NULL,
                   last_stopped_at = ?, updated_at = ?
               WHERE service_name = ?""",
            (now, now, service_name),
        )

    def increment_restart_count(self, service_name: str) -> int:
        """Increment restart_count and set last_restart_at."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE managed_services
               SET restart_count = restart_count + 1,
                   last_restart_at = ?, updated_at = ?
               WHERE service_name = ?""",
            (now, now, service_name),
        )

    def reset_restart_count(self, service_name: str) -> int:
        """Reset restart_count to 0 (called after 10 min healthy)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE managed_services
               SET restart_count = 0, updated_at = ?
               WHERE service_name = ?""",
            (now, service_name),
        )

    def get_startup_order(self) -> List[Dict[str, Any]]:
        """Get services in startup order (priority ascending, auto_start only)."""
        return self.execute_query(
            """SELECT service_name, instance_id, startup_priority, auto_start,
                      auto_restart, restart_policy, health_endpoint, current_status
               FROM managed_services
               WHERE auto_start = 1
               ORDER BY startup_priority, service_name"""
        )

    # ── Service Config Operations ──────────────────────────────────────

    def get_config(self, service_name: str, config_key: str) -> Optional[str]:
        """Get a single config value for a service."""
        rows = self.execute_query(
            "SELECT config_value FROM service_config WHERE service_name = ? AND config_key = ?",
            (service_name, config_key),
        )
        return rows[0]["config_value"] if rows else None

    def get_all_config(self, service_name: str) -> List[Dict[str, Any]]:
        """Get all config key-values for a service."""
        return self.execute_query(
            """SELECT config_key, config_value, is_encrypted
               FROM service_config
               WHERE service_name = ?
               ORDER BY config_key""",
            (service_name,),
        )

    def set_config(
        self, service_name: str, config_key: str, config_value: str, is_encrypted: bool = False
    ) -> int:
        """Set a config value (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO service_config
                   (service_name, config_key, config_value, is_encrypted, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(service_name, config_key) DO UPDATE SET
                       config_value = excluded.config_value,
                       is_encrypted = excluded.is_encrypted,
                       updated_at = excluded.updated_at""",
                (service_name, config_key, config_value, is_encrypted, now, now),
            )
            conn.commit()
            return cursor.rowcount


# ── Singleton ──────────────────────────────────────────────────────────

_registry_instance: Optional[RegistryDatabase] = None


def get_registry_database(db_path: Optional[str] = None) -> RegistryDatabase:
    """
    Get singleton RegistryDatabase.

    On first call, db_path is required. Subsequent calls return the singleton.
    """
    global _registry_instance
    if _registry_instance is None:
        if db_path is None:
            raise RuntimeError("Registry database not initialized. Call with db_path first.")
        _registry_instance = RegistryDatabase(db_path)
    return _registry_instance


def set_registry_database(db: RegistryDatabase) -> None:
    """Override registry database singleton (for testing)."""
    global _registry_instance
    _registry_instance = db


def reset_registry_database() -> None:
    """Reset registry database singleton (for testing)."""
    global _registry_instance
    _registry_instance = None
