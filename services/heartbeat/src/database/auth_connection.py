"""
HeartBeat Auth Database Connection Module

Manages SQLCipher-encrypted connection to auth.db.
Thread-safe connection management with context managers.

Database: auth.db (encrypted with SQLCipher)
Tables: users, roles, permissions, role_permissions, user_permissions,
        sessions, schema_migrations
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

try:
    import sqlcipher3 as sqlite_module
    _HAS_SQLCIPHER = True
except ImportError:
    import sqlite3 as sqlite_module
    _HAS_SQLCIPHER = False


logger = logging.getLogger(__name__)


class AuthDatabase:
    """
    SQLCipher-encrypted database connection manager for auth data.

    Thread-safe singleton pattern. Provides methods for user, session,
    and permission operations.
    """

    def __init__(self, db_path: str, encryption_key: str = ""):
        self.db_path = db_path
        self.encryption_key = encryption_key

        if encryption_key and not _HAS_SQLCIPHER:
            logger.warning(
                "sqlcipher3 not installed — auth.db will NOT be encrypted. "
                "Install sqlcipher3-binary for production use."
            )

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    # ── Connection Management ──────────────────────────────────────

    def _get_raw_connection(self):
        """Get a raw connection with encryption key set."""
        conn = sqlite_module.connect(self.db_path)
        if self.encryption_key and _HAS_SQLCIPHER:
            conn.execute(f'PRAGMA key="{self.encryption_key}"')
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite_module.Row
        return conn

    @contextmanager
    def get_connection(self):
        """
        Thread-safe context manager for database connections.

        Usage:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
        """
        conn = self._get_raw_connection()
        try:
            yield conn
        finally:
            conn.close()

    def execute_query(
        self, query: str, params: Optional[tuple] = None
    ) -> List[Dict[str, Any]]:
        """Execute SELECT query and return results as list of dicts."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def execute_insert(self, query: str, params: tuple) -> int:
        """Execute INSERT query and return last row ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid

    def execute_update(self, query: str, params: tuple) -> int:
        """Execute UPDATE query and return number of affected rows."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount

    # ── User Operations ────────────────────────────────────────────

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Look up user by email address."""
        results = self.execute_query(
            "SELECT * FROM users WHERE email = ?", (email,)
        )
        return results[0] if results else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Look up user by user_id."""
        results = self.execute_query(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        return results[0] if results else None

    def update_last_login(self, user_id: str) -> int:
        """Stamp last_login_at on successful login."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            "UPDATE users SET last_login_at = ?, updated_at = ? "
            "WHERE user_id = ?",
            (now, now, user_id),
        )

    # ── Permission Operations ──────────────────────────────────────

    def get_user_permissions(
        self, user_id: str, role_id: str
    ) -> List[str]:
        """
        Get effective permissions for a user.

        Combines role default permissions + per-user overrides
        (excluding expired overrides).
        """
        now = datetime.now(timezone.utc).isoformat()

        # Role defaults
        role_perms = self.execute_query(
            "SELECT permission_id FROM role_permissions WHERE role_id = ?",
            (role_id,),
        )

        # Per-user overrides (non-expired)
        user_perms = self.execute_query(
            "SELECT permission_id FROM user_permissions "
            "WHERE user_id = ? AND (expires_at IS NULL OR expires_at > ?)",
            (user_id, now),
        )

        # Merge into unique set
        all_perms = set()
        for row in role_perms:
            all_perms.add(row["permission_id"])
        for row in user_perms:
            all_perms.add(row["permission_id"])

        return sorted(all_perms)

    # ── Session Operations ─────────────────────────────────────────

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
    ) -> int:
        """Create a new session record."""
        return self.execute_insert(
            """INSERT INTO sessions
               (session_id, user_id, jwt_jti, issued_at, expires_at,
                last_auth_at, session_expires_at, last_auth_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_id, jwt_jti, issued_at, expires_at,
             last_auth_at, session_expires_at, last_auth_method),
        )

    def get_session_by_jti(self, jwt_jti: str) -> Optional[Dict[str, Any]]:
        """Look up session by JWT jti claim."""
        results = self.execute_query(
            "SELECT * FROM sessions WHERE jwt_jti = ?", (jwt_jti,)
        )
        return results[0] if results else None

    def get_session_by_id(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up session by session_id."""
        results = self.execute_query(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        return results[0] if results else None

    def refresh_session(
        self,
        session_id: str,
        new_jwt_jti: str,
        new_expires_at: str,
    ) -> int:
        """
        Update session on token refresh.

        Updates jwt_jti (new token ID), expires_at, and last_refreshed.
        Preserves last_auth_at — refresh is not re-authentication.
        """
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE sessions
               SET jwt_jti = ?, expires_at = ?, last_refreshed = ?
               WHERE session_id = ? AND is_revoked = 0""",
            (new_jwt_jti, new_expires_at, now, session_id),
        )

    def revoke_session(self, session_id: str, reason: str = "logout") -> int:
        """Mark session as revoked."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE sessions
               SET is_revoked = 1, revoked_at = ?, revoked_reason = ?
               WHERE session_id = ? AND is_revoked = 0""",
            (now, reason, session_id),
        )

    def revoke_session_by_jti(
        self, jwt_jti: str, reason: str = "logout"
    ) -> int:
        """Mark session as revoked by JWT jti."""
        now = datetime.now(timezone.utc).isoformat()
        return self.execute_update(
            """UPDATE sessions
               SET is_revoked = 1, revoked_at = ?, revoked_reason = ?
               WHERE jwt_jti = ? AND is_revoked = 0""",
            (now, reason, jwt_jti),
        )

    # ── Password History Operations ────────────────────────────────

    def get_password_history(
        self, user_id: str, limit: int = 5
    ) -> List[str]:
        """Return the last N bcrypt hashes for a user (most recent first).

        Used by change_password() to enforce the no-recycle-last-N policy.
        Does NOT include the user's current password_hash — that is checked
        separately in the handler.
        """
        rows = self.execute_query(
            "SELECT hash FROM password_history "
            "WHERE user_id = ? "
            "ORDER BY set_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [row["hash"] for row in rows]

    def add_password_history(
        self, user_id: str, password_hash: str, set_at: str
    ) -> int:
        """Insert a hash into password_history.

        Call this BEFORE overwriting users.password_hash so the old hash
        is preserved in history.  Then call trim_password_history() to cap
        the row count.
        """
        return self.execute_insert(
            "INSERT INTO password_history (user_id, hash, set_at) "
            "VALUES (?, ?, ?)",
            (user_id, password_hash, set_at),
        )

    def trim_password_history(self, user_id: str, keep: int = 5) -> int:
        """Delete history rows beyond the cap (oldest first) for a user.

        Keeps at most `keep` rows so the table stays lean.
        """
        return self.execute_update(
            """DELETE FROM password_history
               WHERE user_id = ?
               AND id NOT IN (
                   SELECT id FROM password_history
                   WHERE user_id = ?
                   ORDER BY set_at DESC
                   LIMIT ?
               )""",
            (user_id, user_id, keep),
        )

    def update_password(
        self,
        user_id: str,
        new_hash: str,
        clear_first_run: bool = False,
    ) -> int:
        """Overwrite users.password_hash.

        Args:
            user_id:         Target user.
            new_hash:        New bcrypt hash.
            clear_first_run: If True, also sets is_first_run=0 and
                             must_reset_password=0 (used after bootstrap
                             password setup).
        """
        now = datetime.now(timezone.utc).isoformat()
        if clear_first_run:
            return self.execute_update(
                "UPDATE users "
                "SET password_hash = ?, is_first_run = 0, "
                "    must_reset_password = 0, updated_at = ? "
                "WHERE user_id = ?",
                (new_hash, now, user_id),
            )
        return self.execute_update(
            "UPDATE users SET password_hash = ?, updated_at = ? "
            "WHERE user_id = ?",
            (new_hash, now, user_id),
        )


# ── Singleton ──────────────────────────────────────────────────────

_auth_db_instance: Optional[AuthDatabase] = None
_auth_db_lock = Lock()


def get_auth_database(
    db_path: Optional[str] = None,
    encryption_key: Optional[str] = None,
) -> AuthDatabase:
    """
    Get singleton AuthDatabase instance.

    Args:
        db_path: Database path (required on first call).
        encryption_key: SQLCipher encryption key (required on first call).

    Returns:
        AuthDatabase instance.
    """
    global _auth_db_instance

    with _auth_db_lock:
        if _auth_db_instance is None:
            if db_path is None:
                raise ValueError(
                    "db_path required on first call to get_auth_database()"
                )
            _auth_db_instance = AuthDatabase(
                db_path=db_path,
                encryption_key=encryption_key or "",
            )

        return _auth_db_instance


def reset_auth_database() -> None:
    """Reset singleton instance (for testing)."""
    global _auth_db_instance
    with _auth_db_lock:
        _auth_db_instance = None
