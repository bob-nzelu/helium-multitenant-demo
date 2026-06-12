"""
Two-Level Deduplication

Cherry-picked from old_src/bulk/service.py (lines 408-461).
Extracted to standalone class for reuse by both bulk and external flows.

Level 1: Session cache (in-memory set of SHA256 hashes for current batch).
Level 2: HeartBeat persistent check (across all uploads, all tenants).

Graceful degradation: If HeartBeat is unavailable, allow the upload
and log a warning. Session cache still catches within-batch duplicates.

Hash algorithm: SHA-256 via the canonical ``helium_hash`` library
(CSSV1 D8 / S4 R7 — `HASHING_CONTRACT.md`). Same primitive shipped to
HB, Float SDK, and Reader/Scout SDK so the four consumers agree
byte-for-byte. Do NOT call ``hashlib.sha256`` directly here — that
would diverge from the canonical 64 KiB chunk + lowercase-hex contract.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

from helium_hash import sha256_file

logger = logging.getLogger(__name__)


@dataclass
class DedupResult:
    """Result of a deduplication check."""

    is_duplicate: bool
    file_hash: str
    source: str = ""                # "session" | "heartbeat" | ""
    original_queue_id: Optional[str] = None


class DedupChecker:
    """
    Two-level deduplication checker.

    Usage:
        checker = DedupChecker(heartbeat_client)
        result = await checker.check(file_data)
        if result.is_duplicate:
            return "duplicate"
        # Process file...
        checker.record(result.file_hash)

    Session cache is per-instance (per request/batch).
    HeartBeat check is persistent (across all requests).
    """

    def __init__(self, heartbeat_client: Any = None, trace_id: str = ""):
        """
        Args:
            heartbeat_client: HeartBeatClient for persistent dedup (optional).
            trace_id: Trace ID for logging.
        """
        self._heartbeat = heartbeat_client
        self._trace_id = trace_id
        self._session_cache: Set[str] = set()

    @staticmethod
    def compute_hash(file_data: bytes) -> str:
        """Compute SHA-256 hex digest of file data via the canonical
        ``helium_hash`` library (CSSV1 D8). Lowercase 64-char hex.
        """
        return sha256_file(file_data)

    async def check(self, file_data: bytes) -> DedupResult:
        """
        Check if file is a duplicate.

        Args:
            file_data: Raw file bytes.

        Returns:
            DedupResult with is_duplicate flag and metadata.
        """
        file_hash = self.compute_hash(file_data)

        # Level 1: Session cache (instant, in-memory)
        if file_hash in self._session_cache:
            logger.debug(
                f"Duplicate in session cache — hash={file_hash[:12]}...",
                extra={"trace_id": self._trace_id},
            )
            return DedupResult(
                is_duplicate=True,
                file_hash=file_hash,
                source="session",
            )

        # Level 2: HeartBeat persistent check
        if self._heartbeat:
            try:
                response = await self._heartbeat.check_duplicate(file_hash)

                if response.get("is_duplicate"):
                    logger.info(
                        f"Duplicate in HeartBeat — hash={file_hash[:12]}...",
                        extra={"trace_id": self._trace_id},
                    )
                    return DedupResult(
                        is_duplicate=True,
                        file_hash=file_hash,
                        source="heartbeat",
                        original_queue_id=response.get("original_queue_id"),
                    )

            except Exception as e:
                # Graceful degradation: HeartBeat down → allow upload
                logger.warning(
                    f"HeartBeat dedup check failed — allowing upload: {e}",
                    extra={"trace_id": self._trace_id},
                )

        # Not a duplicate
        return DedupResult(
            is_duplicate=False,
            file_hash=file_hash,
        )

    def record(self, file_hash: str) -> None:
        """
        Record a file hash in the session cache after successful processing.

        Call this AFTER the file has been successfully written to blob storage,
        not before (to avoid false positives on failed writes).

        Args:
            file_hash: SHA256 hex digest.
        """
        self._session_cache.add(file_hash)

    def clear(self) -> None:
        """Clear the session cache (for testing or batch reset)."""
        self._session_cache.clear()

    @property
    def session_size(self) -> int:
        """Number of hashes in session cache."""
        return len(self._session_cache)
