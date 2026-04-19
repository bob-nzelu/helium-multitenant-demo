"""
Integration Test Fixtures for HeartBeat Auth

Bootstraps a complete auth tenancy for integration testing:
- PostgreSQL heartbeat database with auth schema
- Test tenant with configured settings
- 4 test users (owner, admin, operator, support) + first-run user
- Ed25519 JWT keypair for signing/verification
- SSE event bus (isolated per test)
- All singleton resets between tests

Requires: Running PostgreSQL instance at localhost:5432
Database: heartbeat (with auth schema from 001_auth_schema.sql)
"""

import os

import bcrypt
import pytest
from uuid6 import uuid7

from src.database.pg_connection import get_pg_pool, reset_pg_pool
from src.database.pg_auth import get_pg_auth_database, reset_pg_auth_database
from src.config import HeartBeatConfig, set_config, reset_config
from src.auth.jwt_manager import get_jwt_manager, reset_jwt_manager
from src.sse.producer import SSEEventBus, reset_sse_producer


# -- Constants ---------------------------------------------------------------

TEST_PG_DSN = "postgresql://postgres:Technology100@localhost:5432/heartbeat"
TEST_TENANT_ID = "test-tenant-integration"
TEST_TENANT_NAME = "Integration Test Tenant"

# User definitions: (suffix, role_id, display_name, password)
TEST_USERS = {
    "owner": {
        "suffix": "owner",
        "role_id": "Owner",
        "display_name": "Test Owner",
        "password": "OwnerPass1234",
    },
    "admin": {
        "suffix": "admin",
        "role_id": "Admin",
        "display_name": "Test Admin",
        "password": "AdminPass1234",
    },
    "operator": {
        "suffix": "operator",
        "role_id": "Operator",
        "display_name": "Test Operator",
        "password": "OperatorPass12",
    },
    "support": {
        "suffix": "support",
        "role_id": "Support",
        "display_name": "Test Support",
        "password": "SupportPass12",
    },
}


# -- Autouse: PG env vars + singleton resets ---------------------------------

@pytest.fixture(autouse=True)
def _pg_integration_env(monkeypatch):
    """
    Set PG env vars and reset ALL singletons for each integration test.

    Pattern: Same as tests/unit/test_pg_auth.py _pg_env but covers all
    singletons (not just PG ones) to ensure full isolation.
    """
    monkeypatch.setenv("HEARTBEAT_PG_PASSWORD", "Technology100")
    monkeypatch.setenv("HEARTBEAT_PG_HOST", "localhost")
    monkeypatch.setenv("HEARTBEAT_PG_PORT", "5432")
    monkeypatch.setenv("HEARTBEAT_PG_DATABASE", "heartbeat")
    monkeypatch.setenv("HEARTBEAT_MAX_CONCURRENT_SESSIONS", "3")

    # Prevent lifespan from triggering SQLite DB discovery
    monkeypatch.delenv("HEARTBEAT_BLOB_DB_PATH", raising=False)

    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_config()
    reset_sse_producer()

    yield

    reset_pg_pool()
    reset_pg_auth_database()
    reset_jwt_manager()
    reset_config()
    reset_sse_producer()


# -- Core Infrastructure Fixtures -------------------------------------------

@pytest.fixture
def pg_pool():
    """Get a fresh PostgreSQL pool connected to the test database."""
    return get_pg_pool(dsn=TEST_PG_DSN)


@pytest.fixture
def auth_db(pg_pool):
    """Get PgAuthDatabase backed by the test pool."""
    return get_pg_auth_database()


@pytest.fixture
def test_config():
    """
    HeartBeatConfig with test-appropriate values.

    - max_concurrent_sessions=3 (higher than unit tests for multi-user testing)
    - jwt_expiry_minutes=30
    - session_hours=8
    - cipher_window_seconds=540
    """
    config = HeartBeatConfig(
        pg_password="Technology100",
        pg_host="localhost",
        pg_port=5432,
        pg_database="heartbeat",
        jwt_expiry_minutes=30,
        session_hours=8,
        cipher_window_seconds=540,
        max_concurrent_sessions=3,
    )
    set_config(config)
    return config


@pytest.fixture
def jwt_keypair(tmp_path):
    """
    Fresh Ed25519 keypair for test JWT signing/verification.

    Uses tmp_path so keys are isolated per test and don't pollute
    databases/keys/.
    """
    reset_jwt_manager()
    private_path = str(tmp_path / "test_jwt_private.pem")
    public_path = str(tmp_path / "test_jwt_public.pem")

    mgr = get_jwt_manager(
        private_key_path=private_path,
        public_key_path=public_path,
    )

    yield {
        "manager": mgr,
        "private_key_path": private_path,
        "public_key_path": public_path,
        "public_key_pem": mgr.public_key_pem,
    }

    reset_jwt_manager()


# -- Test Run ID (session-scoped for user isolation) -------------------------

@pytest.fixture(scope="session")
def test_run_id():
    """
    Unique ID for this test run.

    Used as prefix for all test user IDs to ensure isolation
    between parallel or repeated test runs against the same DB.
    """
    return f"integ-{uuid7().hex[:8]}"


# -- Multi-User Fixtures ----------------------------------------------------

@pytest.fixture
def test_users(auth_db, test_run_id):
    """
    Create 4 test users with different roles (owner, admin, operator, support).

    Returns dict of role_name -> {user_id, email, password, password_hash, user_record}.
    Cleans up all test data after the test.
    """
    created = {}

    for role_name, user_def in TEST_USERS.items():
        user_id = f"{test_run_id}-{user_def['suffix']}"
        email = f"{user_id}@test.pronalytics.ng"
        password_hash = bcrypt.hashpw(
            user_def["password"].encode("utf-8"),
            bcrypt.gensalt(rounds=4),  # Fast rounds for testing
        ).decode("utf-8")

        user = auth_db.create_user(
            user_id=user_id,
            email=email,
            password_hash=password_hash,
            display_name=user_def["display_name"],
            role_id=user_def["role_id"],
            tenant_id=TEST_TENANT_ID,
            is_first_run=False,
        )

        created[role_name] = {
            "user_id": user_id,
            "email": email,
            "password": user_def["password"],
            "password_hash": password_hash,
            "user_record": user,
        }

    yield created

    # Cleanup: delete in reverse dependency order
    for role_name, user_info in created.items():
        uid = user_info["user_id"]
        try:
            auth_db._pool.execute_update(
                "DELETE FROM auth.sessions WHERE user_id = %s", (uid,))
            auth_db._pool.execute_update(
                "DELETE FROM auth.password_history WHERE user_id = %s", (uid,))
            auth_db._pool.execute_update(
                "DELETE FROM auth.user_permissions WHERE user_id = %s", (uid,))
            auth_db._pool.execute_update(
                "DELETE FROM auth.users WHERE user_id = %s", (uid,))
        except Exception:
            pass


@pytest.fixture
def first_run_user(auth_db, test_run_id):
    """
    Create a single user with is_first_run=True for bootstrap testing.

    Returns dict with user_id, email, password, user_record.
    """
    user_id = f"{test_run_id}-firstrun"
    email = f"{user_id}@test.pronalytics.ng"
    password_hash = bcrypt.hashpw(
        b"TempPassword1",
        bcrypt.gensalt(rounds=4),
    ).decode("utf-8")

    user = auth_db.create_user(
        user_id=user_id,
        email=email,
        password_hash=password_hash,
        display_name="First Run User",
        role_id="Operator",
        tenant_id=TEST_TENANT_ID,
        is_first_run=True,
    )

    yield {
        "user_id": user_id,
        "email": email,
        "password": "TempPassword1",
        "user_record": user,
    }

    # Cleanup
    try:
        auth_db._pool.execute_update(
            "DELETE FROM auth.sessions WHERE user_id = %s", (user_id,))
        auth_db._pool.execute_update(
            "DELETE FROM auth.password_history WHERE user_id = %s", (user_id,))
        auth_db._pool.execute_update(
            "DELETE FROM auth.user_permissions WHERE user_id = %s", (user_id,))
        auth_db._pool.execute_update(
            "DELETE FROM auth.users WHERE user_id = %s", (user_id,))
    except Exception:
        pass


# -- SSE Event Bus (isolated) -----------------------------------------------

@pytest.fixture
def sse_event_bus():
    """Isolated SSEEventBus for each test (not the global singleton)."""
    return SSEEventBus()


# -- Convenience: Pre-Logged-In Fixtures ------------------------------------

@pytest.fixture
async def logged_in_user(test_users, jwt_keypair, test_config):
    """
    Log in the operator user and return auth context.

    Returns dict with: access_token, cipher_text, user, expires_at,
    session_expires_at, email, password, user_id.
    """
    from src.handlers.auth_handler import login

    operator = test_users["operator"]
    result = await login(operator["email"], operator["password"])

    return {
        "access_token": result["access_token"],
        "cipher_text": result["cipher_text"],
        "user": result["user"],
        "expires_at": result["expires_at"],
        "session_expires_at": result["session_expires_at"],
        "email": operator["email"],
        "password": operator["password"],
        "user_id": operator["user_id"],
    }
