# HeartBeat Service Contract — Backup Specification

**Version:** 1.0
**Date:** 2026-02-23
**Status:** AUTHORITATIVE
**Applies To:** All tiers — Standard, Pro, Enterprise
**Audience:** HeartBeat implementation team, Installer team, Float/SDK team
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## ⚠️ IMPLEMENTATION PRIORITY — READ FIRST

The following must be implemented **before all other backup features**
regardless of tier:

**Standard Tier — SDK and External API Backup Trigger**

Any Standard tier client — including those running Helium entirely on a
laptop — must be able to trigger a backup via:
1. The Float SDK dashboard (manual trigger button)
2. An external API call (for clients integrating backup into their own
   IT workflows, scripts, or cron jobs)

This is the minimum viable backup capability. It must ship before
automated scheduling, before restore UI, before retention management.
A client who cannot manually back up their data before a risky
operation — an update, a migration, end of month — has no safety net.

**Minimum implementation to ship first (in this order):**
1. SQLite Online Backup API wrapper — the engine behind everything
2. `POST /api/backup/trigger` — authenticated, works via SDK or external call
3. `GET /api/backup/runs/{run_id}` — poll progress and confirm success
4. `GET /api/backup/status` — last backup time, destination health
5. Installation-time backup destination configuration

Everything else in this document follows after these five are working
and tested.

---

## Overview

HeartBeat owns backup for all platform databases and blob storage.
This covers:
- Manual on-demand backups via SDK or external API call (Standard MVP)
- Scheduled automatic nightly backups
- Installation-time backup location configuration
- Retention management
- Restore operations

Backup is available on **all tiers including Standard.** It is not an
Enterprise-only feature.

**Key principle:** Helium never sends backup data to Pronalytics.
Backups go to a location the client owns and controls — a local path,
external drive, NAS, or cloud sync folder (OneDrive, Google Drive,
Dropbox) the client already has. No outbound connection to Pronalytics
is ever made for backup purposes.

---

## Table of Contents

1. [What Gets Backed Up](#1-what-gets-backed-up)
2. [Backup Destinations](#2-backup-destinations)
3. [Installation-Time Configuration](#3-installation-time-configuration)
4. [Manual Backup Trigger — SDK and External API](#4-manual-backup-trigger--sdk-and-external-api)
5. [Scheduled Automatic Backups](#5-scheduled-automatic-backups)
6. [Retention Policy](#6-retention-policy)
7. [Backup Failure Notifications](#7-backup-failure-notifications)
8. [Restore](#8-restore)
9. [Float Workstation Database Backup](#9-float-workstation-database-backup)
10. [Backup API Endpoint Reference](#10-backup-api-endpoint-reference)
11. [Backup Package Format](#11-backup-package-format)
12. [Environment Variables](#12-environment-variables)
13. [Implementation Notes](#13-implementation-notes)
14. [Implementation Status](#14-implementation-status)

---

## 1. What Gets Backed Up

### Priority Classification

| Database / Store | Priority | Why |
|---|---|---|
| `invoices.db` | CRITICAL | Core invoice records — FIRS compliance data |
| `blob.db` | CRITICAL | Uploaded source files, audit trail, blob metadata |
| `auth.db` | HIGH | Users, roles, sessions — painful to rebuild manually |
| `license.db` | HIGH | License record — needed for platform operation |
| `registry.db` | MEDIUM | Service config — recoverable from reinstall |
| `config.db` | MEDIUM | Platform config — recoverable but time-consuming |
| Blob storage filesystem | CRITICAL | Physical uploaded files (PDFs, XMLs, CSVs) |
| Float `sync.db` | HIGH | Upload queue state, pending items |
| Float `core_queue.db` | HIGH | SDK event queue |

### What Does NOT Need Backing Up
- HeartBeat and service binaries — reinstalled from update package
- Log files — informational only, not compliance-critical
- Temporary and working files — regenerable

### FIRS Compliance Context
Invoices successfully submitted to FIRS exist in both `invoices.db`
and FIRS's own systems. The most critical unprotected window is invoices
that have been processed but not yet submitted. Nightly backup means
worst-case exposure is one day of unsubmitted invoices — acceptable
for Standard tier. Higher tiers can increase backup frequency.

---

## 2. Backup Destinations

HeartBeat supports any writable filesystem path as a backup destination.
Set at installation time, changeable by Owner from the dashboard.

### Supported Destination Types

| Type | Example | Notes |
|---|---|---|
| Local path | `C:\HeliumBackups\abbey-001\` | Simple. Suits machines with a second drive. |
| External drive | `E:\HeliumBackups\` | Graceful failure + Owner notification if drive absent at backup time. |
| Network path / NAS | `\\192.168.1.100\helium-backups\abbey-001\` | Office LAN storage. No internet required. |
| Cloud sync folder | `C:\Users\IT\OneDrive\HeliumBackups\` | **Best for Standard laptop users.** Client's own sync client handles cloud upload. Pronalytics never touches the data. |

### Multiple Destinations
- **Standard:** One destination
- **Pro / Enterprise:** Primary and secondary. HeartBeat writes to both.
  Primary failure triggers Owner notification and secondary attempt.

---

## 3. Installation-Time Configuration

The installer must present a backup configuration step. Not optional
to display — the client may choose to skip and configure later from
the dashboard, but they must be presented with the choice.

### Installer UI

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BACKUP CONFIGURATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Helium backs up your invoice and compliance data
  automatically every night.

  Where should backups be saved?

  [Browse...]  C:\Users\IT\OneDrive\HeliumBackups\

  Backup schedule:  [ Nightly at 2:00 AM ▼ ]
  Keep backups for: [ 7 days ▼ ]

  [ Test this location ]   [ Skip — configure later ]
                                        [ Continue → ]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**"Test this location"** — writes a small test file, reads it back,
deletes it. Client gets confidence the path works before proceeding.

**"Skip for now"** — destination left unconfigured. HeartBeat shows a
persistent dashboard warning from first login: "Backup not configured
— your data is not protected." Warning persists until a destination
is set and a first successful backup has completed.

### Config Written to HeartBeat

Installer writes to config.db on completion:

```json
{ "service_name": "_shared", "config_key": "backup.primary_destination", "value": "C:\\Users\\IT\\OneDrive\\HeliumBackups\\" },
{ "service_name": "_shared", "config_key": "backup.schedule_cron",        "value": "0 2 * * *" },
{ "service_name": "_shared", "config_key": "backup.retain_days",          "value": "7" },
{ "service_name": "_shared", "config_key": "backup.enabled",              "value": "true" }
```

HeartBeat reads these on startup and schedules the APScheduler job.

---

## 4. Manual Backup Trigger — SDK and External API

### ⚠️ This is the first feature to implement. All others follow.

The manual trigger is a **Standard tier feature**. It is available to
any Owner or Admin and can be called:
- From the Float dashboard (button in backup settings)
- From an external script or IT automation tool via authenticated API call

This means a client's IT team can integrate Helium backup into their
existing backup orchestration — a cron job, a PowerShell script, a
backup tool — without needing Helium to have any knowledge of their
IT environment. They call the endpoint, Helium backs up, they poll
for completion, done.

### Who Can Trigger

| Role | Can Trigger |
|---|---|
| Owner | Always |
| Admin | If `backup.trigger` permission is assigned |
| Operator | No |
| Support | No |
| External system (API key) | If the API key has `backup.trigger` scope |

### External API Authentication
An external system calling the backup trigger uses the same
API key + secret Bearer token model used by all Helium services:

```
POST /api/backup/trigger
Authorization: Bearer {api_key}:{api_secret}
Content-Type: application/json
```

The API key must be generated in HeartBeat and scoped to `backup.trigger`.
This allows a client's IT team to create a dedicated backup credential
for their automation — separate from any user account.

### Trigger Request

```json
{
  "destination_override": null,
  "include_blobs": true,
  "label": "Pre-update backup — Feb 23 2026"
}
```

| Field | Description |
|---|---|
| `destination_override` | null = use configured destination. Or supply an alternative path for this run only. |
| `include_blobs` | false = databases only (faster, smaller). true = databases + blob storage files. |
| `label` | Optional human label. Labeled backups are never auto-deleted by retention policy. |

### Trigger Response (202 Accepted)

Backup runs asynchronously. Response returns immediately:

```json
{
  "backup_run_id": "backup_abbey-001_20260223_143000",
  "status": "running",
  "started_at": "2026-02-23T14:30:00Z",
  "destination": "C:\\Users\\IT\\OneDrive\\HeliumBackups\\",
  "estimated_completion_seconds": 45,
  "poll_url": "/api/backup/runs/backup_abbey-001_20260223_143000"
}
```

### Polling Progress

```
GET /api/backup/runs/{backup_run_id}
Authorization: Bearer {jwt} or Bearer {api_key}:{api_secret}
```

In progress:
```json
{
  "backup_run_id": "backup_abbey-001_20260223_143000",
  "status": "running",
  "progress": {
    "current_file": "invoices.db",
    "files_completed": 3,
    "files_total": 7,
    "percent": 42
  }
}
```

Completed:
```json
{
  "backup_run_id": "backup_abbey-001_20260223_143000",
  "status": "completed",
  "started_at": "2026-02-23T14:30:00Z",
  "completed_at": "2026-02-23T14:30:52Z",
  "duration_seconds": 52,
  "destination": "C:\\Users\\IT\\OneDrive\\HeliumBackups\\",
  "label": "Pre-update backup — Feb 23 2026",
  "files_backed_up": 7,
  "total_size_bytes": 284723190,
  "manifest_path": "backup_abbey-001_20260223_143000/manifest.json"
}
```

Failed:
```json
{
  "backup_run_id": "backup_abbey-001_20260223_143000",
  "status": "failed",
  "started_at": "2026-02-23T14:30:00Z",
  "failed_at": "2026-02-23T14:30:08Z",
  "error_code": "DESTINATION_NOT_WRITABLE",
  "error_message": "Cannot write to C:\\Users\\IT\\OneDrive\\HeliumBackups\\ — access denied"
}
```

---

## 5. Scheduled Automatic Backups

Default schedule: nightly at 2:00 AM local time. Configurable by Owner.

### Backup Process

```
1. APScheduler fires the backup job

2. HeartBeat checks:
   - backup.enabled = true
   - destination is configured and writable
   → If not: log failure, notify Owner, exit

3. Generate backup run ID:
   backup_{tenant_id}_{YYYYMMDD_HHMMSS}

4. For each CRITICAL and HIGH priority database:
   → SQLite Online Backup API (live copy, zero downtime)
   → Write to: {destination}/{run_id}/{db_name}.db

5. Copy blob storage filesystem tree:
   → {destination}/{run_id}/blobs/
   → Done last — largest operation

6. Write manifest.json with SHA256 hashes of every file

7. Record backup_run in blob.db

8. Apply retention policy

9. Write audit event: backup.completed | backup.failed
```

### Zero Downtime
SQLite's Online Backup API copies a live database without stopping
any service, pausing writes, or locking the database for more than
microseconds at a time. There is no service disruption during backup.

---

## 6. Retention Policy

### Default (All Tiers)

| Backup Type | Retention |
|---|---|
| Daily backups | 7 most recent |
| Weekly backups (Sunday scheduled run) | 4 most recent |
| Manual backups with a label | Never auto-deleted |
| Manual backups without a label | Treated as daily |

Retention runs after every backup. Old folders deleted from destination.

Labeled backups are permanent until an Owner deletes them manually.

### Configurable by Owner
- Daily retention: 3–30 days
- Weekly retention: 1–12 weeks
- Auto-delete labeled backups: off by default

---

## 7. Backup Failure Notifications

All notifications use the tenant's configured notification channel
(email, SMS, or WhatsApp — same as auth notifications).

| Condition | Notification |
|---|---|
| Destination not configured | Persistent dashboard warning from first login |
| Destination not writable | Immediate Owner notification |
| Single run fails | Owner notification with reason |
| Two consecutive failures | Owner notification — escalated urgency |
| No successful backup in 48 hours | Owner notification — urgent |
| Destination < 10% free | Owner warning |
| Destination < 2% free | Owner urgent notification, backup suspended |

### Dashboard Health Indicator (Float)
- 🟢 Healthy — last backup successful, within schedule
- 🟡 Warning — older than expected, or disk space warning
- 🔴 Critical — failing, not configured, or no successful backup in 48 hours

---

## 8. Restore

Owner-only. Requires Immediate re-auth (highest step-up tier).
Destructive and irreversible — current database replaced with backup.

### Restore Flow

```
1. Owner selects backup run from dashboard
2. Owner shown: what will be overwritten, when backup was taken
3. Owner performs Immediate re-auth (password + MFA if enabled)
4. HeartBeat stops affected services
   → All Float instances show "System restore in progress"
5. HeartBeat verifies SHA256 hashes in manifest
   → Hash mismatch = restore blocked, Owner notified
6. HeartBeat copies backup files over current databases
7. HeartBeat runs migration check
   → Re-applies any migrations the restored DB is missing
8. HeartBeat restarts services in correct startup order
9. HeartBeat verifies health of each service
10. Audit event: restore.completed with full context
    (who, which backup, what was restored, timestamp)
```

### Partial Restore
Owner can restore individual databases rather than full restore.
Same Immediate re-auth requirement applies.

---

## 9. Float Workstation Database Backup

Float's databases (sync.db, core_queue.db) live on the client
workstation, not on the HeartBeat machine. Float backs them up
to HeartBeat on a schedule and on key events.

### Float Backup Endpoint

```
POST /api/backup/float/{instance_id}
Authorization: Bearer {api_key}:{api_secret}
Body: multipart/form-data
  - sync_db: <sync.db file>
  - core_queue_db: <core_queue.db file>
```

HeartBeat stores these as `blob_category: float_backup` and includes
them in the nightly backup run to the configured destination.

### When Float Triggers Its Own Backup
- After every successful upload batch
- On clean application shutdown
- On the nightly schedule (if Float is running)
- Manually triggered by user from Float dashboard

### Recovery After Workstation Loss

```
1. Reinstall Float on new machine
2. Float enrolls with HeartBeat (new enrollment token)
3. GET /api/backup/float/{instance_id}/latest
4. Float downloads and restores sync.db and core_queue.db
5. Float resumes from last known state
```

### What Cannot Be Recovered
Uploads staged in Float's local store but never sent to Relay before
the machine was lost. These exist only on the workstation. Float should
upload to Relay promptly and not accumulate large local queues.

---

## 10. Backup API Endpoint Reference

| Method | Endpoint | Description | Auth | Priority |
|---|---|---|---|---|
| POST | `/api/backup/trigger` | Trigger manual backup | Owner/Admin or API key (5-min step-up for user) | **MVP** |
| GET | `/api/backup/runs/{run_id}` | Get run details and progress | Owner/Admin or API key | **MVP** |
| GET | `/api/backup/status` | Current health, last run, destination | Admin+ | **MVP** |
| GET | `/api/backup/config` | Get backup configuration | Admin+ | **MVP** |
| PUT | `/api/backup/config` | Update backup configuration | Owner | Near-term |
| POST | `/api/backup/config/test` | Test destination writability | Owner/Admin | Near-term |
| GET | `/api/backup/runs` | List all backup runs | Admin+ | Near-term |
| GET | `/api/backup/runs/{run_id}/manifest` | List files in a backup | Admin+ | Near-term |
| DELETE | `/api/backup/runs/{run_id}` | Delete a backup run | Owner/Admin | Near-term |
| POST | `/api/backup/runs/{run_id}/restore` | Full restore | Owner (Immediate re-auth) | Near-term |
| POST | `/api/backup/runs/{run_id}/restore/partial` | Restore specific databases | Owner (Immediate re-auth) | Near-term |
| GET | `/api/backup/runs/{run_id}/restore/status` | Poll restore progress | Owner | Near-term |
| POST | `/api/backup/float/{instance_id}` | Float workstation DB backup | Service API key | Near-term |
| GET | `/api/backup/float/{instance_id}/latest` | Retrieve latest Float backup | Service API key | Near-term |

### GET /api/backup/status Full Response

```json
{
  "backup_enabled": true,
  "destination_configured": true,
  "destination_path": "C:\\Users\\IT\\OneDrive\\HeliumBackups\\",
  "destination_writable": true,
  "destination_free_gb": 42.3,
  "schedule": "0 2 * * *",
  "schedule_human": "Nightly at 2:00 AM",
  "retain_days": 7,
  "retain_weeks": 4,
  "last_run": {
    "run_id": "backup_abbey-001_20260222_020000",
    "status": "completed",
    "completed_at": "2026-02-22T02:01:43Z",
    "duration_seconds": 103,
    "total_size_bytes": 284723190,
    "triggered_by": "schedule"
  },
  "next_scheduled_run": "2026-02-24T02:00:00Z",
  "health": "healthy",
  "total_backup_runs": 14,
  "total_backup_size_bytes": 1823948201
}
```

---

## 11. Backup Package Format

```
{destination}/
└── backup_{tenant_id}_{YYYYMMDD_HHMMSS}/
    ├── manifest.json
    ├── invoices.db
    ├── blob.db
    ├── auth.db              ← encrypted — key NOT included in backup
    ├── registry.db
    ├── config.db
    ├── license.db
    └── blobs/
        └── {tenant_id}/
            └── {year}/{month}/{sha256}.bin
```

### manifest.json

```json
{
  "run_id": "backup_abbey-001_20260223_020000",
  "tenant_id": "abbey-001",
  "helium_version": "1.2.0",
  "created_at": "2026-02-23T02:00:00Z",
  "completed_at": "2026-02-23T02:01:43Z",
  "label": null,
  "triggered_by": "schedule",
  "triggered_by_user_id": null,
  "files": [
    {
      "name": "invoices.db",
      "priority": "critical",
      "size_bytes": 84723190,
      "sha256": "a1b2c3d4e5f6...",
      "backed_up_at": "2026-02-23T02:00:12Z"
    },
    {
      "name": "blob.db",
      "priority": "critical",
      "size_bytes": 12048192,
      "sha256": "e5f6a7b8c9d0...",
      "backed_up_at": "2026-02-23T02:00:15Z"
    }
  ],
  "blob_file_count": 1247,
  "blob_total_size_bytes": 187951818,
  "total_size_bytes": 284723200
}
```

SHA256 hashes are verified before any restore operation. Hash mismatch
blocks the restore entirely.

### auth.db Special Handling
auth.db is SQLCipher-encrypted. The backup copy is also encrypted.
The encryption key (`HEARTBEAT_AUTH_DB_KEY`) is NOT stored in the
backup folder. The client must store this key separately — in a
password manager or secure vault — entirely independent of the backup.

**This must be communicated clearly during installation.**
Without the key, the auth.db backup cannot be restored.

---

## 12. Environment Variables

| Env Var | Default | Description |
|---|---|---|
| `HEARTBEAT_BACKUP_ENABLED` | `true` | Enable/disable backup system |
| `HEARTBEAT_BACKUP_DESTINATION` | (empty) | Primary backup destination path |
| `HEARTBEAT_BACKUP_DESTINATION_2` | (empty) | Secondary destination (Pro/Enterprise) |
| `HEARTBEAT_BACKUP_SCHEDULE` | `0 2 * * *` | Cron expression for automatic backup |
| `HEARTBEAT_BACKUP_RETAIN_DAYS` | `7` | Daily backup retention in days |
| `HEARTBEAT_BACKUP_RETAIN_WEEKS` | `4` | Weekly backup retention in weeks |
| `HEARTBEAT_BACKUP_INCLUDE_BLOBS` | `true` | Include blob storage filesystem in backup |
| `HEARTBEAT_BACKUP_WARN_FREE_GB` | `10` | Warn Owner when destination free space below this |
| `HEARTBEAT_BACKUP_SUSPEND_FREE_GB` | `2` | Suspend backup when free space below this |

Owner dashboard configuration in config.db takes precedence over env vars.

---

## 13. Implementation Notes

### SQLite Online Backup API (Python)

```python
import sqlite3

def backup_database(source_path: str, dest_path: str,
                    progress_callback=None) -> None:
    """
    Live backup of a SQLite database using the Online Backup API.
    Safe to run against an actively-written database.
    pages=100 means HeartBeat copies 100 pages then yields briefly
    — database is never locked for more than a few milliseconds.
    """
    source = sqlite3.connect(source_path)
    dest = sqlite3.connect(dest_path)
    source.backup(dest, pages=100, progress=progress_callback)
    dest.close()
    source.close()
```

### APScheduler Integration

```python
from apscheduler.triggers.cron import CronTrigger

scheduler.add_job(
    run_scheduled_backup,
    CronTrigger.from_crontab(backup_config.schedule_cron),
    id='nightly_backup',
    replace_existing=True   # Owner can update schedule without restart
)
```

### backup_runs Tracking Table (in blob.db)

```sql
CREATE TABLE backup_runs (
    run_id               TEXT PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    triggered_by         TEXT NOT NULL,       -- 'schedule' | 'manual' | 'api'
    triggered_by_user_id TEXT,                -- null for scheduled and api key
    triggered_by_api_key TEXT,                -- null for user-triggered
    label                TEXT,
    destination          TEXT NOT NULL,
    include_blobs        BOOLEAN NOT NULL DEFAULT 1,
    status               TEXT NOT NULL,       -- running | completed | failed
    started_at           TEXT NOT NULL,
    completed_at         TEXT,
    duration_seconds     INTEGER,
    files_backed_up      INTEGER,
    total_size_bytes     INTEGER,
    error_code           TEXT,
    error_message        TEXT,
    manifest_path        TEXT
);
```

---

## 14. Implementation Status

| Component | Priority | Status |
|---|---|---|
| SQLite Online Backup API wrapper | **Standard MVP** | ❌ Not built |
| backup_runs table in blob.db | **Standard MVP** | ❌ Not built |
| POST /api/backup/trigger (user + API key) | **Standard MVP** | ❌ Not built |
| GET /api/backup/runs/{run_id} | **Standard MVP** | ❌ Not built |
| GET /api/backup/status | **Standard MVP** | ❌ Not built |
| GET /api/backup/config | **Standard MVP** | ❌ Not built |
| Installer backup configuration step | **Standard MVP** | ❌ Not built |
| APScheduler nightly backup job | Near-term | ❌ Not built |
| PUT /api/backup/config | Near-term | ❌ Not built |
| POST /api/backup/config/test | Near-term | ❌ Not built |
| GET /api/backup/runs | Near-term | ❌ Not built |
| Retention policy enforcement | Near-term | ❌ Not built |
| SHA256 manifest generation and verification | Near-term | ❌ Not built |
| Backup failure notifications | Near-term | ❌ Not built |
| Dashboard backup health indicator | Near-term | ❌ Not built |
| POST /api/backup/runs/{run_id}/restore | Near-term | ❌ Not built |
| POST /api/backup/runs/{run_id}/restore/partial | Near-term | ❌ Not built |
| POST /api/backup/float/{instance_id} | Near-term | ❌ Not built |
| GET /api/backup/float/{instance_id}/latest | Near-term | ❌ Not built |
| Multiple destinations (Pro/Enterprise) | Future | ❌ Not built |
| Secondary destination failover | Future | ❌ Not built |

---

*End of Backup Specification.*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-02-23 | Version: 1.0*
