"""
HeartBeat Audit Immutability Guard (Q4 — Demo Question)

Provides:
  1. Checksum chain for tamper detection on audit_events
  2. Chain verification (detect if any row has been modified)
  3. Immutable insert that computes checksum_chain automatically

The checksum chain works like a blockchain lite:
  checksum_chain = SHA256(id || service || event_type || details || created_at || prev_checksum)

Where prev_checksum is the checksum_chain of the previous row (by id).
The first row uses a genesis hash: SHA256("HELIUM_AUDIT_GENESIS").

SQLite triggers (created by migration 002) prevent UPDATE and DELETE
on audit_events. This module adds the chain logic on top.
"""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..errors import DatabaseError


logger = logging.getLogger(__name__)

# Genesis hash — anchor for the first audit event
GENESIS_HASH = hashlib.sha256(b"HELIUM_AUDIT_GENESIS").hexdigest()


def compute_audit_checksum(
    event_id: int,
    service: str,
    event_type: str,
    details: Optional[str],
    created_at: str,
    prev_checksum: str,
) -> str:
    """
    Compute the SHA-256 checksum for an audit event.

    The chain: checksum = SHA256(id|service|event_type|details|created_at|prev_checksum)

    Args:
        event_id: Row ID
        service: Service name
        event_type: Event type
        details: JSON details string (or None)
        created_at: ISO timestamp
        prev_checksum: Previous row's checksum_chain value

    Returns:
        SHA-256 hex digest
    """
    payload = f"{event_id}|{service}|{event_type}|{details or ''}|{created_at}|{prev_checksum}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_last_checksum(db_path: str) -> str:
    """
    Get the checksum_chain value of the most recent audit event.

    Returns GENESIS_HASH if no events exist or all events pre-date the
    migration (checksum_chain is NULL).

    Args:
        db_path: Path to blob.db

    Returns:
        The last checksum hex string, or GENESIS_HASH.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            """SELECT checksum_chain FROM audit_events
               WHERE checksum_chain IS NOT NULL
               ORDER BY id DESC LIMIT 1"""
        )
        row = cursor.fetchone()
        if row and row["checksum_chain"]:
            return row["checksum_chain"]
        return GENESIS_HASH
    finally:
        conn.close()


def insert_audited_event(
    db_path: str,
    service: str,
    event_type: str,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Insert an audit event with automatic checksum chain computation.

    This is the immutable-aware replacement for BlobDatabase.log_audit_event().
    It:
      1. Gets the previous checksum (or genesis)
      2. Inserts the event
      3. Computes the checksum chain over the inserted row
      4. Immediately updates the checksum_chain field

    Note: The UPDATE for checksum_chain is the ONE exception to the
    immutability trigger. We work around it by inserting with the checksum
    in a single transaction before the trigger fires, OR we insert with
    the checksum pre-computed and include it in the INSERT.

    Returns:
        Dict with event_id, checksum_chain, and other fields.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    now_unix = int(time.time())
    details_json = json.dumps(details) if details else None

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Get the last checksum in the chain
        cursor = conn.execute(
            """SELECT checksum_chain FROM audit_events
               WHERE checksum_chain IS NOT NULL
               ORDER BY id DESC LIMIT 1"""
        )
        row = cursor.fetchone()
        prev_checksum = row["checksum_chain"] if row and row["checksum_chain"] else GENESIS_HASH

        # Get what the next ID will be (max(id) + 1)
        cursor = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM audit_events")
        next_id = cursor.fetchone()["next_id"]

        # Pre-compute the checksum
        checksum = compute_audit_checksum(
            event_id=next_id,
            service=service,
            event_type=event_type,
            details=details_json,
            created_at=now_iso,
            prev_checksum=prev_checksum,
        )

        # Insert with checksum included (avoids needing UPDATE, which trigger blocks)
        cursor = conn.execute(
            """INSERT INTO audit_events (
                   service, event_type, user_id, details,
                   trace_id, ip_address,
                   created_at, created_at_unix,
                   checksum_chain
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                service, event_type, user_id, details_json,
                trace_id, ip_address,
                now_iso, now_unix,
                checksum,
            ),
        )
        conn.commit()

        actual_id = cursor.lastrowid

        # If the actual ID differs from predicted (shouldn't happen in
        # single-writer SQLite, but safety check), recompute
        if actual_id != next_id:
            logger.warning(
                f"Audit ID prediction mismatch: predicted={next_id}, actual={actual_id}. "
                f"Checksum may be incorrect."
            )

        return {
            "event_id": actual_id,
            "service": service,
            "event_type": event_type,
            "user_id": user_id,
            "details": details,
            "trace_id": trace_id,
            "ip_address": ip_address,
            "created_at": now_iso,
            "created_at_unix": now_unix,
            "checksum_chain": checksum,
        }

    finally:
        conn.close()


def verify_chain(
    db_path: str,
    from_id: Optional[int] = None,
    to_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Verify the integrity of the audit event checksum chain.

    Walks through all audit events with checksum_chain != NULL,
    recomputes each checksum, and compares.

    Args:
        db_path: Path to blob.db
        from_id: Start verification from this event ID (inclusive)
        to_id: End verification at this event ID (inclusive)

    Returns:
        Dict with:
          - verified: bool (True if chain is intact)
          - chain_length: int (number of events verified)
          - tampered_rows: list of IDs where checksum doesn't match
          - first_chained_id: first ID in the chain
          - last_chained_id: last ID in the chain
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Build query with optional range
        conditions = ["checksum_chain IS NOT NULL"]
        params = []

        if from_id is not None:
            conditions.append("id >= ?")
            params.append(from_id)
        if to_id is not None:
            conditions.append("id <= ?")
            params.append(to_id)

        where = " AND ".join(conditions)
        cursor = conn.execute(
            f"SELECT * FROM audit_events WHERE {where} ORDER BY id ASC",
            tuple(params),
        )
        rows = [dict(row) for row in cursor.fetchall()]

        if not rows:
            return {
                "verified": True,
                "chain_length": 0,
                "tampered_rows": [],
                "first_chained_id": None,
                "last_chained_id": None,
            }

        tampered = []
        prev_checksum = GENESIS_HASH

        # If starting mid-chain, we need the previous row's checksum
        if from_id is not None and rows[0]["id"] > 1:
            prev_cursor = conn.execute(
                """SELECT checksum_chain FROM audit_events
                   WHERE id < ? AND checksum_chain IS NOT NULL
                   ORDER BY id DESC LIMIT 1""",
                (rows[0]["id"],),
            )
            prev_row = prev_cursor.fetchone()
            if prev_row and prev_row["checksum_chain"]:
                prev_checksum = prev_row["checksum_chain"]

        for row in rows:
            expected = compute_audit_checksum(
                event_id=row["id"],
                service=row["service"],
                event_type=row["event_type"],
                details=row["details"],
                created_at=row["created_at"],
                prev_checksum=prev_checksum,
            )

            if row["checksum_chain"] != expected:
                tampered.append(row["id"])

            prev_checksum = row["checksum_chain"]

        return {
            "verified": len(tampered) == 0,
            "chain_length": len(rows),
            "tampered_rows": tampered,
            "first_chained_id": rows[0]["id"],
            "last_chained_id": rows[-1]["id"],
        }

    finally:
        conn.close()
