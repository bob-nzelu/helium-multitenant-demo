"""
Pytest Configuration for HeartBeat Tests

Fixtures and configuration for HeartBeat service testing.
"""

import sys
import os
from pathlib import Path

# Add HeartBeat root to path so `src.*` imports work (matches Relay convention)
heartbeat_root = Path(__file__).parent.parent
sys.path.insert(0, str(heartbeat_root))

import pytest
import sqlite3
from fastapi.testclient import TestClient


# ── Database Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def test_db_path(tmp_path):
    """Create temporary database path for testing."""
    return str(tmp_path / "test_blob.db")


@pytest.fixture
def test_db(tmp_path):
    """
    Create a fully initialized temporary database.

    Runs schema.sql to create all 12 tables.
    Returns path to the database file.
    """
    db_path = str(tmp_path / "test_blob.db")

    schema_path = Path(__file__).parent.parent / "databases" / "schema.sql"
    conn = sqlite3.connect(db_path)

    if schema_path.exists():
        with open(schema_path, 'r') as f:
            conn.executescript(f.read())

    # Load seed data if exists
    seed_path = Path(__file__).parent.parent / "databases" / "seed.sql"
    if seed_path.exists():
        with open(seed_path, 'r') as f:
            conn.executescript(f.read())

    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ── App/Client Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def app_config(test_db, tmp_path, monkeypatch):
    """
    Set up environment for HeartBeat app with test database.
    Resets singletons between tests.
    """
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    # Create a temp blob storage root
    blob_root = str(tmp_path / "blobs")
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    # Reset singletons
    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.config_db import reset_config_database
    from src.database.registry import reset_registry_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.clients.primary_client import reset_primary_client
    from src.database.pg_connection import reset_pg_pool
    from src.database.pg_auth import reset_pg_auth_database
    from src.auth.jwt_manager import reset_jwt_manager
    from src.sse.producer import reset_sse_producer
    from src.keepalive.manager import reset_keepalive_manager

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_registry_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()

    # Prevent lifespan from connecting to real PostgreSQL during existing tests
    monkeypatch.delenv("HEARTBEAT_PG_PASSWORD", raising=False)
    monkeypatch.delenv("HEARTBEAT_PG_DSN", raising=False)

    # Pre-initialize DB singleton so handlers find the test database
    get_blob_database(test_db)

    yield test_db

    # Cleanup singletons
    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_registry_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()


@pytest.fixture
def client(app_config):
    """Create FastAPI test client with test database and mock filesystem storage."""
    from src.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    """Standard authorization headers for testing."""
    return {"Authorization": "Bearer test-token"}


# ── Mock Filesystem Storage Fixture ─────────────────────────────────────

class MockFilesystemClient:
    """Mock filesystem blob client for testing without real filesystem I/O."""

    def __init__(self):
        self._storage = {}  # object_name -> bytes
        self._healthy = True

    async def put_blob(self, object_name, data, content_type="application/octet-stream"):
        self._storage[object_name] = data
        return object_name

    async def get_blob(self, object_name):
        if object_name not in self._storage:
            raise FileNotFoundError(f"Blob not found: {object_name}")
        return self._storage[object_name]

    async def delete_blob(self, object_name):
        self._storage.pop(object_name, None)

    async def blob_exists(self, object_name):
        return object_name in self._storage

    async def is_healthy(self):
        return self._healthy


@pytest.fixture
def mock_storage(monkeypatch):
    """Inject mock filesystem blob client."""
    mock = MockFilesystemClient()

    from src.clients import filesystem_client
    monkeypatch.setattr(filesystem_client, "_filesystem_instance", mock)

    return mock


# ── Request Payload Fixtures ────────────────────────────────────────────

@pytest.fixture
def valid_blob_request():
    """Valid blob registration request payload (legacy Phase 2 format)."""
    return {
        "blob_uuid": "550e8400-e29b-41d4-a716-446655440000",
        "blob_path": "/files_blob/550e8400-e29b-41d4-a716-446655440000-invoice.pdf",
        "file_size_bytes": 2048576,
        "file_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "content_type": "application/pdf",
        "source": "execujet-bulk-1",
    }


@pytest.fixture
def sample_file_data():
    """Sample file bytes for blob write tests."""
    return b"This is a sample PDF file content for testing purposes."


@pytest.fixture
def sample_file_hash():
    """SHA256 hash of sample_file_data."""
    import hashlib
    data = b"This is a sample PDF file content for testing purposes."
    return hashlib.sha256(data).hexdigest()


# ── Registry Database Fixtures ────────────────────────────────────────

@pytest.fixture
def registry_db_path(tmp_path):
    """Create a temporary registry database path with schema SQL available."""
    db_path = str(tmp_path / "databases" / "test_registry.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Copy schema SQL so RegistryDatabase._initialize_database finds it
    schema_src = Path(__file__).parent.parent / "databases" / "registry_schema.sql"
    schema_dst = tmp_path / "databases" / "registry_schema.sql"
    if schema_src.exists():
        import shutil
        shutil.copy(str(schema_src), str(schema_dst))

    return db_path


@pytest.fixture
def registry_db(registry_db_path):
    """
    Create a RegistryDatabase with schema only (no seed data).
    Resets singleton before and after.
    """
    from src.database.registry import RegistryDatabase, reset_registry_database

    reset_registry_database()
    db = RegistryDatabase(registry_db_path)

    yield db

    reset_registry_database()


@pytest.fixture
def seeded_registry_db(tmp_path):
    """
    Create a RegistryDatabase with schema AND seed data.
    For testing against pre-seeded credentials and instances.
    """
    from src.database.registry import RegistryDatabase, reset_registry_database
    import shutil

    db_path = str(tmp_path / "databases" / "seeded_registry.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    schema_src = Path(__file__).parent.parent / "databases" / "registry_schema.sql"
    seed_src = Path(__file__).parent.parent / "databases" / "registry_seed.sql"
    schema_dst = tmp_path / "databases" / "registry_schema.sql"
    seed_dst = tmp_path / "databases" / "registry_seed.sql"

    if schema_src.exists():
        shutil.copy(str(schema_src), str(schema_dst))
    if seed_src.exists():
        shutil.copy(str(seed_src), str(seed_dst))

    reset_registry_database()
    db = RegistryDatabase(db_path)

    yield db

    reset_registry_database()


@pytest.fixture
def registry_app_config(test_db, registry_db, tmp_path, monkeypatch):
    """
    Full app setup with BOTH blob and registry databases.
    For API-level registry endpoint tests.
    """
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    blob_root = str(tmp_path / "blobs")
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    # Prevent lifespan from connecting to real PostgreSQL
    monkeypatch.delenv("HEARTBEAT_PG_PASSWORD", raising=False)
    monkeypatch.delenv("HEARTBEAT_PG_DSN", raising=False)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.registry import set_registry_database, reset_registry_database
    from src.database.config_db import reset_config_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.clients.primary_client import reset_primary_client
    from src.database.pg_connection import reset_pg_pool
    from src.database.pg_auth import reset_pg_auth_database
    from src.auth.jwt_manager import reset_jwt_manager
    from src.sse.producer import reset_sse_producer
    from src.keepalive.manager import reset_keepalive_manager

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_registry_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()

    get_blob_database(test_db)
    set_registry_database(registry_db)

    yield test_db

    reset_blob_database()
    reset_registry_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()


@pytest.fixture
def registry_client(registry_app_config):
    """FastAPI test client with blob + registry databases."""
    from src.main import app
    with TestClient(app) as c:
        yield c


# ── Config Database Fixtures ─────────────────────────────────────────

@pytest.fixture
def config_db_path(tmp_path):
    """Create a temporary config database path with schema SQL available."""
    db_path = str(tmp_path / "databases" / "test_config.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Copy schema SQL so ConfigDatabase._initialize_database finds it
    schema_src = Path(__file__).parent.parent / "databases" / "config_schema.sql"
    schema_dst = tmp_path / "databases" / "config_schema.sql"
    if schema_src.exists():
        import shutil
        shutil.copy(str(schema_src), str(schema_dst))

    # Copy seed SQL
    seed_src = Path(__file__).parent.parent / "databases" / "config_seed.sql"
    seed_dst = tmp_path / "databases" / "config_seed.sql"
    if seed_src.exists():
        import shutil
        shutil.copy(str(seed_src), str(seed_dst))

    return db_path


@pytest.fixture
def config_db(config_db_path):
    """
    Create a ConfigDatabase with schema only (no seed data).
    Resets singleton before and after.
    """
    from src.database.config_db import ConfigDatabase, reset_config_database

    # Remove seed so only schema is loaded
    import os as _os
    seed_path = _os.path.join(_os.path.dirname(config_db_path), "config_seed.sql")
    if _os.path.exists(seed_path):
        _os.unlink(seed_path)

    reset_config_database()
    db = ConfigDatabase(config_db_path)

    yield db

    reset_config_database()


@pytest.fixture
def seeded_config_db(config_db_path):
    """
    Create a ConfigDatabase with schema AND seed data.
    For testing against pre-seeded config, tiers, and flags.
    """
    from src.database.config_db import ConfigDatabase, reset_config_database

    reset_config_database()
    db = ConfigDatabase(config_db_path)

    yield db

    reset_config_database()


@pytest.fixture
def config_app_config(test_db, seeded_config_db, tmp_path, monkeypatch):
    """
    Full app setup with blob + config databases.
    For API-level config endpoint tests.
    """
    monkeypatch.setenv("HEARTBEAT_BLOB_DB_PATH", test_db)
    monkeypatch.setenv("HEARTBEAT_MODE", "primary")

    blob_root = str(tmp_path / "blobs")
    os.makedirs(blob_root, exist_ok=True)
    monkeypatch.setenv("HEARTBEAT_BLOB_STORAGE_ROOT", blob_root)

    # Prevent lifespan from connecting to real PostgreSQL
    monkeypatch.delenv("HEARTBEAT_PG_PASSWORD", raising=False)
    monkeypatch.delenv("HEARTBEAT_PG_DSN", raising=False)

    from src.database.connection import reset_blob_database, get_blob_database
    from src.database.config_db import set_config_database, reset_config_database
    from src.database.registry import reset_registry_database
    from src.config import reset_config
    from src.clients.filesystem_client import reset_filesystem_client
    from src.clients.primary_client import reset_primary_client
    from src.database.pg_connection import reset_pg_pool
    from src.database.pg_auth import reset_pg_auth_database
    from src.auth.jwt_manager import reset_jwt_manager
    from src.sse.producer import reset_sse_producer
    from src.keepalive.manager import reset_keepalive_manager

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_registry_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()

    get_blob_database(test_db)
    set_config_database(seeded_config_db)

    yield test_db

    reset_blob_database()
    reset_config()
    reset_filesystem_client()
    reset_config_database()
    reset_registry_database()
    reset_primary_client()
    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_sse_producer()
    reset_keepalive_manager()


@pytest.fixture
def config_client(config_app_config):
    """FastAPI test client with blob + config databases."""
    from src.main import app
    with TestClient(app) as c:
        yield c
