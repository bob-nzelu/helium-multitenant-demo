"""
PostgreSQL Connection Pool Manager for HeartBeat

Manages a psycopg2 connection pool to the heartbeat PostgreSQL database.
Thread-safe singleton with connection pooling.

All HeartBeat components share a single pool to a single PostgreSQL instance
with multiple schemas (auth, blob, audit, registry, license, notifications).

Connection string: postgresql://user:password@host:port/heartbeat
"""

import logging
import os
from contextlib import contextmanager
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import psycopg2.pool


logger = logging.getLogger(__name__)


class PostgresPool:
    """
    Thread-safe PostgreSQL connection pool.

    Uses psycopg2.pool.ThreadedConnectionPool for concurrent access.
    All queries return results as list of dicts (RealDictCursor).
    """

    def __init__(
        self,
        dsn: str,
        min_connections: int = 2,
        max_connections: int = 10,
        connect_timeout: int = 5,
    ):
        """
        Initialize the connection pool.

        Args:
            dsn: PostgreSQL connection string
                 e.g. "postgresql://postgres:pass@localhost:5432/heartbeat"
            min_connections: Minimum pool size (default: 2)
            max_connections: Maximum pool size (default: 10)
            connect_timeout: Seconds before connection attempt times out (default: 5)
        """
        self.dsn = dsn
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=min_connections,
            maxconn=max_connections,
            dsn=dsn,
            connect_timeout=connect_timeout,
        )
        logger.info(
            f"PostgreSQL pool initialized: min={min_connections}, "
            f"max={max_connections}"
        )

    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool (context manager).

        Automatically returns connection to pool on exit.
        Sets autocommit=False so callers control transactions.

        Usage:
            with pool.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ...")
                conn.commit()
        """
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    @contextmanager
    def get_cursor(self, commit: bool = True):
        """
        Get a cursor from a pooled connection (convenience).

        Auto-commits on success, rolls back on exception.

        Args:
            commit: Auto-commit on successful exit (default: True)

        Usage:
            with pool.get_cursor() as cur:
                cur.execute("INSERT INTO ...")
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def execute_query(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Execute SELECT query and return results as list of dicts."""
        with self.get_cursor(commit=False) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def execute_insert(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> Optional[Any]:
        """Execute INSERT query. Returns last inserted value if RETURNING used."""
        with self.get_cursor(commit=True) as cur:
            cur.execute(query, params)
            if cur.description:
                row = cur.fetchone()
                return dict(row) if row else None
            return None

    def execute_update(
        self,
        query: str,
        params: Optional[tuple] = None,
    ) -> int:
        """Execute UPDATE/DELETE query and return number of affected rows."""
        with self.get_cursor(commit=True) as cur:
            cur.execute(query, params)
            return cur.rowcount

    def close(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()
            logger.info("PostgreSQL connection pool closed")


# -- Singleton ---------------------------------------------------------

_pg_pool: Optional[PostgresPool] = None
_pg_lock = Lock()


def get_pg_pool(dsn: Optional[str] = None) -> PostgresPool:
    """
    Get singleton PostgresPool instance.

    Args:
        dsn: PostgreSQL connection string (required on first call).

    Returns:
        PostgresPool instance.
    """
    global _pg_pool

    with _pg_lock:
        if _pg_pool is None:
            if dsn is None:
                raise ValueError(
                    "dsn required on first call to get_pg_pool()"
                )
            _pg_pool = PostgresPool(dsn=dsn)

        return _pg_pool


def reset_pg_pool() -> None:
    """Close and reset the singleton pool (for testing/shutdown)."""
    global _pg_pool
    with _pg_lock:
        if _pg_pool is not None:
            _pg_pool.close()
            _pg_pool = None
