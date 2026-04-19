"""
Schema Registry — loads, caches, and serves canonical SQL schemas.

Each registered schema has:
  - name:        Logical name (e.g. "invoices")
  - version:     Semantic version extracted from the SQL file header
  - sql:         Full SQL text (CREATE TABLE + indexes + triggers + views)
  - file_path:   Absolute path to the .sql file on disk

Usage:
    from src.schemas import invoice_schema, get_invoice_schema_sql

    # Get the full SQL text (for creating databases)
    sql = get_invoice_schema_sql()

    # Get version info
    version = get_invoice_schema_version()  # "2.0"

    # Or use the registry directly
    registry = get_schema_registry()
    info = registry.get_schema("invoices")
    sql = info["sql"]
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Default schemas directory: HeartBeat/databases/schemas/
_DEFAULT_SCHEMAS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "databases",
    "schemas",
)


@dataclass
class SchemaInfo:
    """Metadata for a registered schema."""
    name: str
    version: str
    sql: str
    file_path: str
    description: str = ""


class SchemaRegistry:
    """
    Central registry of canonical database schemas.

    Loads .sql files from the schemas directory, extracts version metadata,
    and serves them to callers (API endpoints, SDK sync, Core startup).
    """

    def __init__(self, schemas_dir: str = ""):
        self._schemas_dir = schemas_dir or _DEFAULT_SCHEMAS_DIR
        self._schemas: Dict[str, SchemaInfo] = {}
        self._loaded = False

    @property
    def schemas_dir(self) -> str:
        return self._schemas_dir

    def load(self) -> None:
        """
        Load all .sql files from the schemas directory.

        Extracts version from SQL header comment (looks for 'v2.0' pattern
        in the first 10 lines). Idempotent — safe to call multiple times.
        """
        if self._loaded:
            return

        if not os.path.isdir(self._schemas_dir):
            logger.warning(f"Schemas directory not found: {self._schemas_dir}")
            self._loaded = True
            return

        for filename in os.listdir(self._schemas_dir):
            if not filename.endswith(".sql"):
                continue

            file_path = os.path.join(self._schemas_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sql = f.read()

                # Extract schema name from filename
                # e.g. "invoices_canonical_v2.sql" → "invoices"
                name = filename.split("_")[0]

                # Extract version from header (first 10 lines)
                version = self._extract_version(sql)

                # Extract description from header
                description = self._extract_description(sql)

                self._schemas[name] = SchemaInfo(
                    name=name,
                    version=version,
                    sql=sql,
                    file_path=file_path,
                    description=description,
                )
                logger.info(
                    f"Schema registered: {name} v{version} ({filename})"
                )

            except Exception as e:
                logger.error(f"Failed to load schema {filename}: {e}")

        self._loaded = True
        logger.info(f"Schema registry loaded: {len(self._schemas)} schema(s)")

    def get_schema(self, name: str) -> Optional[SchemaInfo]:
        """Get a registered schema by name. Returns None if not found."""
        self.load()
        return self._schemas.get(name)

    def get_schema_sql(self, name: str) -> Optional[str]:
        """Get raw SQL text for a schema. Returns None if not found."""
        info = self.get_schema(name)
        return info.sql if info else None

    def list_schemas(self) -> List[Dict]:
        """List all registered schemas (name, version, description)."""
        self.load()
        return [
            {
                "name": s.name,
                "version": s.version,
                "description": s.description,
            }
            for s in self._schemas.values()
        ]

    def update_schema(self, name: str, sql: str) -> tuple:
        """
        Hot-update a schema: validate version, write to disk, update in-memory.

        Args:
            name: Schema name (e.g. "invoices")
            sql:  Full SQL text (must contain version header)

        Returns:
            (old_version, new_version) tuple.
            old_version is "0.0" if no previous version existed.

        Raises:
            ValueError: If new version is not strictly newer than current.
        """
        self.load()

        new_version = self._extract_version(sql)
        if new_version == "unknown":
            raise ValueError(
                f"Cannot extract version from SQL. "
                f"Ensure header contains a version pattern like 'v2.1'."
            )

        # Check current version
        current = self._schemas.get(name)
        old_version = current.version if current else "0.0"

        if current is not None:
            cmp = self._compare_versions(current.version, new_version)
            if cmp >= 0:
                raise ValueError(
                    f"Schema '{name}' version {new_version} is not newer "
                    f"than current version {current.version}. "
                    f"Bump the version in the SQL header."
                )

        # Write new file to disk
        new_filename = f"{name}_canonical_v{new_version}.sql"
        new_file_path = os.path.join(self._schemas_dir, new_filename)

        os.makedirs(self._schemas_dir, exist_ok=True)
        with open(new_file_path, "w", encoding="utf-8") as f:
            f.write(sql)
        logger.info(f"Schema file written: {new_filename}")

        # Delete old file if it exists with a different version
        if current is not None and current.file_path != new_file_path:
            if os.path.exists(current.file_path):
                os.remove(current.file_path)
                logger.info(f"Old schema file removed: {os.path.basename(current.file_path)}")

        # Update in-memory registry
        description = self._extract_description(sql)
        self._schemas[name] = SchemaInfo(
            name=name,
            version=new_version,
            sql=sql,
            file_path=new_file_path,
            description=description,
        )
        logger.info(f"Schema updated in-memory: {name} v{old_version} -> v{new_version}")

        return (old_version, new_version)

    def reload_from_disk(self) -> list:
        """
        Re-scan the schemas directory and update in-memory state.

        Unlike load(), this always runs (not idempotent). Detects new schemas,
        updated versions, and refreshes in-memory cache accordingly.

        Returns:
            List of (name, old_version, new_version) tuples for schemas
            that changed. Empty list if nothing changed.
        """
        changes = []

        if not os.path.isdir(self._schemas_dir):
            logger.warning(f"Schemas directory not found: {self._schemas_dir}")
            self._loaded = True
            return changes

        for filename in os.listdir(self._schemas_dir):
            if not filename.endswith(".sql"):
                continue

            file_path = os.path.join(self._schemas_dir, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sql = f.read()

                name = filename.split("_")[0]
                version = self._extract_version(sql)
                description = self._extract_description(sql)

                current = self._schemas.get(name)
                old_version = current.version if current else "0.0"

                if current is None or current.version != version:
                    self._schemas[name] = SchemaInfo(
                        name=name,
                        version=version,
                        sql=sql,
                        file_path=file_path,
                        description=description,
                    )
                    changes.append((name, old_version, version))
                    logger.info(
                        f"Schema reloaded: {name} v{old_version} -> v{version} ({filename})"
                    )

            except Exception as e:
                logger.error(f"Failed to reload schema {filename}: {e}")

        self._loaded = True
        logger.info(
            f"Schema reload complete: {len(changes)} change(s) detected "
            f"out of {len(self._schemas)} schema(s)"
        )
        return changes

    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two version strings numerically.

        Args:
            v1: First version (e.g. "2.0")
            v2: Second version (e.g. "2.1")

        Returns:
            -1 if v1 < v2, 0 if equal, 1 if v1 > v2.
            Returns 0 if either version is "unknown".
        """
        if v1 == "unknown" or v2 == "unknown":
            return 0

        parts1 = [int(p) for p in v1.split(".")]
        parts2 = [int(p) for p in v2.split(".")]

        for a, b in zip(parts1, parts2):
            if a < b:
                return -1
            if a > b:
                return 1

        # If all compared parts are equal, longer version is greater
        if len(parts1) < len(parts2):
            return -1
        if len(parts1) > len(parts2):
            return 1

        return 0

    def _extract_version(self, sql: str) -> str:
        """Extract version from SQL header comment."""
        # Look for patterns like "v2.0", "CANONICAL SCHEMA v2.0", "Version: 2.0"
        header = "\n".join(sql.split("\n")[:15])
        match = re.search(r"v(\d+\.\d+)", header, re.IGNORECASE)
        if match:
            return match.group(1)

        # Fallback: look for INSERT INTO schema_version
        match = re.search(r"VALUES\s*\(\s*'(\d+\.\d+)'", sql)
        if match:
            return match.group(1)

        return "unknown"

    def _extract_description(self, sql: str) -> str:
        """Extract description from SQL header comment."""
        header = "\n".join(sql.split("\n")[:10])
        match = re.search(r"--\s*Status:\s*(.+)", header)
        if match:
            return match.group(1).strip()
        return ""


# ── Singleton ────────────────────────────────────────────────────────────

_registry: Optional[SchemaRegistry] = None


def get_schema_registry(schemas_dir: str = "") -> SchemaRegistry:
    """Get singleton schema registry (loads on first call)."""
    global _registry
    if _registry is None:
        _registry = SchemaRegistry(schemas_dir)
    return _registry


def reset_schema_registry() -> None:
    """Reset singleton (for testing)."""
    global _registry
    _registry = None


# ── Convenience accessors for the invoice schema ─────────────────────────

def get_invoice_schema_sql() -> str:
    """
    Get the canonical invoice schema SQL.

    This is the SQL that Core uses to create invoices.db and
    the SDK uses to create the invoices partition of sync.db.

    Returns:
        Full SQL text (CREATE TABLE + indexes + triggers + views)

    Raises:
        RuntimeError: If invoice schema not found in registry
    """
    registry = get_schema_registry()
    sql = registry.get_schema_sql("invoices")
    if sql is None:
        raise RuntimeError(
            "Invoice schema not found in registry. "
            f"Check that invoices_canonical_v2.sql exists in {registry.schemas_dir}"
        )
    return sql


def get_invoice_schema_version() -> str:
    """Get the version string of the canonical invoice schema."""
    registry = get_schema_registry()
    info = registry.get_schema("invoices")
    return info.version if info else "unknown"


# Module-level constant: pre-loaded invoice schema SQL.
# Lazy — actually loaded on first access via the registry.
class _LazySchemaProxy:
    """Lazy proxy that loads schema SQL on first string access."""

    def __str__(self) -> str:
        return get_invoice_schema_sql()

    def __repr__(self) -> str:
        version = get_invoice_schema_version()
        return f"<InvoiceSchema v{version}>"

    def __bool__(self) -> bool:
        try:
            get_invoice_schema_sql()
            return True
        except RuntimeError:
            return False


invoice_schema = _LazySchemaProxy()
