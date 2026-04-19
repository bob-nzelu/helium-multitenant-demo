# HeartBeat Service Contract — Software Update Specification

**Version:** 1.0
**Date:** 2026-02-23
**Status:** AUTHORITATIVE
**Applies To:** All tiers — Standard, Pro, Enterprise
**Audience:** HeartBeat implementation team, Installer team, Pronalytics Engineering
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## Overview

HeartBeat owns software update delivery and application for the entire
Helium platform. This covers how Pronalytics delivers updates to client
environments, how HeartBeat verifies, stages, and applies them, how
child services are updated with minimal disruption, and how Float
applications on workstations receive updates from HeartBeat internally.

**Core constraint:** Helium never makes outbound connections to Pronalytics
for updates. Updates are delivered by Pronalytics to the client via a
secure out-of-band channel. The client drops the update package into a
watched folder. HeartBeat does the rest.

**Trust model:** Every update package is cryptographically signed by
Pronalytics using the same Ed25519 all-Father private key used for
license signing. HeartBeat verifies the signature using the Pronalytics
public key baked into the binary at build time. A package that does not
carry a valid Pronalytics signature is rejected outright — not applied,
not partially processed, quarantined and Owner notified.

---

## Table of Contents

1. [Update Delivery — How Packages Reach the Client](#1-update-delivery)
2. [Update Package Format](#2-update-package-format)
3. [HeartBeat Update Detection](#3-heartbeat-update-detection)
4. [Signature Verification](#4-signature-verification)
5. [Owner Approval](#5-owner-approval)
6. [Update Application — Server Services](#6-update-application--server-services)
7. [HeartBeat Self-Update](#7-heartbeat-self-update)
8. [Float Application Updates](#8-float-application-updates)
9. [Rollback](#9-rollback)
10. [Update API Endpoint Reference](#10-update-api-endpoint-reference)
11. [Environment Variables](#11-environment-variables)
12. [Implementation Status](#12-implementation-status)

---

## 1. Update Delivery

Pronalytics delivers update packages to clients via a secure
out-of-band channel — email, SFTP, or a client-facing portal.
The delivery method is agreed with each client at onboarding.

The client's IT team receives the package, reviews the release notes
(included in the package manifest), and places the `.hpkg` file into
HeartBeat's watched updates folder:

```
Windows: C:\HeliumData\{tenant_id}\updates\
Linux:   /var/helium/{tenant_id}/updates/
```

HeartBeat monitors this folder continuously. On detecting a new `.hpkg`
file, it begins the verification and staging process automatically.

### For Cloud-Hosted Tenants
Clients on their own cloud tenancy (AWS, Azure, GCP) may optionally
configure a network path or mounted storage location as the updates
folder. The delivery mechanism from Pronalytics remains the same —
out-of-band — but the drop location can be a cloud-native path
the client controls.

### Optional Pull Mechanism (Future)
For clients who prefer it and whose network policy permits it, a
future version may support HeartBeat checking a Pronalytics-hosted
update endpoint over a secured, client-configured connection. This
is strictly opt-in and off by default. It is not part of the current
design.

---

## 2. Update Package Format

Update packages use the `.hpkg` (Helium Package) extension.
Internally they are structured archives.

```
helium-update-{version}.hpkg
├── manifest.json          ← version metadata, changelog, affected services
├── manifest.signature     ← Ed25519 signature of manifest.json
├── heartbeat/             ← HeartBeat files (present if HeartBeat changed)
│   └── ...
├── relay/                 ← Relay files (present if Relay changed)
│   └── ...
├── core/                  ← Core files (present if Core changed)
│   └── ...
├── his/                   ← HIS files (present if HIS changed)
│   └── ...
├── edge/                  ← Edge files (present if Edge changed)
│   └── ...
├── float/                 ← Float installer (present if Float changed)
│   └── float-setup-{version}.exe (or .AppImage / .dmg)
└── migrations/            ← Database migration files for any changed service
    └── ...
```

### manifest.json

```json
{
  "package_id": "helium-update-1.3.0",
  "version": "1.3.0",
  "min_current_version": "1.2.0",
  "released_at": "2026-02-20T00:00:00Z",
  "released_by": "Pronalytics Limited",
  "is_security_update": false,
  "requires_restart": true,
  "changelog": "Improved invoice deduplication. Fixed Relay timeout on large batches. Updated FIRS schema to v2.1.",
  "affected_services": ["relay", "core"],
  "float_version": "1.3.0",
  "float_update_required": false,
  "migrations": [
    { "service": "core", "file": "migrations/core_0023_firs_v21_schema.sql" }
  ]
}
```

`min_current_version` — if the installed version is older than this,
HeartBeat blocks the update and notifies the Owner: "This update requires
version {min} or later. Please apply the intermediate update first."

`float_update_required` — if true, Float instances will be blocked from
connecting to HeartBeat until they update. If false, the update is
offered but not mandatory.

---

## 3. HeartBeat Update Detection

HeartBeat runs a filesystem watcher on the updates folder:

- **Windows:** `ReadDirectoryChangesW` via `watchdog` library
- **Linux:** `inotify` via `watchdog` library

On detecting a new `.hpkg` file:

```
1. Read manifest.json from package
2. Check package_id has not already been applied
   → If already applied: log, ignore, do not re-apply
3. Verify manifest.signature (see Section 4)
4. Check min_current_version compatibility
5. Check affected services are known in service registry
6. Stage package contents to a temporary staging area
7. Notify Owner: "Update {version} is ready to review and apply"
8. If auto_apply is enabled in config: proceed to application
   If not (default): wait for Owner approval
```

---

## 4. Signature Verification

HeartBeat verifies the Ed25519 signature on `manifest.json` using
the Pronalytics public key baked into the binary at build time.

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
import base64

PRONALYTICS_PUBLIC_KEY_PEM = b"""
-----BEGIN PUBLIC KEY-----
{key baked in at build time}
-----END PUBLIC KEY-----
"""

def verify_package_signature(manifest_bytes: bytes,
                              signature_b64: str) -> bool:
    public_key = load_pem_public_key(PRONALYTICS_PUBLIC_KEY_PEM)
    signature = base64.b64decode(signature_b64)
    try:
        public_key.verify(signature, manifest_bytes)
        return True
    except Exception:
        return False
```

**On signature failure:**
- Package is moved to `{updates_folder}/quarantine/`
- Owner notified immediately via configured notification channel
- Audit event written: `update.signature_failed`
- Package is never applied

This is the same trust anchor as the license and the all-Father key.
One Ed25519 keypair governs the entire Pronalytics trust relationship.

---

## 5. Owner Approval

By default, HeartBeat does not auto-apply updates. It notifies the
Owner and waits for explicit approval.

Owner receives notification:
> "Helium update 1.3.0 is ready to apply.
> Affected services: Relay, Core.
> Changelog: Improved invoice deduplication. Fixed Relay timeout on large batches.
> [Review and Apply] [Dismiss]"

Owner approves from the Float dashboard:

```
POST /api/updates/{package_id}/apply
Authorization: Bearer {jwt}    ← Immediate re-auth required
```

### Auto-Apply Configuration
Owners can enable auto-apply for non-breaking updates:

```
PUT /api/updates/config
{ "auto_apply": true, "auto_apply_security_only": false }
```

Security updates (`is_security_update: true`) can be configured to
auto-apply regardless of the general auto-apply setting.

Auto-apply still verifies the signature, checks compatibility, and
runs the full application sequence — it just skips the manual approval
step.

---

## 6. Update Application — Server Services

HeartBeat updates server services one at a time in reverse dependency
order, then restarts them in forward dependency order.

### Service Update Sequence

```
Update order (reverse dependency — least critical first):
1. Edge
2. HIS
3. Relay
4. Core
5. HeartBeat (see Section 7 — special case)

Restart order (forward dependency):
1. Core
2. Relay
3. HIS
4. Edge
```

### Per-Service Update Process

For each affected service:

```
1. HeartBeat signals the service to drain:
   POST /internal/prepare-update
   → Service returns 503 on new incoming requests
   → In-flight requests are allowed to complete (grace period: 30s)
   → Service responds: { "ready": true } when drained

2. HeartBeat waits for drain confirmation or grace period expiry

3. HeartBeat stops the service process

4. HeartBeat creates a rollback snapshot:
   → Copies current service binaries to {staging}/rollback/{service}/

5. HeartBeat applies new files from package to service directory

6. HeartBeat runs any new database migrations for this service
   → Uses existing migrator framework
   → Migration failure = immediate rollback for this service

7. HeartBeat starts the service

8. HeartBeat polls /health until healthy (timeout: 60s)
   → Healthy: proceed to next service
   → Unhealthy: rollback this service, halt update, notify Owner

9. Service registered back as active in service registry
   → Other services resume routing to it
```

### During the Update Window
- HeartBeat marks each service as `status: updating` in the registry
- Other services that depend on an updating service queue their calls
  briefly or return appropriate retry responses
- Float shows a subtle status indicator: "System maintenance in progress"
  but remains functional for operations not touching the updating service

---

## 7. HeartBeat Self-Update

HeartBeat cannot update itself while running — it is the process
manager. Self-update requires a brief controlled restart.

### Self-Update Process

```
1. HeartBeat updates all child services first (Section 6)
2. All child services confirmed healthy on new version

3. HeartBeat notifies all connected Float clients:
   SSE event: { "type": "heartbeat_restart_imminent", "seconds": 30 }
   → Float SDK shows: "Helium updating — brief pause in 30 seconds"

4. HeartBeat creates rollback snapshot of its own binaries

5. HeartBeat applies new HeartBeat files to a staging location

6. HeartBeat writes a restart marker file:
   {data_path}/.pending_update
   Contains: new binary path, rollback path, package_id

7. HeartBeat signals OS service manager to restart:
   Windows: net stop HeliumHeartBeat && net start HeliumHeartBeat
   Linux:   systemctl restart helium-heartbeat

8. OS service manager restarts HeartBeat from new binary

9. On startup, HeartBeat detects .pending_update marker:
   → Verifies it started successfully
   → Clears marker
   → Writes audit event: update.heartbeat.completed
   → Notifies Owner of successful update

10. If startup fails on new binary:
    → OS service manager restarts (NSSM / systemd handles this)
    → HeartBeat detects .pending_update on retry startup
    → If retry count > 2: rolls back to snapshot binary
    → Clears marker, notifies Owner of rollback
```

### Restart Duration
HeartBeat restart is typically 3-8 seconds. Child services continue
running during this window — they cache their registry config and
handle brief HeartBeat unavailability gracefully.

---

## 8. Float Application Updates

Float applications on client workstations receive updates from
HeartBeat internally — no connection to Pronalytics required from
the workstation.

### Version Check on Float Startup

```
GET /api/updates/float/version
Authorization: Bearer {api_key}:{api_secret}
```

Response:
```json
{
  "current_version": "1.2.0",
  "available_version": "1.3.0",
  "update_required": false,
  "changelog": "Improved upload progress display. Minor UI fixes.",
  "download_url": "/api/updates/float/download"
}
```

If `update_required: true` — Float blocks launch and forces update.
If `update_required: false` and update is available — Float shows
an "Update available" notification in the dashboard. User decides when.

### Float Update Download

```
GET /api/updates/float/download
Authorization: Bearer {api_key}:{api_secret}
```

HeartBeat streams the Float installer from its blob storage.
Float launcher receives the installer, applies it, and restarts.

### HeartBeat Storing Float Installers
When a `.hpkg` package includes a Float installer, HeartBeat
extracts and stores it to blob storage tagged as
`blob_category: float_installer`. It is served on demand to
Float instances that check for updates.

### Float Update Flow (User Experience)

```
Float startup → version check → "Update available"
User clicks "Update Now" in dashboard
→ Float downloads installer from HeartBeat
→ Float launcher exits, applies installer, relaunches
→ User is back on new version
Total interruption: 30-60 seconds
```

---

## 9. Rollback

Every update creates a rollback snapshot before applying changes.
If any step fails, HeartBeat rolls back automatically.

### Automatic Rollback Triggers
- Service fails health check after update
- Database migration fails during update
- HeartBeat fails to start on new binary (after 2 OS restart attempts)

### Manual Rollback
Owner can manually rollback to the previous version from the dashboard
within 24 hours of an update, as long as the snapshot files still exist.

```
POST /api/updates/{package_id}/rollback
Authorization: Bearer {jwt}    ← Immediate re-auth required
```

### Rollback Scope
Rollback is per-service or full. A failed Relay update rolls back
only Relay — Core, HIS, Edge remain on the new version if they
updated successfully.

Database migrations run during update cannot be automatically
reversed — migration rollback requires manual DBA intervention.
HeartBeat will warn the Owner if a rollback would leave databases
at a newer schema version than the binary being restored.

---

## 10. Update API Endpoint Reference

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/api/updates` | List available and applied updates | Admin+ |
| GET | `/api/updates/{package_id}` | Get update details and status | Admin+ |
| POST | `/api/updates/{package_id}/apply` | Apply a staged update | Owner (Immediate re-auth) |
| POST | `/api/updates/{package_id}/rollback` | Roll back an applied update | Owner (Immediate re-auth) |
| GET | `/api/updates/config` | Get update configuration | Admin+ |
| PUT | `/api/updates/config` | Update auto-apply configuration | Owner |
| GET | `/api/updates/float/version` | Float version check | Service API key |
| GET | `/api/updates/float/download` | Download Float installer | Service API key |

---

## 11. Environment Variables

| Env Var | Default | Description |
|---|---|---|
| `HEARTBEAT_UPDATES_FOLDER` | `{data_path}/updates/` | Watched folder for incoming .hpkg files |
| `HEARTBEAT_UPDATES_QUARANTINE` | `{data_path}/updates/quarantine/` | Failed signature packages moved here |
| `HEARTBEAT_UPDATES_STAGING` | `{data_path}/updates/staging/` | Extracted packages awaiting application |
| `HEARTBEAT_UPDATES_AUTO_APPLY` | `false` | Auto-apply updates without Owner approval |
| `HEARTBEAT_UPDATES_AUTO_APPLY_SECURITY` | `true` | Auto-apply security updates specifically |
| `HEARTBEAT_UPDATES_DRAIN_GRACE_SECONDS` | `30` | Grace period for service drain before forced stop |
| `HEARTBEAT_UPDATES_HEALTH_TIMEOUT_SECONDS` | `60` | Max wait for service health after restart |

---

## 12. Implementation Status

| Component | Status |
|---|---|
| Updates folder watcher (watchdog) | ❌ Not built |
| .hpkg package extraction | ❌ Not built |
| Ed25519 signature verification | ❌ Not built (key infrastructure in auth work) |
| Compatibility check (min_current_version) | ❌ Not built |
| Owner notification on new package | ❌ Not built |
| POST /api/updates/{package_id}/apply | ❌ Not built |
| Service drain signal (/internal/prepare-update) | ❌ Not built |
| Per-service rollback snapshot | ❌ Not built |
| Database migration on update | ✅ Migrator framework exists |
| Service health check after update | ✅ Health polling exists |
| HeartBeat self-update (.pending_update marker) | ❌ Not built |
| Float version check endpoint | ❌ Not built |
| Float installer storage and serving | ❌ Not built |
| Auto-apply configuration | ❌ Not built |
| Manual rollback endpoint | ❌ Not built |

---

*End of Software Update Specification.*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-02-23 | Version: 1.0*
