"""
Wazuh Security Event Emitter (P2-B)

Emits OCSF-aligned security events to:
1. JSONL file (Wazuh agent reads this via log collector)
2. security_events table in blob.db (for dashboard queries)

OCSF = Open Cybersecurity Schema Framework
Wazuh uses JSON-based log decoding — we write one JSON object per line.

Event classes:
    - authentication: login/auth attempts
    - credential_lifecycle: key creation, rotation, revocation
    - file_activity: blob uploads, anomalies
    - security_finding: brute force, anomaly detection

Severity levels (Wazuh-aligned):
    - info (1): normal operations logged for baseline
    - low (3): minor issues, informational warnings
    - medium (5): suspicious activity, potential threat
    - high (7): confirmed threat, requires attention
    - critical (9): active attack, immediate response needed
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ..config import get_config


logger = logging.getLogger(__name__)


SEVERITY_MAP = {
    "info": 1,
    "low": 3,
    "medium": 5,
    "high": 7,
    "critical": 9,
}


class WazuhEventEmitter:
    """
    Writes OCSF-aligned security events to JSONL + SQLite.

    Usage:
        emitter = WazuhEventEmitter(db_path, log_path)
        emitter.emit(
            event_class="authentication",
            event_type="auth_failure",
            severity="medium",
            message="Invalid API key",
            actor_service="relay",
            actor_ip="10.0.1.5",
        )
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        log_path: Optional[str] = None,
    ):
        self.db_path = db_path
        self.log_path = log_path

        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def emit(
        self,
        event_class: str,
        event_type: str,
        message: str,
        severity: str = "info",
        actor_service: Optional[str] = None,
        actor_ip: Optional[str] = None,
        actor_credential_id: Optional[str] = None,
        target_resource: Optional[str] = None,
        target_service: str = "heartbeat",
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[int]:
        """
        Emit a security event.

        Writes to both JSONL log file and SQLite security_events table.
        Returns the SQLite row ID (or None if DB write is skipped).
        """
        now = datetime.now(timezone.utc).isoformat()

        event = {
            "timestamp": now,
            "event_class": event_class,
            "event_type": event_type,
            "severity": severity,
            "severity_id": SEVERITY_MAP.get(severity, 1),
            "message": message,
            "actor": {
                "service": actor_service,
                "ip": actor_ip,
                "credential_id": actor_credential_id,
            },
            "target": {
                "resource": target_resource,
                "service": target_service,
            },
            "details": details or {},
            "correlation_id": correlation_id,
            "source": "heartbeat",
        }

        # Compute checksum for tamper detection
        checksum_data = f"{event_class}|{event_type}|{severity}|{message}|{now}"
        checksum = hashlib.sha256(checksum_data.encode()).hexdigest()
        event["checksum"] = checksum

        # Write to JSONL file (Wazuh reads this)
        self._write_jsonl(event)

        # Write to SQLite (for dashboard/API queries)
        row_id = self._write_db(event, checksum, now)

        return row_id

    def _write_jsonl(self, event: Dict[str, Any]):
        """Append event as a single JSON line to the log file."""
        if not self.log_path:
            return

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.error(f"Failed to write security event to JSONL: {e}")

    def _write_db(
        self, event: Dict[str, Any], checksum: str, created_at: str
    ) -> Optional[int]:
        """Insert event into security_events table."""
        if not self.db_path:
            return None

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                """INSERT INTO security_events
                   (event_class, event_type, severity,
                    actor_service, actor_ip, actor_credential_id,
                    target_resource, target_service,
                    message, details_json, correlation_id,
                    created_at, checksum)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_class"],
                    event["event_type"],
                    event["severity"],
                    event["actor"]["service"],
                    event["actor"]["ip"],
                    event["actor"]["credential_id"],
                    event["target"]["resource"],
                    event["target"]["service"],
                    event["message"],
                    json.dumps(event.get("details", {})),
                    event.get("correlation_id"),
                    created_at,
                    checksum,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Failed to write security event to DB: {e}")
            return None

    def emit_auth_failure(
        self,
        actor_ip: str,
        actor_service: Optional[str] = None,
        reason: str = "Invalid API key",
        endpoint: str = "",
    ) -> Optional[int]:
        """Convenience: emit an authentication failure event."""
        return self.emit(
            event_class="authentication",
            event_type="auth_failure",
            severity="medium",
            message=reason,
            actor_service=actor_service,
            actor_ip=actor_ip,
            target_resource=endpoint,
        )

    def emit_brute_force(
        self,
        actor_ip: str,
        attempt_count: int,
        window_seconds: int = 300,
    ) -> Optional[int]:
        """Convenience: emit a brute force detection event."""
        return self.emit(
            event_class="security_finding",
            event_type="brute_force",
            severity="high",
            message=f"Brute force detected: {attempt_count} failed attempts in {window_seconds}s from {actor_ip}",
            actor_ip=actor_ip,
            details={
                "attempt_count": attempt_count,
                "window_seconds": window_seconds,
            },
        )

    def emit_credential_event(
        self,
        event_type: str,
        credential_id: str,
        performed_by: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Convenience: emit a credential lifecycle event (created/rotated/revoked)."""
        severity = "info" if event_type == "key_created" else "low"
        return self.emit(
            event_class="credential_lifecycle",
            event_type=event_type,
            severity=severity,
            message=f"Credential {event_type}: {credential_id}",
            actor_service=performed_by,
            actor_credential_id=credential_id,
            target_resource=f"credential:{credential_id}",
            details=details,
        )

    def emit_upload_event(
        self,
        blob_uuid: str,
        file_size: int,
        actor_service: Optional[str] = None,
        actor_ip: Optional[str] = None,
    ) -> Optional[int]:
        """Convenience: emit a file upload event."""
        return self.emit(
            event_class="file_activity",
            event_type="file_uploaded",
            severity="info",
            message=f"Blob uploaded: {blob_uuid} ({file_size} bytes)",
            actor_service=actor_service,
            actor_ip=actor_ip,
            target_resource=f"blob:{blob_uuid}",
            details={"file_size": file_size, "blob_uuid": blob_uuid},
        )


# ── Singleton ──────────────────────────────────────────────────────────

_emitter_instance: Optional[WazuhEventEmitter] = None


def get_wazuh_emitter() -> Optional[WazuhEventEmitter]:
    """Get singleton WazuhEventEmitter (returns None if not initialized)."""
    return _emitter_instance


def init_wazuh_emitter(
    db_path: Optional[str] = None,
    log_path: Optional[str] = None,
) -> WazuhEventEmitter:
    """Initialize and return the WazuhEventEmitter singleton."""
    global _emitter_instance
    _emitter_instance = WazuhEventEmitter(db_path=db_path, log_path=log_path)
    return _emitter_instance


def set_wazuh_emitter(emitter: WazuhEventEmitter) -> None:
    """Override emitter singleton (for testing)."""
    global _emitter_instance
    _emitter_instance = emitter


def reset_wazuh_emitter() -> None:
    """Reset emitter singleton (for testing)."""
    global _emitter_instance
    _emitter_instance = None
