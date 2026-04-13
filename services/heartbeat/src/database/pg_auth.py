"""
HeartBeat Auth Database (PostgreSQL)

PostgreSQL-backed auth operations for the auth schema.
Replaces the SQLite auth_connection.py for production use.

All queries use the auth.* schema prefix.
Uses %s placeholders (psycopg2 style, not ? SQLite style).
"""

import logging
import os
import secrets
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

from .pg_connection import get_pg_pool


logger = logging.getLogger(__name__)


class PgAuthDatabase:
    """
    PostgreSQL auth database operations.

    All queries target the auth schema in the heartbeat database.
    Thread-safe via the underlying connection pool.
    """

    def __init__(self):
        """Initialize with the shared PostgresPool."""
        self._pool = get_pg_pool()

    # -- User Operations -----------------------------------------------

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Look up user by email address."""
        results = self._pool.execute_query(
            "SELECT * FROM auth.users WHERE email = %s", (email,)
        )
        return results[0] if results else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Look up user by user_id."""
        results = self._pool.execute_query(
            "SELECT * FROM auth.users WHERE user_id = %s", (user_id,)
        )
        return results[0] if results else None

    def update_last_login(self, user_id: str) -> int:
        """Stamp last_login_at on successful login."""
        now = datetime.now(timezone.utc)
        return self._pool.execute_update(
            "UPDATE auth.users SET last_login_at = %s, updated_at = %s "
            "WHERE user_id = %s",
            (now, now, user_id),
        )

    def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        role_id: str,
        tenant_id: str,
        is_first_run: bool = True,
        must_reset_password: bool = False,
    ) -> Dict[str, Any]:
        """Create a new user with auto-generated master_secret."""
        master_secret = secrets.token_hex(32)  # 32 bytes = 64 hex chars
        now = datetime.now(timezone.utc)

        self._pool.execute_insert(
            """INSERT INTO auth.users
               (user_id, email, password_hash, display_name, role_id,
                tenant_id, is_first_run, must_reset_password,
                master_secret, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, email, password_hash, display_name, role_id,
             tenant_id, is_first_run, must_reset_password,
             master_secret, now, now),
        )

        return self.get_user_by_id(user_id)

    # -- Permission Operations -----------------------------------------

    def get_user_permissions(
        self, user_id: str, role_id: str
    ) -> List[str]:
        """
        Get effective permissions for a user.

        Combines role default permissions + per-user overrides (granted=true).
        """
        # Role defaults
        role_perms = self._pool.execute_query(
            "SELECT permission_id FROM auth.role_permissions "
            "WHERE role_id = %s",
            (role_id,),
        )

        # Per-user overrides (granted only)
        user_perms = self._pool.execute_query(
            "SELECT permission_id FROM auth.user_permissions "
            "WHERE user_id = %s AND granted = TRUE",
            (user_id,),
        )

        # Merge into unique set
        all_perms = set()
        for row in role_perms:
            all_perms.add(row["permission_id"])
        for row in user_perms:
            all_perms.add(row["permission_id"])

        return sorted(all_perms)

    # -- Session Operations --------------------------------------------

    def create_session(
        self,
        session_id: str,
        user_id: str,
        jwt_jti: str,
        issued_at: str,
        expires_at: str,
        last_auth_at: str,
        session_expires_at: str = "",
        last_auth_method: str = "password",
    ) -> None:
        """Create a new session record."""
        self._pool.execute_insert(
            """INSERT INTO auth.sessions
               (session_id, user_id, jwt_jti, issued_at, expires_at,
                last_auth_at, session_expires_at, last_auth_method)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (session_id, user_id, jwt_jti, issued_at, expires_at,
             last_auth_at, session_expires_at, last_auth_method),
        )

    def get_session_by_jti(self, jwt_jti: str) -> Optional[Dict[str, Any]]:
        """Look up session by JWT jti claim."""
        results = self._pool.execute_query(
            "SELECT * FROM auth.sessions WHERE jwt_jti = %s", (jwt_jti,)
        )
        return results[0] if results else None

    def get_session_by_id(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up session by session_id."""
        results = self._pool.execute_query(
            "SELECT * FROM auth.sessions WHERE session_id = %s",
            (session_id,),
        )
        return results[0] if results else None

    def get_active_sessions_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active (non-revoked) sessions for a user, oldest first."""
        return self._pool.execute_query(
            """SELECT * FROM auth.sessions
               WHERE user_id = %s AND is_revoked = FALSE
               ORDER BY issued_at ASC""",
            (user_id,),
        )

    def count_active_sessions(self, user_id: str) -> int:
        """Count active (non-revoked) sessions for a user."""
        results = self._pool.execute_query(
            """SELECT COUNT(*) as count FROM auth.sessions
               WHERE user_id = %s AND is_revoked = FALSE""",
            (user_id,),
        )
        return results[0]["count"] if results else 0

    def refresh_session(
        self,
        session_id: str,
        new_jwt_jti: str,
        new_expires_at: str,
    ) -> int:
        """
        Update session on token refresh.

        Updates jwt_jti, expires_at, and last_refreshed.
        Preserves last_auth_at (refresh is not re-authentication).
        """
        now = datetime.now(timezone.utc).isoformat()
        return self._pool.execute_update(
            """UPDATE auth.sessions
               SET jwt_jti = %s, expires_at = %s, last_refreshed = %s
               WHERE session_id = %s AND is_revoked = FALSE""",
            (new_jwt_jti, new_expires_at, now, session_id),
        )

    def update_session_auth(
        self,
        session_id: str,
        new_jwt_jti: str,
        new_expires_at: str,
        new_last_auth_at: str,
    ) -> int:
        """
        Update session after step-up re-authentication.

        Updates jwt_jti, expires_at, last_auth_at, and last_refreshed.
        Unlike refresh_session, this DOES update last_auth_at.
        """
        now = datetime.now(timezone.utc).isoformat()
        return self._pool.execute_update(
            """UPDATE auth.sessions
               SET jwt_jti = %s, expires_at = %s,
                   last_auth_at = %s, last_refreshed = %s
               WHERE session_id = %s AND is_revoked = FALSE""",
            (new_jwt_jti, new_expires_at, new_last_auth_at, now,
             session_id),
        )

    def revoke_session(
        self, session_id: str, reason: str = "logout"
    ) -> int:
        """Mark session as revoked."""
        now = datetime.now(timezone.utc).isoformat()
        return self._pool.execute_update(
            """UPDATE auth.sessions
               SET is_revoked = TRUE, revoked_at = %s, revoked_reason = %s
               WHERE session_id = %s AND is_revoked = FALSE""",
            (now, reason, session_id),
        )

    def revoke_session_by_jti(
        self, jwt_jti: str, reason: str = "logout"
    ) -> int:
        """Mark session as revoked by JWT jti."""
        now = datetime.now(timezone.utc).isoformat()
        return self._pool.execute_update(
            """UPDATE auth.sessions
               SET is_revoked = TRUE, revoked_at = %s, revoked_reason = %s
               WHERE jwt_jti = %s AND is_revoked = FALSE""",
            (now, reason, jwt_jti),
        )

    def revoke_all_user_sessions(
        self, user_id: str, reason: str = "password_changed"
    ) -> int:
        """Revoke all active sessions for a user."""
        now = datetime.now(timezone.utc).isoformat()
        return self._pool.execute_update(
            """UPDATE auth.sessions
               SET is_revoked = TRUE, revoked_at = %s, revoked_reason = %s
               WHERE user_id = %s AND is_revoked = FALSE""",
            (now, reason, user_id),
        )

    # -- Password History Operations -----------------------------------

    def get_password_history(
        self, user_id: str, limit: int = 5
    ) -> List[str]:
        """Return the last N bcrypt hashes for a user (most recent first)."""
        rows = self._pool.execute_query(
            "SELECT password_hash FROM auth.password_history "
            "WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
        return [row["password_hash"] for row in rows]

    def add_password_history(
        self, user_id: str, password_hash: str
    ) -> None:
        """Insert a hash into password_history."""
        self._pool.execute_insert(
            "INSERT INTO auth.password_history (user_id, password_hash) "
            "VALUES (%s, %s)",
            (user_id, password_hash),
        )

    def trim_password_history(self, user_id: str, keep: int = 5) -> int:
        """Delete history rows beyond the cap (oldest first)."""
        return self._pool.execute_update(
            """DELETE FROM auth.password_history
               WHERE user_id = %s
               AND id NOT IN (
                   SELECT id FROM auth.password_history
                   WHERE user_id = %s
                   ORDER BY created_at DESC
                   LIMIT %s
               )""",
            (user_id, user_id, keep),
        )

    def update_password(
        self,
        user_id: str,
        new_hash: str,
        clear_first_run: bool = False,
    ) -> int:
        """Overwrite users.password_hash."""
        now = datetime.now(timezone.utc)
        if clear_first_run:
            return self._pool.execute_update(
                "UPDATE auth.users "
                "SET password_hash = %s, is_first_run = FALSE, "
                "    must_reset_password = FALSE, updated_at = %s "
                "WHERE user_id = %s",
                (new_hash, now, user_id),
            )
        return self._pool.execute_update(
            "UPDATE auth.users SET password_hash = %s, updated_at = %s "
            "WHERE user_id = %s",
            (new_hash, now, user_id),
        )

    # -- Step-Up Policy Operations -------------------------------------

    def get_step_up_policy(self, operation: str) -> Optional[Dict[str, Any]]:
        """Get step-up policy for an operation."""
        results = self._pool.execute_query(
            "SELECT * FROM auth.step_up_policies WHERE operation = %s",
            (operation,),
        )
        return results[0] if results else None

    def get_all_step_up_policies(self) -> List[Dict[str, Any]]:
        """Get all step-up policies."""
        return self._pool.execute_query(
            "SELECT * FROM auth.step_up_policies ORDER BY operation"
        )

    # -- Master Secret Operations --------------------------------------

    def get_master_secret(self, user_id: str) -> Optional[str]:
        """Get the master_secret for cipher text derivation."""
        results = self._pool.execute_query(
            "SELECT master_secret FROM auth.users WHERE user_id = %s",
            (user_id,),
        )
        return results[0]["master_secret"] if results else None

    def rotate_master_secret(self, user_id: str) -> str:
        """Generate and set a new master_secret. Returns the new secret."""
        new_secret = secrets.token_hex(32)
        now = datetime.now(timezone.utc)
        self._pool.execute_update(
            "UPDATE auth.users SET master_secret = %s, updated_at = %s "
            "WHERE user_id = %s",
            (new_secret, now, user_id),
        )
        return new_secret

    # -- Concurrent Session Helpers ------------------------------------

    def get_tenant_max_sessions(self, tenant_id: str) -> int:
        """
        Get max concurrent sessions for a tenant.

        Currently returns the default (1). In future, this will query
        a tenant configuration table.
        """
        # TODO: Query tenant config table when built
        default = int(os.environ.get(
            "HEARTBEAT_MAX_CONCURRENT_SESSIONS", "1"
        ))
        return default


# -- Singleton ---------------------------------------------------------

_pg_auth_instance: Optional[PgAuthDatabase] = None
_pg_auth_lock = Lock()


def get_pg_auth_database() -> PgAuthDatabase:
    """Get singleton PgAuthDatabase instance."""
    global _pg_auth_instance

    with _pg_auth_lock:
        if _pg_auth_instance is None:
            _pg_auth_instance = PgAuthDatabase()

        return _pg_auth_instance


def reset_pg_auth_database() -> None:
    """Reset singleton instance (for testing/shutdown)."""
    global _pg_auth_instance
    with _pg_auth_lock:
        _pg_auth_instance = None
