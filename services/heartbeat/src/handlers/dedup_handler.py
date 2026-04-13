"""
Deduplication Handler — Business logic for duplicate checking and recording.

Called by api/internal/dedup.py router.
"""

import logging
from typing import Any, Dict

from ..database import get_blob_database

logger = logging.getLogger(__name__)


async def check_duplicate(file_hash: str) -> Dict[str, Any]:
    """
    Check if a file hash has been seen before.

    Matches Relay HeartBeatClient.check_duplicate() contract:
        Returns: {is_duplicate: bool, file_hash, original_queue_id: str|null}
    """
    db = get_blob_database()
    record = db.check_dedup(file_hash)

    if record:
        # Increment the rejection counter
        db.increment_dedup_count(file_hash)

        logger.info(f"Duplicate detected: hash={file_hash[:12]}...")
        return {
            "is_duplicate": True,
            "file_hash": file_hash,
            "original_queue_id": record.get("original_blob_uuid"),
        }

    logger.debug(f"No duplicate: hash={file_hash[:12]}...")
    return {
        "is_duplicate": False,
        "file_hash": file_hash,
        "original_queue_id": None,
    }


async def record_duplicate(
    file_hash: str,
    queue_id: str,
) -> Dict[str, Any]:
    """
    Record a file hash after successful processing.

    Matches Relay HeartBeatClient.record_duplicate() contract:
        Returns: {file_hash, queue_id, status: "recorded"}
    """
    db = get_blob_database()

    try:
        db.record_dedup(
            file_hash=file_hash,
            source_system="relay",
            original_blob_uuid=queue_id,
        )
        logger.info(f"Dedup recorded: hash={file_hash[:12]}..., queue={queue_id}")
    except Exception as e:
        # IntegrityError = already recorded (idempotent)
        import sqlite3
        if isinstance(e, sqlite3.IntegrityError):
            logger.info(f"Dedup already recorded: hash={file_hash[:12]}...")
        else:
            raise

    return {
        "file_hash": file_hash,
        "queue_id": queue_id,
        "status": "recorded",
    }
