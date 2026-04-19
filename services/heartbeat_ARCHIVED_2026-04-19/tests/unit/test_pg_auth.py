"""
Tests for PostgreSQL Auth Layer

Tests the pg_connection, pg_auth, auth_handler (with cipher_text,
step-up, concurrent sessions), and SSE producer.

Requires a running PostgreSQL instance with the heartbeat database.
Connection: postgresql://postgres:Technology100@localhost:5432/heartbeat
"""

import asyncio
import hashlib
import hmac
import math
import os
import time

import bcrypt
import pytest
from uuid6 import uuid7

# NOTE: Do NOT set os.environ at module level — it leaks into other test
# files and causes the app lifespan to attempt real PG connections,
# which can hang or timeout for tests that use TestClient(app).
# PG env vars are scoped to each test via the _pg_env autouse fixture.

from src.database.pg_connection import PostgresPool, get_pg_pool, reset_pg_pool
from src.database.pg_auth import PgAuthDatabase, get_pg_auth_database, reset_pg_auth_database
from src.config import HeartBeatConfig, set_config, reset_config


# -- Fixtures ----------------------------------------------------------

TEST_DSN = "postgresql://postgres:Technology100@localhost:5432/heartbeat"


@pytest.fixture(autouse=True)
def _pg_env(monkeypatch):
    """Set PG env vars for this test only, then reset singletons."""
    monkeypatch.setenv("HEARTBEAT_PG_PASSWORD", "Technology100")
    monkeypatch.setenv("HEARTBEAT_PG_HOST", "localhost")
    monkeypatch.setenv("HEARTBEAT_PG_PORT", "5432")
    monkeypatch.setenv("HEARTBEAT_PG_DATABASE", "heartbeat")

    reset_pg_pool()
    reset_pg_auth_database()
    reset_config()
    yield
    reset_pg_pool()
    reset_pg_auth_database()
    reset_config()


@pytest.fixture
def pg_pool():
    """Get a fresh PostgreSQL pool."""
    pool = get_pg_pool(dsn=TEST_DSN)
    return pool


@pytest.fixture
def auth_db(pg_pool):
    """Get PgAuthDatabase backed by the test pool."""
    return get_pg_auth_database()


@pytest.fixture
def test_config():
    """Set up a test config with PostgreSQL settings."""
    config = HeartBeatConfig(
        pg_password="Technology100",
        pg_host="localhost",
        pg_port=5432,
        pg_database="heartbeat",
        jwt_expiry_minutes=30,
        session_hours=8,
        cipher_window_seconds=540,
        max_concurrent_sessions=1,
    )
    set_config(config)
    return config


@pytest.fixture
def test_user_id():
    """Generate a unique test user ID."""
    return f"test-user-{uuid7().hex[:8]}"


@pytest.fixture
def test_user(auth_db, test_user_id):
    """Create a test user in the database and clean up after."""
    password = "TestPassword1"
    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=4),  # Fast rounds for testing
    ).decode("utf-8")

    user = auth_db.create_user(
        user_id=test_user_id,
        email=f"{test_user_id}@test.pronalytics.ng",
        password_hash=password_hash,
        display_name="Test User",
        role_id="Operator",
        tenant_id="test-tenant-001",
        is_first_run=False,
    )

    yield {
        "user": user,
        "password": password,
        "password_hash": password_hash,
    }

    # Cleanup: delete test data
    try:
        auth_db._pool.execute_update(
            "DELETE FROM auth.sessions WHERE user_id = %s",
            (test_user_id,),
        )
        auth_db._pool.execute_update(
            "DELETE FROM auth.password_history WHERE user_id = %s",
            (test_user_id,),
        )
        auth_db._pool.execute_update(
            "DELETE FROM auth.user_permissions WHERE user_id = %s",
            (test_user_id,),
        )
        auth_db._pool.execute_update(
            "DELETE FROM auth.users WHERE user_id = %s",
            (test_user_id,),
        )
    except Exception:
        pass


# -- PostgreSQL Pool Tests ---------------------------------------------

class TestPostgresPool:
    """Tests for pg_connection.py PostgresPool."""

    def test_pool_creates_successfully(self, pg_pool):
        """Pool connects to PostgreSQL."""
        assert pg_pool is not None

    def test_execute_query(self, pg_pool):
        """Can run a SELECT query."""
        result = pg_pool.execute_query("SELECT 1 as num")
        assert result == [{"num": 1}]

    def test_execute_query_with_params(self, pg_pool):
        """Can run a parameterized query."""
        result = pg_pool.execute_query(
            "SELECT %s as val", ("hello",)
        )
        assert result[0]["val"] == "hello"

    def test_connection_context_manager(self, pg_pool):
        """Context manager returns connection to pool."""
        with pg_pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            assert row[0] == 1

    def test_cursor_context_manager(self, pg_pool):
        """Cursor context manager auto-commits."""
        with pg_pool.get_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM auth.roles")
            row = cur.fetchone()
            assert row["count"] >= 4  # owner, admin, operator, support

    def test_singleton_returns_same_pool(self, pg_pool):
        """get_pg_pool returns the same instance."""
        pool2 = get_pg_pool()
        assert pool2 is pg_pool


# -- Auth Database Tests -----------------------------------------------

class TestPgAuthDatabase:
    """Tests for pg_auth.py PgAuthDatabase."""

    def test_get_user_by_email(self, auth_db, test_user):
        """Look up user by email."""
        email = test_user["user"]["email"]
        user = auth_db.get_user_by_email(email)
        assert user is not None
        assert user["email"] == email

    def test_get_user_by_id(self, auth_db, test_user):
        """Look up user by user_id."""
        uid = test_user["user"]["user_id"]
        user = auth_db.get_user_by_id(uid)
        assert user is not None
        assert user["user_id"] == uid

    def test_get_user_not_found(self, auth_db):
        """Returns None for nonexistent user."""
        assert auth_db.get_user_by_email("nonexistent@test.com") is None
        assert auth_db.get_user_by_id("nonexistent-id") is None

    def test_create_user_generates_master_secret(self, auth_db, test_user):
        """create_user generates a master_secret."""
        user = test_user["user"]
        assert user["master_secret"] is not None
        assert len(user["master_secret"]) == 64  # 32 bytes hex

    def test_update_last_login(self, auth_db, test_user):
        """update_last_login stamps the timestamp."""
        uid = test_user["user"]["user_id"]
        result = auth_db.update_last_login(uid)
        assert result == 1

        user = auth_db.get_user_by_id(uid)
        assert user["last_login_at"] is not None

    def test_get_user_permissions(self, auth_db, test_user):
        """Get effective permissions for operator role."""
        user = test_user["user"]
        perms = auth_db.get_user_permissions(
            user["user_id"], user["role_id"]
        )
        assert isinstance(perms, list)
        assert len(perms) > 0
        assert "invoice.view" in perms  # Operators can view invoices

    def test_master_secret_operations(self, auth_db, test_user):
        """get_master_secret and rotate_master_secret."""
        uid = test_user["user"]["user_id"]

        # Get current secret
        secret = auth_db.get_master_secret(uid)
        assert secret is not None
        assert len(secret) == 64

        # Rotate
        new_secret = auth_db.rotate_master_secret(uid)
        assert new_secret != secret
        assert len(new_secret) == 64

        # Verify rotation
        fetched = auth_db.get_master_secret(uid)
        assert fetched == new_secret


# -- Session Tests -----------------------------------------------------

class TestSessions:
    """Tests for session CRUD operations."""

    def test_create_and_get_session(self, auth_db, test_user):
        """Create and retrieve a session."""
        uid = test_user["user"]["user_id"]
        session_id = f"sess-{uuid7()}"
        jti = f"tok-{uuid7()}"

        auth_db.create_session(
            session_id=session_id,
            user_id=uid,
            jwt_jti=jti,
            issued_at="2026-03-03T10:00:00Z",
            expires_at="2026-03-03T10:30:00Z",
            last_auth_at="2026-03-03T10:00:00Z",
            session_expires_at="2026-03-03T18:00:00Z",
        )

        session = auth_db.get_session_by_jti(jti)
        assert session is not None
        assert session["user_id"] == uid
        assert session["is_revoked"] is False

    def test_refresh_session(self, auth_db, test_user):
        """Refresh updates jti and expires_at."""
        uid = test_user["user"]["user_id"]
        session_id = f"sess-{uuid7()}"
        jti = f"tok-{uuid7()}"

        auth_db.create_session(
            session_id=session_id,
            user_id=uid,
            jwt_jti=jti,
            issued_at="2026-03-03T10:00:00Z",
            expires_at="2026-03-03T10:30:00Z",
            last_auth_at="2026-03-03T10:00:00Z",
            session_expires_at="2026-03-03T18:00:00Z",
        )

        new_jti = f"tok-{uuid7()}"
        result = auth_db.refresh_session(
            session_id=session_id,
            new_jwt_jti=new_jti,
            new_expires_at="2026-03-03T11:00:00Z",
        )
        assert result == 1

        session = auth_db.get_session_by_jti(new_jti)
        assert session is not None

    def test_revoke_session(self, auth_db, test_user):
        """Revoke a session by session_id."""
        uid = test_user["user"]["user_id"]
        session_id = f"sess-{uuid7()}"
        jti = f"tok-{uuid7()}"

        auth_db.create_session(
            session_id=session_id,
            user_id=uid,
            jwt_jti=jti,
            issued_at="2026-03-03T10:00:00Z",
            expires_at="2026-03-03T10:30:00Z",
            last_auth_at="2026-03-03T10:00:00Z",
            session_expires_at="2026-03-03T18:00:00Z",
        )

        result = auth_db.revoke_session(session_id, "test_revoke")
        assert result == 1

        session = auth_db.get_session_by_jti(jti)
        assert session["is_revoked"] is True

    def test_count_active_sessions(self, auth_db, test_user):
        """Count active sessions for a user."""
        uid = test_user["user"]["user_id"]

        # Initially 0
        assert auth_db.count_active_sessions(uid) == 0

        # Create 2 sessions
        for i in range(2):
            auth_db.create_session(
                session_id=f"sess-{uuid7()}",
                user_id=uid,
                jwt_jti=f"tok-{uuid7()}",
                issued_at="2026-03-03T10:00:00Z",
                expires_at="2026-03-03T10:30:00Z",
                last_auth_at="2026-03-03T10:00:00Z",
                session_expires_at="2026-03-03T18:00:00Z",
            )

        assert auth_db.count_active_sessions(uid) == 2

    def test_revoke_all_user_sessions(self, auth_db, test_user):
        """Revoke all sessions for a user."""
        uid = test_user["user"]["user_id"]

        for i in range(3):
            auth_db.create_session(
                session_id=f"sess-{uuid7()}",
                user_id=uid,
                jwt_jti=f"tok-{uuid7()}",
                issued_at="2026-03-03T10:00:00Z",
                expires_at="2026-03-03T10:30:00Z",
                last_auth_at="2026-03-03T10:00:00Z",
                session_expires_at="2026-03-03T18:00:00Z",
            )

        assert auth_db.count_active_sessions(uid) == 3

        revoked = auth_db.revoke_all_user_sessions(uid, "test")
        assert revoked == 3
        assert auth_db.count_active_sessions(uid) == 0

    def test_update_session_auth(self, auth_db, test_user):
        """Step-up updates last_auth_at."""
        uid = test_user["user"]["user_id"]
        session_id = f"sess-{uuid7()}"
        jti = f"tok-{uuid7()}"

        auth_db.create_session(
            session_id=session_id,
            user_id=uid,
            jwt_jti=jti,
            issued_at="2026-03-03T10:00:00Z",
            expires_at="2026-03-03T10:30:00Z",
            last_auth_at="2026-03-03T10:00:00Z",
            session_expires_at="2026-03-03T18:00:00Z",
        )

        new_jti = f"tok-{uuid7()}"
        result = auth_db.update_session_auth(
            session_id=session_id,
            new_jwt_jti=new_jti,
            new_expires_at="2026-03-03T14:30:00Z",
            new_last_auth_at="2026-03-03T14:00:00Z",
        )
        assert result == 1


# -- Password History Tests --------------------------------------------

class TestPasswordHistory:
    """Tests for password history operations."""

    def test_add_and_get_history(self, auth_db, test_user):
        """Add and retrieve password history."""
        uid = test_user["user"]["user_id"]
        old_hash = test_user["password_hash"]

        auth_db.add_password_history(uid, old_hash)

        history = auth_db.get_password_history(uid, limit=5)
        assert len(history) == 1
        assert history[0] == old_hash

    def test_trim_password_history(self, auth_db, test_user):
        """Trim keeps only the most recent N entries."""
        uid = test_user["user"]["user_id"]

        # Add 8 entries
        for i in range(8):
            auth_db.add_password_history(uid, f"hash_{i}")

        auth_db.trim_password_history(uid, keep=5)

        history = auth_db.get_password_history(uid, limit=10)
        assert len(history) == 5

    def test_update_password(self, auth_db, test_user):
        """Update password hash."""
        uid = test_user["user"]["user_id"]
        new_hash = bcrypt.hashpw(
            b"NewPassword123",
            bcrypt.gensalt(rounds=4),
        ).decode("utf-8")

        result = auth_db.update_password(uid, new_hash)
        assert result == 1

        user = auth_db.get_user_by_id(uid)
        assert user["password_hash"] == new_hash


# -- Step-Up Policy Tests ----------------------------------------------

class TestStepUpPolicies:
    """Tests for step-up policy operations."""

    def test_get_existing_policy(self, auth_db):
        """Get a seeded step-up policy."""
        policy = auth_db.get_step_up_policy("invoice.finalize")
        assert policy is not None
        assert policy["tier"] == "auth"
        assert policy["required_within_seconds"] == 300

    def test_get_nonexistent_policy(self, auth_db):
        """Returns None for unknown operation."""
        policy = auth_db.get_step_up_policy("nonexistent.operation")
        assert policy is None

    def test_get_all_policies(self, auth_db):
        """Get all step-up policies."""
        policies = auth_db.get_all_step_up_policies()
        assert len(policies) >= 12  # 12 seeded policies


# -- Cipher Text Derivation Tests --------------------------------------

class TestCipherTextDerivation:
    """Tests for cipher text derivation logic."""

    def test_derive_cipher_text_deterministic(self):
        """Same secret + same time window = same cipher text."""
        from src.handlers.auth_handler import _derive_cipher_text

        secret = "a" * 64  # 32 bytes hex
        ct1 = _derive_cipher_text(secret, 540)
        ct2 = _derive_cipher_text(secret, 540)
        assert ct1 == ct2

    def test_derive_cipher_text_different_secrets(self):
        """Different secrets produce different cipher text."""
        from src.handlers.auth_handler import _derive_cipher_text

        ct1 = _derive_cipher_text("a" * 64, 540)
        ct2 = _derive_cipher_text("b" * 64, 540)
        assert ct1 != ct2

    def test_cipher_text_is_hex(self):
        """Cipher text is a valid hex string."""
        from src.handlers.auth_handler import _derive_cipher_text

        ct = _derive_cipher_text("a" * 64, 540)
        assert len(ct) == 64  # SHA256 hex = 64 chars
        int(ct, 16)  # Should not raise

    def test_cipher_valid_until(self):
        """valid_until is in the future."""
        from src.handlers.auth_handler import _cipher_valid_until
        from datetime import datetime, timezone

        valid_until = _cipher_valid_until(540)
        assert "Z" in valid_until

        # Parse and verify it's in the future
        dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        assert dt > datetime.now(timezone.utc)


# -- Auth Handler Tests (Integration) ----------------------------------

class TestAuthHandlerLogin:
    """Tests for login handler with PostgreSQL."""

    @pytest.mark.asyncio
    async def test_login_success(self, auth_db, test_user, test_config):
        """Successful login returns access_token and cipher_text."""
        from src.handlers.auth_handler import login
        from src.auth.jwt_manager import get_jwt_manager, reset_jwt_manager

        reset_jwt_manager()
        # Init JWT manager
        import tempfile
        keys_dir = tempfile.mkdtemp()
        get_jwt_manager(
            private_key_path=os.path.join(keys_dir, "test_private.pem"),
            public_key_path=os.path.join(keys_dir, "test_public.pem"),
        )

        try:
            email = test_user["user"]["email"]
            password = test_user["password"]

            result = await login(email, password)

            assert "access_token" in result
            assert "cipher_text" in result
            assert result["token_type"] == "bearer"
            assert result["user"]["user_id"] == test_user["user"]["user_id"]
            assert result["user"]["role"] == "Operator"
            assert len(result["cipher_text"]) == 64  # SHA256 hex
        finally:
            reset_jwt_manager()

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, auth_db, test_user, test_config):
        """Wrong password returns TOKEN_INVALID."""
        from src.handlers.auth_handler import login
        from src.errors import HeartBeatError
        from src.auth.jwt_manager import get_jwt_manager, reset_jwt_manager

        reset_jwt_manager()
        import tempfile
        keys_dir = tempfile.mkdtemp()
        get_jwt_manager(
            private_key_path=os.path.join(keys_dir, "test_private.pem"),
            public_key_path=os.path.join(keys_dir, "test_public.pem"),
        )

        try:
            email = test_user["user"]["email"]
            with pytest.raises(HeartBeatError) as exc_info:
                await login(email, "WrongPassword123")
            assert exc_info.value.error_code == "TOKEN_INVALID"
        finally:
            reset_jwt_manager()

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, auth_db, test_config):
        """Nonexistent email returns TOKEN_INVALID."""
        from src.handlers.auth_handler import login
        from src.errors import HeartBeatError
        from src.auth.jwt_manager import get_jwt_manager, reset_jwt_manager

        reset_jwt_manager()
        import tempfile
        keys_dir = tempfile.mkdtemp()
        get_jwt_manager(
            private_key_path=os.path.join(keys_dir, "test_private.pem"),
            public_key_path=os.path.join(keys_dir, "test_public.pem"),
        )

        try:
            with pytest.raises(HeartBeatError) as exc_info:
                await login("nonexistent@test.com", "password")
            assert exc_info.value.error_code == "TOKEN_INVALID"
        finally:
            reset_jwt_manager()

    @pytest.mark.asyncio
    async def test_login_concurrent_session_limit(self, auth_db, test_user, test_config):
        """Login blocked when concurrent session limit reached."""
        from src.handlers.auth_handler import login
        from src.errors import HeartBeatError
        from src.auth.jwt_manager import get_jwt_manager, reset_jwt_manager

        reset_jwt_manager()
        import tempfile
        keys_dir = tempfile.mkdtemp()
        get_jwt_manager(
            private_key_path=os.path.join(keys_dir, "test_private.pem"),
            public_key_path=os.path.join(keys_dir, "test_public.pem"),
        )

        try:
            email = test_user["user"]["email"]
            password = test_user["password"]

            # First login succeeds (max_concurrent_sessions=1)
            result1 = await login(email, password)
            assert "access_token" in result1

            # Second login should fail with SESSION_LIMIT
            with pytest.raises(HeartBeatError) as exc_info:
                await login(email, password)
            assert exc_info.value.error_code == "SESSION_LIMIT"
        finally:
            reset_jwt_manager()


# -- SSE Event Bus Tests -----------------------------------------------

class TestSSEEventBus:
    """Tests for the SSE event bus."""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        """Subscribe and receive an event."""
        from src.sse.producer import SSEEventBus, SSEEvent

        bus = SSEEventBus()
        claims = {"sub": "user-1", "role": "admin", "permissions": ["*"]}
        queue = await bus.subscribe("client-1", claims)

        event = SSEEvent(
            event_type="blob.uploaded",
            data={"blob_uuid": "test-123"},
        )
        delivered = await bus.publish(event)
        assert delivered == 1

        received = queue.get_nowait()
        assert received.event_type == "blob.uploaded"

        await bus.unsubscribe("client-1")

    @pytest.mark.asyncio
    async def test_user_targeted_event(self):
        """User-targeted events only go to the right user."""
        from src.sse.producer import SSEEventBus, SSEEvent

        bus = SSEEventBus()
        q1 = await bus.subscribe("c1", {"sub": "user-1", "role": "admin", "permissions": []})
        q2 = await bus.subscribe("c2", {"sub": "user-2", "role": "admin", "permissions": []})

        event = SSEEvent(
            event_type="auth.cipher_refresh",
            data={"cipher_text": "abc"},
            target_user_id="user-1",
        )
        delivered = await bus.publish(event)
        assert delivered == 1
        assert not q2.empty() is False  # q2 should be empty
        assert q2.empty()

        await bus.unsubscribe("c1")
        await bus.unsubscribe("c2")

    @pytest.mark.asyncio
    async def test_role_filtered_event(self):
        """Role-filtered events respect hierarchy."""
        from src.sse.producer import SSEEventBus, SSEEvent

        bus = SSEEventBus()
        q_admin = await bus.subscribe("c-admin", {"sub": "u1", "role": "admin", "permissions": []})
        q_operator = await bus.subscribe("c-op", {"sub": "u2", "role": "operator", "permissions": []})

        event = SSEEvent(
            event_type="service.health_changed",
            data={"service": "core", "status": "degraded"},
            target_role="admin",
        )
        delivered = await bus.publish(event)
        assert delivered == 1
        assert not q_admin.empty()
        assert q_operator.empty()

        await bus.unsubscribe("c-admin")
        await bus.unsubscribe("c-op")

    @pytest.mark.asyncio
    async def test_permission_filtered_event(self):
        """Permission-filtered events check user permissions."""
        from src.sse.producer import SSEEventBus, SSEEvent

        bus = SSEEventBus()
        q1 = await bus.subscribe("c1", {"sub": "u1", "role": "op", "permissions": ["audit.view"]})
        q2 = await bus.subscribe("c2", {"sub": "u2", "role": "op", "permissions": ["invoice.view"]})

        event = SSEEvent(
            event_type="notification.new",
            data={"msg": "audit event"},
            required_permission="audit.view",
        )
        delivered = await bus.publish(event)
        assert delivered == 1
        assert not q1.empty()
        assert q2.empty()

        await bus.unsubscribe("c1")
        await bus.unsubscribe("c2")

    @pytest.mark.asyncio
    async def test_format_sse(self):
        """SSE wire format is correct."""
        from src.sse.producer import SSEEvent

        event = SSEEvent(
            event_type="blob.uploaded",
            data={"uuid": "123"},
        )
        formatted = event.format_sse()
        assert formatted.startswith("event: blob.uploaded\n")
        assert "data: " in formatted
        assert formatted.endswith("\n\n")


# -- Step-Up Policy Handler Tests --------------------------------------

class TestStepUpPolicyHandler:
    """Tests for get_operation_policy handler."""

    @pytest.mark.asyncio
    async def test_known_operation(self, auth_db):
        """Known operation returns its policy."""
        from src.handlers.auth_handler import get_operation_policy

        result = await get_operation_policy("invoice.finalize")
        assert result["operation"] == "invoice.finalize"
        assert result["tier"] == "auth"
        assert result["required_within_seconds"] == 300

    @pytest.mark.asyncio
    async def test_unknown_operation_defaults(self, auth_db):
        """Unknown operation defaults to routine/3600."""
        from src.handlers.auth_handler import get_operation_policy

        result = await get_operation_policy("custom.operation")
        assert result["operation"] == "custom.operation"
        assert result["tier"] == "routine"
        assert result["required_within_seconds"] == 3600
