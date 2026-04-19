"""
HeartBeat Config Database Module

Manages SQLite connection for config.db (configuration, tier limits,
feature flags, and database catalog).

Thread-safe connection management with context managers.

Database: config.db (created by config_schema.sql)
Tables: config_entries, tier_limits, feature_flags, database_catalog (4 tables)

HeartBeat is the SOLE GATEKEEPER — all other services call HeartBeat's
HTTP API. No service reads config.db directly.

This is HeartBeat's 3rd database (alongside blob.db and registry.db).
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


class ConfigDatabase:
    """
    SQLite database connection manager for config.db.

    Thread-safe singleton pattern. Provides helpers for config entry CRUD,
    tier limit lookups, feature flag management, and database catalog operations.
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
            logger.info(f"Creating new config database at {self.db_path}")
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            conn = sqlite3.connect(self.db_path)

            schema_path = os.path.join(
                os.path.dirname(self.db_path),
                "config_schema.sql"
            )
            if os.path.exists(schema_path):
                with open(schema_path, 'r') as f:
                    conn.executescript(f.read())
                logger.info("Config schema created successfully")

            seed_path = os.path.join(
                os.path.dirname(self.db_path),
                "config_seed.sql"
            )
            if os.path.exists(seed_path):
                with open(seed_path, 'r') as f:
                    conn.executescript(f.read())
                logger.info("Config seed data loaded successfully")

            conn.commit()
            conn.close()
        else:
            logger.info(f"Using existing config database at {self.db_path}")

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

    # ── Config Entry Operations ──────────────────────────────────────────

    def get_config_entry(
        self, service_name: str, config_key: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single config entry by service + key."""
        rows = self.execute_query(
            "SELECT * FROM config_entries WHERE service_name = ? AND config_key = ?",
            (service_name, config_key),
        )
        return rows[0] if rows else None

    def get_config_value(
        self, service_name: str, config_key: str
    ) -> Optional[str]:
        """Get just the value for a config entry (convenience)."""
        entry = self.get_config_entry(service_name, config_key)
        return entry["config_value"] if entry else None

    def get_all_config(
        self, service_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all config entries, optionally filtered by service.

        If service_name is None, returns ALL entries (all services).
        """
        if service_name:
            return self.execute_query(
                """SELECT * FROM config_entries
                   WHERE service_name = ?
                   ORDER BY config_key""",
                (service_name,),
            )
        return self.execute_query(
            "SELECT * FROM config_entries ORDER BY service_name, config_key"
        )

    def set_config_entry(
        self,
        service_name: str,
        config_key: str,
        config_value: str,
        value_type: str = "string",
        description: Optional[str] = None,
        updated_by: str = "api",
    ) -> int:
        """
        Create or update a config entry (upsert).

        Respects is_readonly — raises ValueError if entry exists and is read-only.
        Returns lastrowid on insert or rowcount on update.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Check readonly
        existing = self.get_config_entry(service_name, config_key)
        if existing and existing.get("is_readonly"):
            raise ValueError(
                f"Config entry '{service_name}/{config_key}' is read-only"
            )

        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO config_entries
                   (service_name, config_key, config_value, value_type,
                    description, updated_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(service_name, config_key) DO UPDATE SET
                       config_value = excluded.config_value,
                       value_type = excluded.value_type,
                       description = COALESCE(excluded.description, config_entries.description),
                       updated_by = excluded.updated_by,
                       updated_at = excluded.updated_at""",
                (
                    service_name, config_key, config_value, value_type,
                    description, updated_by, now, now,
                ),
            )
            conn.commit()
            return cursor.lastrowid or cursor.rowcount

    def delete_config_entry(
        self, service_name: str, config_key: str
    ) -> int:
        """
        Delete a config entry.

        Respects is_readonly — raises ValueError if entry is read-only.
        Returns rowcount (0 if not found).
        """
        existing = self.get_config_entry(service_name, config_key)
        if existing and existing.get("is_readonly"):
            raise ValueError(
                f"Config entry '{service_name}/{config_key}' is read-only"
            )

        return self.execute_update(
            "DELETE FROM config_entries WHERE service_name = ? AND config_key = ?",
            (service_name, config_key),
        )

    # ── Tier Limit Operations ────────────────────────────────────────────

    def get_tier_limit(
        self, tier: str, limit_key: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single tier limit entry."""
        rows = self.execute_query(
            "SELECT * FROM tier_limits WHERE tier = ? AND limit_key = ?",
            (tier, limit_key),
        )
        return rows[0] if rows else None

    def get_tier_limit_value(
        self, tier: str, limit_key: str
    ) -> Optional[str]:
        """Get just the value for a tier limit (convenience)."""
        entry = self.get_tier_limit(tier, limit_key)
        return entry["limit_value"] if entry else None

    def get_all_limits_for_tier(
        self, tier: str
    ) -> List[Dict[str, Any]]:
        """Get all limits for a given tier."""
        return self.execute_query(
            "SELECT * FROM tier_limits WHERE tier = ? ORDER BY limit_key",
            (tier,),
        )

    def get_all_tier_limits(self) -> List[Dict[str, Any]]:
        """Get all tier limits across all tiers."""
        return self.execute_query(
            "SELECT * FROM tier_limits ORDER BY tier, limit_key"
        )

    def set_tier_limit(
        self,
        tier: str,
        limit_key: str,
        limit_value: str,
        value_type: str = "int",
        description: Optional[str] = None,
    ) -> int:
        """Create or update a tier limit (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO tier_limits
                   (tier, limit_key, limit_value, value_type, description,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tier, limit_key) DO UPDATE SET
                       limit_value = excluded.limit_value,
                       value_type = excluded.value_type,
                       description = COALESCE(excluded.description, tier_limits.description),
                       updated_at = excluded.updated_at""",
                (tier, limit_key, limit_value, value_type, description, now, now),
            )
            conn.commit()
            return cursor.lastrowid or cursor.rowcount

    # ── Feature Flag Operations ──────────────────────────────────────────

    def get_feature_flag(
        self, flag_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single feature flag by name."""
        rows = self.execute_query(
            "SELECT * FROM feature_flags WHERE flag_name = ?",
            (flag_name,),
        )
        return rows[0] if rows else None

    def is_feature_enabled(
        self, flag_name: str, default: bool = False
    ) -> bool:
        """
        Check if a feature flag is enabled.

        Returns default if flag doesn't exist.
        """
        flag = self.get_feature_flag(flag_name)
        if flag is None:
            return default
        return bool(flag["is_enabled"])

    def get_all_feature_flags(
        self, scope: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all feature flags, optionally filtered by scope.
        """
        if scope:
            return self.execute_query(
                "SELECT * FROM feature_flags WHERE scope = ? ORDER BY flag_name",
                (scope,),
            )
        return self.execute_query(
            "SELECT * FROM feature_flags ORDER BY flag_name"
        )

    def set_feature_flag(
        self,
        flag_name: str,
        is_enabled: bool,
        scope: str = "global",
        description: Optional[str] = None,
    ) -> int:
        """Create or update a feature flag (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO feature_flags
                   (flag_name, is_enabled, scope, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(flag_name) DO UPDATE SET
                       is_enabled = excluded.is_enabled,
                       scope = excluded.scope,
                       description = COALESCE(excluded.description, feature_flags.description),
                       updated_at = excluded.updated_at""",
                (flag_name, int(is_enabled), scope, description, now, now),
            )
            conn.commit()
            return cursor.lastrowid or cursor.rowcount

    def delete_feature_flag(self, flag_name: str) -> int:
        """Delete a feature flag. Returns rowcount (0 if not found)."""
        return self.execute_update(
            "DELETE FROM feature_flags WHERE flag_name = ?",
            (flag_name,),
        )

    # ── Access Control Operations ────────────────────────────────────────

    def get_access_control(
        self,
        service_name: str,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get access control entries for a service.

        Optionally filtered by resource_type (e.g., "transforma_module", "endpoint").
        """
        if resource_type:
            return self.execute_query(
                """SELECT * FROM access_control
                   WHERE service_name = ? AND resource_type = ?
                   ORDER BY resource_key""",
                (service_name, resource_type),
            )
        return self.execute_query(
            """SELECT * FROM access_control
               WHERE service_name = ?
               ORDER BY resource_type, resource_key""",
            (service_name,),
        )

    def check_access(
        self,
        service_name: str,
        resource_type: str,
        resource_key: str,
    ) -> str:
        """
        Check access level for a service + resource.

        Supports wildcard '*' resource_key (e.g., core gets all Transforma modules).
        Returns 'none' if no matching rule exists.

        Priority: exact match > wildcard match > 'none' default.
        """
        # Try exact match first
        rows = self.execute_query(
            """SELECT access_level FROM access_control
               WHERE service_name = ? AND resource_type = ? AND resource_key = ?""",
            (service_name, resource_type, resource_key),
        )
        if rows:
            return rows[0]["access_level"]

        # Try wildcard match
        rows = self.execute_query(
            """SELECT access_level FROM access_control
               WHERE service_name = ? AND resource_type = ? AND resource_key = '*'""",
            (service_name, resource_type),
        )
        if rows:
            return rows[0]["access_level"]

        # No rule — deny by default
        return "none"

    def get_allowed_resources(
        self,
        service_name: str,
        resource_type: str,
    ) -> List[str]:
        """
        Get list of resource_keys the service can access for a given type.

        Returns ['*'] for wildcard access, specific keys otherwise.
        Excludes entries with access_level='none'.
        """
        rows = self.execute_query(
            """SELECT resource_key FROM access_control
               WHERE service_name = ? AND resource_type = ?
               AND access_level != 'none'
               ORDER BY resource_key""",
            (service_name, resource_type),
        )
        return [r["resource_key"] for r in rows]

    def set_access_control(
        self,
        service_name: str,
        resource_type: str,
        resource_key: str,
        access_level: str = "read",
        description: Optional[str] = None,
    ) -> int:
        """Create or update an access control entry (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO access_control
                   (service_name, resource_type, resource_key, access_level,
                    description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(service_name, resource_type, resource_key) DO UPDATE SET
                       access_level = excluded.access_level,
                       description = COALESCE(excluded.description, access_control.description),
                       updated_at = excluded.updated_at""",
                (
                    service_name, resource_type, resource_key, access_level,
                    description, now, now,
                ),
            )
            conn.commit()
            return cursor.lastrowid or cursor.rowcount

    def delete_access_control(
        self,
        service_name: str,
        resource_type: str,
        resource_key: str,
    ) -> int:
        """Delete an access control entry. Returns rowcount (0 if not found)."""
        return self.execute_update(
            """DELETE FROM access_control
               WHERE service_name = ? AND resource_type = ? AND resource_key = ?""",
            (service_name, resource_type, resource_key),
        )

    # ── Database Catalog Operations ──────────────────────────────────────

    def register_database(
        self,
        db_logical_name: str,
        db_category: str,
        tenant_id: str,
        owner_service: str,
        db_physical_name: str,
        db_path: str,
        db_engine: str = "sqlite",
        status: str = "active",
        schema_version: Optional[str] = None,
        size_bytes: Optional[int] = None,
        description: Optional[str] = None,
    ) -> int:
        """
        Register or update a database in the catalog (upsert).

        Returns lastrowid on insert or rowcount on update.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO database_catalog
                   (db_logical_name, db_category, tenant_id, owner_service,
                    db_physical_name, db_path, db_engine, status,
                    schema_version, size_bytes, description,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(db_logical_name, tenant_id) DO UPDATE SET
                       db_category = excluded.db_category,
                       owner_service = excluded.owner_service,
                       db_physical_name = excluded.db_physical_name,
                       db_path = excluded.db_path,
                       db_engine = excluded.db_engine,
                       status = excluded.status,
                       schema_version = excluded.schema_version,
                       size_bytes = excluded.size_bytes,
                       description = COALESCE(excluded.description, database_catalog.description),
                       updated_at = excluded.updated_at""",
                (
                    db_logical_name, db_category, tenant_id, owner_service,
                    db_physical_name, db_path, db_engine, status,
                    schema_version, size_bytes, description,
                    now, now,
                ),
            )
            conn.commit()
            return cursor.lastrowid or cursor.rowcount

    def get_database_entry(
        self, db_logical_name: str, tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a specific database catalog entry."""
        rows = self.execute_query(
            "SELECT * FROM database_catalog WHERE db_logical_name = ? AND tenant_id = ?",
            (db_logical_name, tenant_id),
        )
        return rows[0] if rows else None

    def get_databases_by_service(
        self, owner_service: str, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all databases owned by a service."""
        if status:
            return self.execute_query(
                """SELECT * FROM database_catalog
                   WHERE owner_service = ? AND status = ?
                   ORDER BY db_logical_name""",
                (owner_service, status),
            )
        return self.execute_query(
            """SELECT * FROM database_catalog
               WHERE owner_service = ?
               ORDER BY db_logical_name""",
            (owner_service,),
        )

    def get_databases_by_tenant(
        self, tenant_id: str
    ) -> List[Dict[str, Any]]:
        """Get all databases for a tenant."""
        return self.execute_query(
            """SELECT * FROM database_catalog
               WHERE tenant_id = ?
               ORDER BY owner_service, db_logical_name""",
            (tenant_id,),
        )

    def get_full_catalog(
        self, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get the full database catalog, optionally filtered by status."""
        if status:
            return self.execute_query(
                """SELECT * FROM database_catalog
                   WHERE status = ?
                   ORDER BY owner_service, db_logical_name""",
                (status,),
            )
        return self.execute_query(
            """SELECT * FROM database_catalog
               ORDER BY owner_service, db_logical_name"""
        )

    def update_database_status(
        self, db_logical_name: str, tenant_id: str, status: str
    ) -> int:
        """Update the status of a database catalog entry."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE database_catalog
               SET status = ?, updated_at = ?
               WHERE db_logical_name = ? AND tenant_id = ?""",
            (status, now, db_logical_name, tenant_id),
        )

    def update_database_size(
        self, db_logical_name: str, tenant_id: str, size_bytes: int
    ) -> int:
        """Update the recorded size of a database."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE database_catalog
               SET size_bytes = ?, updated_at = ?
               WHERE db_logical_name = ? AND tenant_id = ?""",
            (size_bytes, now, db_logical_name, tenant_id),
        )

    def get_catalog_summary(self) -> Dict[str, Any]:
        """
        Quick summary of the database catalog.

        Returns counts by status, by service, and total size.
        """
        by_status = self.execute_query(
            """SELECT status, COUNT(*) as count
               FROM database_catalog
               GROUP BY status"""
        )
        by_service = self.execute_query(
            """SELECT owner_service, COUNT(*) as count
               FROM database_catalog
               GROUP BY owner_service"""
        )
        total_size = self.execute_query(
            """SELECT COALESCE(SUM(size_bytes), 0) as total_bytes
               FROM database_catalog
               WHERE size_bytes IS NOT NULL"""
        )

        return {
            "total_databases": sum(s["count"] for s in by_status),
            "by_status": {s["status"]: s["count"] for s in by_status},
            "by_service": {s["owner_service"]: s["count"] for s in by_service},
            "total_size_bytes": total_size[0]["total_bytes"] if total_size else 0,
        }


# ── Singleton ──────────────────────────────────────────────────────────

_config_db_instance: Optional[ConfigDatabase] = None


def get_config_database(db_path: Optional[str] = None) -> ConfigDatabase:
    """
    Get singleton ConfigDatabase.

    On first call, db_path is required. Subsequent calls return the singleton.
    """
    global _config_db_instance
    if _config_db_instance is None:
        if db_path is None:
            raise RuntimeError("Config database not initialized. Call with db_path first.")
        _config_db_instance = ConfigDatabase(db_path)
    return _config_db_instance


def set_config_database(db: ConfigDatabase) -> None:
    """Override config database singleton (for testing)."""
    global _config_db_instance
    _config_db_instance = db


def reset_config_database() -> None:
    """Reset config database singleton (for testing)."""
    global _config_db_instance
    _config_db_instance = None
