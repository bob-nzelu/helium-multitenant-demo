"""
Integration Tests for Complete Auth Flows

Tests the full auth lifecycle against real PostgreSQL:
- Login with multiple roles
- Token refresh within session cap
- Step-up re-authentication
- Logout and session revocation
- First-run bootstrap flow
- Concurrent session limits
- Password change with session revocation
- Cipher text derivation and consistency
- Token introspection

Requires: Running PostgreSQL instance with heartbeat database.
"""

import uuid
from datetime import datetime, timezone

import bcrypt
import pytest

from src.handlers.auth_handler import (
    login,
    refresh_token,
    logout,
    introspect_token,
    step_up_auth,
    change_password,
    get_operation_policy,
    get_cipher_text_for_user,
    _derive_cipher_text,
    _cipher_valid_until,
)
from src.errors import HeartBeatError


def _parse_iso(ts: str) -> datetime:
    """Parse ISO timestamp (handles both Z and +HH:MM suffixes)."""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


# ── Login Flow Tests ────────────────────────────────────────────────────

class TestLoginFlow:
    """Tests for the login endpoint handler."""

    @pytest.mark.asyncio
    async def test_login_returns_access_token_and_cipher_text(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Successful login returns access_token and cipher_text."""
        operator = test_users["operator"]
        result = await login(operator["email"], operator["password"])

        assert "access_token" in result
        assert "cipher_text" in result
        assert result["token_type"] == "bearer"
        assert len(result["cipher_text"]) == 64  # SHA256 hex
        assert result["user"]["role"] == "Operator"

    @pytest.mark.asyncio
    async def test_login_returns_correct_user_info(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Login response includes correct user metadata."""
        admin = test_users["admin"]
        result = await login(admin["email"], admin["password"])

        assert result["user"]["user_id"] == admin["user_id"]
        assert result["user"]["role"] == "Admin"
        assert result["user"]["display_name"] == "Test Admin"
        assert result["user"]["is_first_run"] is False
        assert "expires_at" in result
        assert "session_expires_at" in result

    @pytest.mark.asyncio
    async def test_owner_login_has_all_permissions(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Owner role gets all permissions (either wildcard or full set)."""
        owner = test_users["owner"]
        result = await login(owner["email"], owner["password"])

        # Verify JWT by introspecting
        introspect = await introspect_token(result["access_token"])
        assert introspect["active"] is True
        perms = introspect["permissions"]
        # Owner gets either wildcard (*) or the full explicit permission set
        has_all = "*" in perms or "system.admin" in perms
        assert has_all, f"Owner should have full permissions, got: {perms}"

    @pytest.mark.asyncio
    async def test_support_login_has_readonly_permissions(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Support role gets read-only permissions."""
        support = test_users["support"]
        result = await login(support["email"], support["password"])

        introspect = await introspect_token(result["access_token"])
        assert introspect["active"] is True
        # Support should have view permissions but not write
        assert "invoice.view" in introspect["permissions"]
        assert "invoice.create" not in introspect["permissions"]

    @pytest.mark.asyncio
    async def test_login_wrong_password(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Wrong password raises TOKEN_INVALID."""
        operator = test_users["operator"]
        with pytest.raises(HeartBeatError) as exc_info:
            await login(operator["email"], "WrongPassword999")
        assert exc_info.value.error_code == "TOKEN_INVALID"

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(
        self, auth_db, jwt_keypair, test_config
    ):
        """Nonexistent email raises TOKEN_INVALID."""
        with pytest.raises(HeartBeatError) as exc_info:
            await login("nobody@nowhere.com", "password123")
        assert exc_info.value.error_code == "TOKEN_INVALID"

    @pytest.mark.asyncio
    async def test_all_four_roles_can_login(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """All four roles (owner, admin, operator, support) can log in."""
        for role_name, user_info in test_users.items():
            result = await login(user_info["email"], user_info["password"])
            assert result["user"]["role"] == user_info["user_record"]["role_id"]
            assert "access_token" in result


# ── Refresh Flow Tests ──────────────────────────────────────────────────

class TestRefreshFlow:
    """Tests for token refresh handler."""

    @pytest.mark.asyncio
    async def test_refresh_issues_new_token(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Refresh returns a new access_token."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])
        old_token = login_result["access_token"]

        refresh_result = await refresh_token(old_token)

        assert "access_token" in refresh_result
        assert refresh_result["access_token"] != old_token
        assert "expires_at" in refresh_result
        assert "session_expires_at" in refresh_result
        assert "last_auth_at" in refresh_result

    @pytest.mark.asyncio
    async def test_refresh_preserves_session_expiry(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Refresh does NOT extend session_expires_at (8hr cap is immutable)."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        refresh_result = await refresh_token(login_result["access_token"])

        # Compare as datetime objects (login uses Z suffix, refresh may use +HH:MM)
        login_cap = _parse_iso(login_result["session_expires_at"])
        refresh_cap = _parse_iso(refresh_result["session_expires_at"])
        assert login_cap == refresh_cap

    @pytest.mark.asyncio
    async def test_refresh_fails_on_revoked_session(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Refresh fails if session has been revoked."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        # Logout to revoke the session
        await logout(login_result["access_token"])

        # Refresh should fail
        with pytest.raises(HeartBeatError) as exc_info:
            await refresh_token(login_result["access_token"])
        assert exc_info.value.error_code in ("TOKEN_INVALID", "TOKEN_REVOKED")

    @pytest.mark.asyncio
    async def test_refresh_chain(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Can refresh a refreshed token (chain)."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        # First refresh
        r1 = await refresh_token(login_result["access_token"])
        # Second refresh from the first refreshed token
        r2 = await refresh_token(r1["access_token"])

        assert r2["access_token"] != r1["access_token"]
        # Compare as datetime objects (timezone format may differ)
        login_cap = _parse_iso(login_result["session_expires_at"])
        r2_cap = _parse_iso(r2["session_expires_at"])
        assert r2_cap == login_cap


# ── Step-Up Flow Tests ──────────────────────────────────────────────────

class TestStepUpFlow:
    """Tests for step-up re-authentication."""

    @pytest.mark.asyncio
    async def test_stepup_returns_fresh_token_and_cipher_text(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Step-up returns new access_token with fresh last_auth_at + cipher_text."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        stepup_result = await step_up_auth(
            login_result["access_token"], operator["password"]
        )

        assert "access_token" in stepup_result
        assert stepup_result["access_token"] != login_result["access_token"]
        assert "cipher_text" in stepup_result
        assert len(stepup_result["cipher_text"]) == 64
        assert "last_auth_at" in stepup_result

    @pytest.mark.asyncio
    async def test_stepup_wrong_password_fails(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Step-up with wrong password raises TOKEN_INVALID."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        with pytest.raises(HeartBeatError) as exc_info:
            await step_up_auth(login_result["access_token"], "WrongPassword99")
        assert exc_info.value.error_code == "TOKEN_INVALID"

    @pytest.mark.asyncio
    async def test_stepup_satisfies_introspect_freshness(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """After step-up, introspect with required_within_seconds succeeds."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        # Step up
        stepup_result = await step_up_auth(
            login_result["access_token"], operator["password"]
        )

        # Introspect with 300-second freshness requirement
        introspect = await introspect_token(
            stepup_result["access_token"],
            required_within_seconds=300,
        )
        assert introspect["active"] is True
        assert introspect["step_up_satisfied"] is True


# ── Logout Flow Tests ───────────────────────────────────────────────────

class TestLogoutFlow:
    """Tests for logout handler."""

    @pytest.mark.asyncio
    async def test_logout_revokes_session(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Logout revokes the session and prevents refresh."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        result = await logout(login_result["access_token"])
        assert result["status"] == "logged_out"

        # Token should now be invalid for introspect
        introspect = await introspect_token(login_result["access_token"])
        assert introspect["active"] is False
        assert introspect["error_code"] == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_double_logout_is_safe(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Calling logout twice does not error."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        await logout(login_result["access_token"])
        # Second logout should not raise
        result = await logout(login_result["access_token"])
        assert result["status"] == "logged_out"


# ── First-Run Bootstrap Tests ───────────────────────────────────────────

class TestFirstRunBootstrap:
    """Tests for first-run bootstrap flow."""

    @pytest.mark.asyncio
    async def test_first_run_login_gets_bootstrap_scope(
        self, auth_db, first_run_user, jwt_keypair, test_config
    ):
        """First-run user gets token with scope=bootstrap."""
        result = await login(
            first_run_user["email"], first_run_user["password"]
        )

        assert result["user"]["is_first_run"] is True
        # Introspect should show FIRST_RUN_REQUIRED
        introspect = await introspect_token(result["access_token"])
        assert introspect["active"] is False
        assert introspect["error_code"] == "FIRST_RUN_REQUIRED"

    @pytest.mark.asyncio
    async def test_bootstrap_password_change(
        self, auth_db, first_run_user, jwt_keypair, test_config
    ):
        """First-run user can change password without current_password."""
        login_result = await login(
            first_run_user["email"], first_run_user["password"]
        )

        result = await change_password(
            token=login_result["access_token"],
            new_password="NewSecurePass1",
            current_password=None,  # Not required for bootstrap
        )
        assert result["status"] == "password_changed"

        # After password change, all sessions revoked -- must re-login
        # with new password
        login2 = await login(
            first_run_user["email"], "NewSecurePass1"
        )
        assert login2["user"]["is_first_run"] is False

        # Introspect should now show active
        introspect = await introspect_token(login2["access_token"])
        assert introspect["active"] is True


# ── Session Limit Tests ─────────────────────────────────────────────────

class TestSessionLimits:
    """Tests for concurrent session enforcement."""

    @pytest.mark.asyncio
    async def test_concurrent_session_limit(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Login blocked when concurrent session limit reached."""
        # test_config has max_concurrent_sessions=3
        operator = test_users["operator"]

        # Login 3 times (max)
        tokens = []
        for _ in range(3):
            result = await login(operator["email"], operator["password"])
            tokens.append(result["access_token"])

        # 4th login should fail
        with pytest.raises(HeartBeatError) as exc_info:
            await login(operator["email"], operator["password"])
        assert exc_info.value.error_code == "SESSION_LIMIT"

    @pytest.mark.asyncio
    async def test_can_login_after_logout(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """After logout, a new session slot opens up."""
        operator = test_users["operator"]

        # Fill all 3 slots
        tokens = []
        for _ in range(3):
            result = await login(operator["email"], operator["password"])
            tokens.append(result["access_token"])

        # Logout one session
        await logout(tokens[0])

        # Should now be able to login again
        result = await login(operator["email"], operator["password"])
        assert "access_token" in result


# ── Password Change Tests ───────────────────────────────────────────────

class TestPasswordChange:
    """Tests for password change handler."""

    @pytest.mark.asyncio
    async def test_change_password_revokes_all_sessions(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Password change revokes all active sessions."""
        operator = test_users["operator"]

        # Login twice
        r1 = await login(operator["email"], operator["password"])
        r2 = await login(operator["email"], operator["password"])

        # Change password from session 1
        result = await change_password(
            token=r1["access_token"],
            new_password="BrandNewPass1",
            current_password=operator["password"],
        )
        assert result["status"] == "password_changed"

        # Both sessions should be revoked
        i1 = await introspect_token(r1["access_token"])
        i2 = await introspect_token(r2["access_token"])
        assert i1["active"] is False
        assert i2["active"] is False

        # Can login with new password
        r3 = await login(operator["email"], "BrandNewPass1")
        assert "access_token" in r3

    @pytest.mark.asyncio
    async def test_password_strength_enforced(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Weak password raises PW_WEAK."""
        operator = test_users["operator"]
        r = await login(operator["email"], operator["password"])

        with pytest.raises(HeartBeatError) as exc_info:
            await change_password(
                token=r["access_token"],
                new_password="weak",  # Too short, no uppercase, no digit
                current_password=operator["password"],
            )
        assert exc_info.value.error_code == "PW_WEAK"

    @pytest.mark.asyncio
    async def test_password_wrong_current(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Wrong current password raises PW_WRONG_CURRENT."""
        operator = test_users["operator"]
        r = await login(operator["email"], operator["password"])

        with pytest.raises(HeartBeatError) as exc_info:
            await change_password(
                token=r["access_token"],
                new_password="NewSecurePass1",
                current_password="WrongCurrent99",
            )
        assert exc_info.value.error_code == "PW_WRONG_CURRENT"

    @pytest.mark.asyncio
    async def test_password_recycling_prevented(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Cannot reuse the current password."""
        operator = test_users["operator"]
        r = await login(operator["email"], operator["password"])

        with pytest.raises(HeartBeatError) as exc_info:
            await change_password(
                token=r["access_token"],
                new_password=operator["password"],  # Same as current
                current_password=operator["password"],
            )
        assert exc_info.value.error_code == "PW_RECYCLED"


# ── Token Introspection Tests ──────────────────────────────────────────

class TestIntrospection:
    """Tests for token introspection handler."""

    @pytest.mark.asyncio
    async def test_introspect_active_token(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Valid token introspects as active with correct claims."""
        admin = test_users["admin"]
        login_result = await login(admin["email"], admin["password"])

        result = await introspect_token(login_result["access_token"])

        assert result["active"] is True
        assert result["actor_type"] == "human"
        assert result["user_id"] == admin["user_id"]
        assert result["role"] == "Admin"
        assert result["tenant_id"] == "test-tenant-integration"
        assert isinstance(result["permissions"], list)
        assert result["step_up_satisfied"] is True  # No freshness required

    @pytest.mark.asyncio
    async def test_introspect_with_permission_check(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Introspect checks required_permission."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        # Operator should have invoice.view
        result = await introspect_token(
            login_result["access_token"],
            required_permission="invoice.view",
        )
        assert result["active"] is True

    @pytest.mark.asyncio
    async def test_introspect_permission_denied(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Introspect returns PERMISSION_DENIED for unauthorized permission."""
        support = test_users["support"]
        login_result = await login(support["email"], support["password"])

        # Support should NOT have admin permissions
        result = await introspect_token(
            login_result["access_token"],
            required_permission="admin.manage_users",
        )
        assert result.get("error_code") == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_introspect_invalid_token(
        self, auth_db, jwt_keypair, test_config
    ):
        """Introspect returns active=false for garbage token."""
        result = await introspect_token("not.a.valid.token")
        assert result["active"] is False
        assert result["error_code"] == "TOKEN_INVALID"

    @pytest.mark.asyncio
    async def test_introspect_revoked_session(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Introspect returns active=false for revoked session."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])
        await logout(login_result["access_token"])

        result = await introspect_token(login_result["access_token"])
        assert result["active"] is False
        assert result["error_code"] == "TOKEN_REVOKED"


# ── Cipher Text Tests ──────────────────────────────────────────────────

class TestCipherText:
    """Tests for cipher text derivation and SSE delivery."""

    @pytest.mark.asyncio
    async def test_login_cipher_text_matches_derivation(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Login-returned cipher_text matches manual derivation."""
        operator = test_users["operator"]
        login_result = await login(operator["email"], operator["password"])

        # Get master_secret for this user
        master_secret = auth_db.get_master_secret(operator["user_id"])

        # Derive manually
        expected = _derive_cipher_text(master_secret, 540)

        assert login_result["cipher_text"] == expected

    @pytest.mark.asyncio
    async def test_get_cipher_text_for_user(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """get_cipher_text_for_user returns valid cipher data."""
        operator = test_users["operator"]
        # Just need a user in the DB, no login required
        result = await get_cipher_text_for_user(operator["user_id"])

        assert result is not None
        assert "cipher_text" in result
        assert "valid_until" in result
        assert "window_seconds" in result
        assert result["window_seconds"] == 540
        assert len(result["cipher_text"]) == 64

    @pytest.mark.asyncio
    async def test_cipher_text_different_users(
        self, auth_db, test_users, jwt_keypair, test_config
    ):
        """Different users produce different cipher texts (different master_secrets)."""
        owner = test_users["owner"]
        operator = test_users["operator"]

        r1 = await login(owner["email"], owner["password"])
        r2 = await login(operator["email"], operator["password"])

        assert r1["cipher_text"] != r2["cipher_text"]


# ── Operation Policy Tests ─────────────────────────────────────────────

class TestOperationPolicy:
    """Tests for step-up operation policy query."""

    @pytest.mark.asyncio
    async def test_known_policy(self, auth_db):
        """Known operation returns its policy."""
        result = await get_operation_policy("invoice.finalize")
        assert result["operation"] == "invoice.finalize"
        assert result["tier"] == "auth"
        assert result["required_within_seconds"] == 300

    @pytest.mark.asyncio
    async def test_unknown_policy_defaults_to_routine(self, auth_db):
        """Unknown operation defaults to routine (3600s)."""
        result = await get_operation_policy("custom.unknown.op")
        assert result["tier"] == "routine"
        assert result["required_within_seconds"] == 3600


# ── SSE Event Bus Integration Tests ────────────────────────────────────

class TestSSEEventBusIntegration:
    """Tests for SSE event bus with real auth context."""

    @pytest.mark.asyncio
    async def test_cipher_refresh_delivered_to_correct_user(
        self, sse_event_bus
    ):
        """Cipher refresh event targets only the specified user."""
        from src.sse.producer import SSEEvent

        # Subscribe two users
        q1 = await sse_event_bus.subscribe(
            "c1", {"sub": "user-1", "role": "operator", "permissions": []}
        )
        q2 = await sse_event_bus.subscribe(
            "c2", {"sub": "user-2", "role": "operator", "permissions": []}
        )

        event = SSEEvent(
            event_type="auth.cipher_refresh",
            data={"cipher_text": "abc123", "valid_until": "2026-03-04T12:00:00Z"},
            target_user_id="user-1",
        )
        delivered = await sse_event_bus.publish(event)

        assert delivered == 1
        assert not q1.empty()
        assert q2.empty()

        received = q1.get_nowait()
        assert received.event_type == "auth.cipher_refresh"
        assert received.data["cipher_text"] == "abc123"

        await sse_event_bus.unsubscribe("c1")
        await sse_event_bus.unsubscribe("c2")

    @pytest.mark.asyncio
    async def test_admin_only_event_filtered(self, sse_event_bus):
        """Admin-targeted event not delivered to operator."""
        from src.sse.producer import SSEEvent

        q_admin = await sse_event_bus.subscribe(
            "c-admin", {"sub": "u1", "role": "admin", "permissions": []}
        )
        q_operator = await sse_event_bus.subscribe(
            "c-op", {"sub": "u2", "role": "operator", "permissions": []}
        )

        event = SSEEvent(
            event_type="service.health_changed",
            data={"service": "core", "status": "degraded"},
            target_role="admin",
        )
        delivered = await sse_event_bus.publish(event)

        assert delivered == 1
        assert not q_admin.empty()
        assert q_operator.empty()

        await sse_event_bus.unsubscribe("c-admin")
        await sse_event_bus.unsubscribe("c-op")
