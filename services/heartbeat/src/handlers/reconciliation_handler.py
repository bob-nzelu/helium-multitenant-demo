"""
Reconciliation Engine (P2-E)

5-phase consistency check between blob database and filesystem storage.
Detects orphans, missing files, stuck processing, expired retention, and
batch integrity issues. Results are written to the notifications table.

Phases:
    1. Orphan Detection     — Files on disk with no DB entry
    2. Missing Files        — DB entries with no file on disk
    3. Stuck Processing     — Blobs stuck in non-terminal status > threshold
    4. Expired Retention    — Blobs past retention deadline (not yet cleaned up)
    5. Batch Integrity      — Batches with mismatched entry counts

Usage:
    engine = ReconciliationEngine(db, filesystem_root)
    report = engine.run()
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────

STUCK_THRESHOLD_HOURS = 24  # Blobs processing > 24h are stuck
RETENTION_GRACE_DAYS = 30   # Grace period after retention expires


@dataclass
class ReconciliationFinding:
    """A single reconciliation finding."""
    phase: str
    finding_type: str
    severity: str  # "info", "warning", "error"
    message: str
    blob_uuid: Optional[str] = None
    blob_path: Optional[str] = None
    details: Optional[str] = None


@dataclass
class ReconciliationReport:
    """Complete reconciliation run results."""
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0
    findings: List[ReconciliationFinding] = field(default_factory=list)
    phase_summaries: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_findings": self.total_findings,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "phase_summaries": self.phase_summaries,
            "findings": [
                {
                    "phase": f.phase,
                    "type": f.finding_type,
                    "severity": f.severity,
                    "message": f.message,
                    "blob_uuid": f.blob_uuid,
                    "blob_path": f.blob_path,
                }
                for f in self.findings
            ],
        }


class ReconciliationEngine:
    """
    5-phase blob consistency checker.

    Compares blob database state against filesystem storage to detect
    inconsistencies. Writes findings to the notifications table.
    """

    def __init__(self, db, filesystem_root: str):
        """
        Args:
            db: BlobDatabase instance (execute_query, execute_insert)
            filesystem_root: Root path of filesystem blob storage
        """
        self.db = db
        self.filesystem_root = filesystem_root

    def run(self) -> ReconciliationReport:
        """Execute all 5 reconciliation phases. Returns a report."""
        from uuid6 import uuid7
        run_id = f"recon-{uuid7().hex[:8]}"
        start = time.monotonic()
        now = datetime.now(timezone.utc).isoformat()

        report = ReconciliationReport(run_id=run_id, started_at=now)

        phases = [
            ("orphan_detection", self._phase_orphan_detection),
            ("missing_files", self._phase_missing_files),
            ("stuck_processing", self._phase_stuck_processing),
            ("expired_retention", self._phase_expired_retention),
            ("batch_integrity", self._phase_batch_integrity),
        ]

        for phase_name, phase_fn in phases:
            try:
                findings = phase_fn()
                report.findings.extend(findings)
                report.phase_summaries[phase_name] = {
                    "findings": len(findings),
                    "errors": sum(1 for f in findings if f.severity == "error"),
                    "warnings": sum(1 for f in findings if f.severity == "warning"),
                }
            except Exception as e:
                logger.error(f"Reconciliation phase '{phase_name}' failed: {e}")
                report.findings.append(ReconciliationFinding(
                    phase=phase_name,
                    finding_type="phase_error",
                    severity="error",
                    message=f"Phase failed: {str(e)}",
                ))
                report.phase_summaries[phase_name] = {"findings": 1, "errors": 1, "warnings": 0}

        report.completed_at = datetime.now(timezone.utc).isoformat()
        report.duration_seconds = time.monotonic() - start

        # Persist findings to notifications table
        self._persist_findings(report)

        logger.info(
            f"Reconciliation {run_id} complete: "
            f"{report.total_findings} findings "
            f"({report.error_count} errors, {report.warning_count} warnings) "
            f"in {report.duration_seconds:.1f}s"
        )

        return report

    # ── Phase 1: Orphan Detection ──────────────────────────────────────

    def _phase_orphan_detection(self) -> List[ReconciliationFinding]:
        """Find files on disk that have no corresponding DB entry."""
        findings = []

        blob_dir = os.path.join(self.filesystem_root, "files_blob")
        if not os.path.isdir(blob_dir):
            return findings

        # Get all blob_paths from DB
        db_paths = set()
        rows = self.db.execute_query("SELECT blob_path FROM file_entries")
        for row in rows:
            # blob_path format: /files_blob/{uuid}-filename
            # filesystem: {root}/files_blob/{uuid}-filename
            db_paths.add(row["blob_path"])

        # Walk filesystem
        for filename in os.listdir(blob_dir):
            if filename.endswith(".metadata.json"):
                continue  # Skip sidecar files
            fs_blob_path = f"/files_blob/{filename}"
            if fs_blob_path not in db_paths:
                findings.append(ReconciliationFinding(
                    phase="orphan_detection",
                    finding_type="orphaned_blob",
                    severity="warning",
                    message=f"File on disk has no DB entry: {filename}",
                    blob_path=fs_blob_path,
                ))

        return findings

    # ── Phase 2: Missing Files ─────────────────────────────────────────

    def _phase_missing_files(self) -> List[ReconciliationFinding]:
        """Find DB entries whose files are missing from disk."""
        findings = []

        rows = self.db.execute_query(
            "SELECT blob_uuid, blob_path FROM file_entries WHERE status != 'deleted'"
        )

        for row in rows:
            # Convert blob_path to filesystem path
            relative_path = row["blob_path"].lstrip("/")
            fs_path = os.path.join(self.filesystem_root, relative_path.replace("/", os.sep))

            if not os.path.exists(fs_path):
                findings.append(ReconciliationFinding(
                    phase="missing_files",
                    finding_type="missing_blob",
                    severity="error",
                    message=f"DB entry exists but file missing: {row['blob_path']}",
                    blob_uuid=row["blob_uuid"],
                    blob_path=row["blob_path"],
                ))

        return findings

    # ── Phase 3: Stuck Processing ──────────────────────────────────────

    def _phase_stuck_processing(self) -> List[ReconciliationFinding]:
        """Find blobs stuck in non-terminal status beyond threshold."""
        findings = []

        threshold_unix = int(time.time()) - (STUCK_THRESHOLD_HOURS * 3600)

        rows = self.db.execute_query(
            """SELECT blob_uuid, blob_path, status, processing_stage, uploaded_at_unix
               FROM file_entries
               WHERE status IN ('processing', 'uploaded')
                 AND uploaded_at_unix < ?""",
            (threshold_unix,),
        )

        for row in rows:
            hours_stuck = (int(time.time()) - row["uploaded_at_unix"]) / 3600
            findings.append(ReconciliationFinding(
                phase="stuck_processing",
                finding_type="stuck_blob",
                severity="warning",
                message=f"Blob stuck in '{row['status']}' for {hours_stuck:.1f}h",
                blob_uuid=row["blob_uuid"],
                blob_path=row["blob_path"],
                details=f"stage={row.get('processing_stage', 'unknown')}",
            ))

        return findings

    # ── Phase 4: Expired Retention ─────────────────────────────────────

    def _phase_expired_retention(self) -> List[ReconciliationFinding]:
        """Find blobs/batches past their retention deadline."""
        findings = []

        now_unix = int(time.time())

        # Check batches with retention
        rows = self.db.execute_query(
            """SELECT batch_uuid, status, retention_until_unix
               FROM blob_batches
               WHERE retention_until_unix IS NOT NULL
                 AND retention_until_unix < ?
                 AND deleted_at_unix IS NULL""",
            (now_unix,),
        )

        for row in rows:
            days_expired = (now_unix - row["retention_until_unix"]) / 86400
            findings.append(ReconciliationFinding(
                phase="expired_retention",
                finding_type="retention_expired",
                severity="info",
                message=f"Batch {row['batch_uuid']} retention expired {days_expired:.0f} days ago",
                blob_uuid=row["batch_uuid"],
            ))

        return findings

    # ── Phase 5: Batch Integrity ───────────────────────────────────────

    def _phase_batch_integrity(self) -> List[ReconciliationFinding]:
        """Check batch entry counts match actual blob_batch_entries rows."""
        findings = []

        rows = self.db.execute_query(
            """SELECT b.batch_display_id, b.file_count,
                      COUNT(be.file_display_id) as actual_count
               FROM blob_batches b
               LEFT JOIN blob_batch_entries be ON b.batch_display_id = be.batch_display_id
               GROUP BY b.batch_display_id
               HAVING b.file_count != actual_count"""
        )

        for row in rows:
            findings.append(ReconciliationFinding(
                phase="batch_integrity",
                finding_type="batch_mismatch",
                severity="warning",
                message=(
                    f"Batch {row['batch_display_id']} claims {row['file_count']} files "
                    f"but has {row['actual_count']} entries"
                ),
                blob_uuid=row["batch_display_id"],
            ))

        return findings

    # ── Persist Findings ───────────────────────────────────────────────

    def _persist_findings(self, report: ReconciliationReport) -> int:
        """Write findings to the notifications table. Returns count written."""
        now_unix = int(time.time())
        now_iso = datetime.now(timezone.utc).isoformat()
        count = 0

        for finding in report.findings:
            try:
                self.db.execute_insert(
                    """INSERT INTO notifications
                       (notification_type, severity, blob_uuid, blob_path,
                        message, details, is_resolved,
                        created_at_unix, created_at_iso, created_at, updated_at,
                        created_by_service)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
                    (
                        finding.finding_type, finding.severity,
                        finding.blob_uuid, finding.blob_path,
                        finding.message, finding.details,
                        now_unix, now_iso, now_iso, now_iso,
                        f"reconciliation/{report.run_id}",
                    ),
                )
                count += 1
            except Exception as e:
                logger.error(f"Failed to persist finding: {e}")

        return count
