# HeartBeat Service Contract — Service Lifecycle Specification

**Version:** 1.1
**Date:** 2026-03-04
**Status:** AUTHORITATIVE
**Applies To:** All tiers — Standard, Pro, Enterprise
**Audience:** HeartBeat implementation team, Installer team, DevOps
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## Overview

HeartBeat is not just a registry and auth service — its name reflects
its original and most fundamental purpose: keeping every Helium service
alive. HeartBeat starts with the operating system and is responsible
for starting, monitoring, and restarting all other Helium services.

Nothing in the Helium platform starts independently. Everything starts
through HeartBeat.

---

## Table of Contents

1. [OS-Level Presence](#1-os-level-presence)
2. [Service Registry and Startup Order](#2-service-registry-and-startup-order)
3. [Internal Process Manager](#3-internal-process-manager)
4. [Restart Policies](#4-restart-policies)
5. [Health Monitoring](#5-health-monitoring)
6. [Graceful Shutdown](#6-graceful-shutdown)
7. [Startup and Shutdown Sequences](#7-startup-and-shutdown-sequences)
8. [Enterprise High Availability](#8-enterprise-high-availability)
9. [Lifecycle API Endpoint Reference](#9-lifecycle-api-endpoint-reference)
10. [Environment Variables](#10-environment-variables)
11. [Implementation Status](#11-implementation-status)

---

## 1. OS-Level Presence

HeartBeat registers itself as an OS-level service on installation.
It starts automatically when the machine boots — before any user logs in.

### Windows — NSSM

HeartBeat uses **NSSM (Non-Sucking Service Manager)** to register as a
Windows Service.

```
nssm install HeliumHeartBeat "{install_path}\heartbeat\heartbeat.exe"
nssm set HeliumHeartBeat AppDirectory "{install_path}\heartbeat"
nssm set HeliumHeartBeat AppParameters "--config {data_path}\config.env"
nssm set HeliumHeartBeat Start SERVICE_AUTO_START
nssm set HeliumHeartBeat AppRestartDelay 5000
nssm set HeliumHeartBeat AppStdout "{log_path}\heartbeat.log"
nssm set HeliumHeartBeat AppStderr "{log_path}\heartbeat_error.log"
```

NSSM handles HeartBeat's own restarts if it crashes — with a 5-second
delay before restart. Child services (Core, Relay, HIS, Edge) are NOT
registered as individual Windows Services. HeartBeat's internal process
manager owns them entirely.

### Linux — systemd

HeartBeat registers as a **systemd unit**:

```ini
[Unit]
Description=Helium HeartBeat Service
After=network.target
Wants=network.target

[Service]
Type=simple
User=helium
WorkingDirectory={install_path}/heartbeat
ExecStart={install_path}/heartbeat/heartbeat --config {data_path}/config.env
Restart=always
RestartSec=5
StandardOutput=append:{log_path}/heartbeat.log
StandardError=append:{log_path}/heartbeat_error.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable helium-heartbeat
systemctl start helium-heartbeat
```

Child services on Linux are managed by HeartBeat's internal process
manager — NOT as individual systemd units. HeartBeat owns their restart
logic, backoff, and ordering.

---

## 2. Service Registry and Startup Order

HeartBeat maintains a **startup manifest** — the definitive record of
which services exist, their executable paths, their arguments, and
their startup order. This is stored in registry.db and set at
installation time.

### Startup Order (Forward Dependency)

```
Priority 0  HeartBeat         (already running — manages all others)
Priority 1  Core              (other services depend on Core)
Priority 2  Relay             (depends on Core being reachable)
Priority 2  HIS               (depends on Core)
Priority 3  Edge              (depends on Core and Relay)
Priority 4  Float             (user-facing — starts last, not auto-restarted)
```

Services at the same priority level can start in parallel.

### Service Manifest Entry (registry.db)

```sql
CREATE TABLE managed_services (
    service_name        TEXT PRIMARY KEY,
    instance_id         TEXT NOT NULL,
    executable_path     TEXT NOT NULL,
    working_directory   TEXT NOT NULL,
    arguments           TEXT,              -- JSON array
    environment         TEXT,              -- JSON object of env vars
    startup_priority    INTEGER NOT NULL,
    auto_start          BOOLEAN NOT NULL DEFAULT 1,
    auto_restart        BOOLEAN NOT NULL DEFAULT 1,
    restart_policy      TEXT NOT NULL,     -- see Section 4
    current_pid         INTEGER,
    current_status      TEXT NOT NULL DEFAULT 'stopped',
    last_started_at     TEXT,
    last_stopped_at     TEXT,
    restart_count       INTEGER NOT NULL DEFAULT 0,
    last_restart_at     TEXT
);
```

---

## 3. Internal Process Manager

HeartBeat's internal process manager is responsible for the full
lifecycle of every child service.

### Starting a Service

```python
import subprocess
import psutil

def start_service(service: ManagedService) -> int:
    """
    Start a managed service. Returns the PID of the started process.
    """
    process = subprocess.Popen(
        [service.executable_path] + service.arguments,
        cwd=service.working_directory,
        env={**os.environ, **service.environment},
        stdout=open(f"{log_path}/{service.service_name}.log", "a"),
        stderr=open(f"{log_path}/{service.service_name}_error.log", "a"),
    )
    # Record PID in registry.db
    update_service_pid(service.service_name, process.pid)
    return process.pid
```

### PID Monitoring

HeartBeat monitors child service PIDs using `psutil`:

```python
import psutil

def is_process_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != 'zombie'
    except psutil.NoSuchProcess:
        return False
```

HeartBeat checks every monitored PID every 10 seconds. If a PID is
gone, the process has crashed and the restart policy activates.

---

## 4. Restart Policies

Each service has a restart policy that governs what HeartBeat does
when the service goes down unexpectedly.

### Policy Definitions

| Service | Priority | Policy |
|---|---|---|
| Core | P0 | Restart immediately. Max 3 attempts in 5 minutes. After 3: alert Owner, keep retrying every 5 minutes. |
| Relay | P0 | Restart immediately. Max 3 attempts in 5 minutes. After 3: alert Owner, keep retrying every 5 minutes. |
| HIS | P2 | Restart after 30s backoff. Max 3 attempts in 10 minutes. After 3: alert Owner, keep retrying every 10 minutes. |
| Edge | P2 | Restart after 30s backoff. Max 3 attempts in 10 minutes. After 3: alert Owner, keep retrying every 10 minutes. |
| Float | P4 | **Do not auto-restart.** Float is a user-facing application. If it crashes, the user relaunches it. HeartBeat logs the crash and notifies Admin. |

### Restart Backoff

```
Attempt 1: immediate restart
Attempt 2: wait 10 seconds
Attempt 3: wait 30 seconds
After 3 attempts: alert Owner, wait restart_retry_interval (default 5 min)
Continue retrying indefinitely at that interval until service is healthy
```

### Crash Loop Detection
If a service restarts more than 10 times in 30 minutes, HeartBeat
treats it as a crash loop:
- Stops attempting automatic restarts temporarily (30-minute pause)
- Escalates alert to Owner: "Service {name} is in a crash loop —
  automatic restart paused for 30 minutes. Manual investigation required."
- Resumes automatic restart attempts after the pause

### Restart Count Reset
Restart counter resets to 0 once a service has been healthy for
10 consecutive minutes.

---

## 5. Health Monitoring

HeartBeat uses two complementary health monitoring mechanisms.

### Passive Monitoring — Service Reports In
Services call `POST /api/registry/health/{instance_id}` every 30 seconds.
HeartBeat records the timestamp. If a service stops reporting for
more than 90 seconds, HeartBeat flags it as potentially unhealthy.

### Active Monitoring — HeartBeat Polls
When a service stops reporting, HeartBeat actively polls its
`/health` endpoint. Three consecutive failed polls marks the service
as `unhealthy` in the registry and triggers the restart policy.

### Health Status Values

| Status | Meaning |
|---|---|
| `healthy` | Service reporting in and responding normally |
| `degraded` | Service reporting in but with issues in its details |
| `unhealthy` | Not responding — restart policy active |
| `starting` | Recently started — grace period before health checks begin |
| `updating` | Being updated — health checks paused |
| `stopped` | Deliberately stopped by HeartBeat or Owner |

### Grace Period
After HeartBeat starts a service, it waits 15 seconds before beginning
health checks. This gives the service time to initialise, connect to
its databases, and register with HeartBeat before being marked unhealthy.

### Downtime Notifications (Pro/Enterprise Tier)

For **Helium Pro** and **Enterprise** tenants, HeartBeat sends automated
email and/or push notifications when any monitored server experiences
service downtime. This ensures operations teams are alerted immediately
to outages without needing to manually check dashboards.

- **Trigger:** Service transitions to `unhealthy` or `stopped` (unexpected)
- **Channel:** Email (primary), push notification (if configured)
- **Audience:** Tenant admin users and designated operations contacts
- **Frequency:** Initial alert on state change, follow-up every 15 minutes
  while downtime persists, resolved notification when service recovers
- **Not available** on Standard tier — Standard tenants rely on
  dashboard-only health status visibility

---

## 6. Graceful Shutdown

When HeartBeat needs to stop a service (for update, shutdown, or restart),
it uses a graceful drain sequence rather than killing the process
immediately.

### Drain Sequence

```
1. POST /internal/prepare-shutdown to the service
   → Service stops accepting new requests (returns 503)
   → In-flight requests allowed to complete

2. HeartBeat polls service for drain confirmation:
   GET /internal/drain-status
   → { "drained": true, "in_flight": 0 }

3. Grace period timeout (default 30s):
   If not drained within grace period, HeartBeat sends SIGTERM

4. If process still alive after 10 seconds post-SIGTERM:
   HeartBeat sends SIGKILL (Windows: TerminateProcess)

5. HeartBeat confirms PID is gone before proceeding
```

### Platform Shutdown
When the OS is shutting down, systemd/NSSM signals HeartBeat to stop.
HeartBeat receives the signal, runs graceful shutdown on all child
services in reverse priority order, then exits cleanly.

---

## 7. Startup and Shutdown Sequences

### Full Platform Startup

```
OS boots
→ systemd / NSSM starts HeartBeat (Priority 0)
→ HeartBeat initialises databases (registry.db, blob.db, auth.db)
→ HeartBeat loads managed_services manifest
→ HeartBeat verifies license (Ed25519 signature check)
→ HeartBeat starts Priority 1 services (Core)
   → Waits for Core /health = healthy
→ HeartBeat starts Priority 2 services (Relay, HIS) — parallel
   → Waits for all Priority 2 /health = healthy
→ HeartBeat starts Priority 3 services (Edge)
   → Waits for Edge /health = healthy
→ All services registered in registry
→ HeartBeat begins normal health monitoring loop
→ Float instances can now connect and authenticate
```

### Full Platform Shutdown

```
Owner triggers shutdown from dashboard
OR OS initiates shutdown
→ HeartBeat receives shutdown signal
→ HeartBeat notifies all connected Float instances via SSE:
   { "type": "platform_shutdown_imminent", "seconds": 30 }
→ HeartBeat gracefully stops Priority 4 first (Float — if managed)
→ HeartBeat gracefully stops Priority 3 (Edge)
→ HeartBeat gracefully stops Priority 2 (Relay, HIS) — parallel
→ HeartBeat gracefully stops Priority 1 (Core)
→ HeartBeat closes its own databases cleanly
→ HeartBeat exits
→ systemd / NSSM confirms process stopped
```

---

## 8. Enterprise High Availability

For Enterprise tier, HeartBeat itself must not be a single point of
failure. A warm standby HeartBeat monitors the Primary and promotes
itself automatically on Primary failure.

**Status: Designed — implementation deferred to dedicated HA session.**

High-level model:
- Primary HeartBeat: authoritative, manages all services
- Standby HeartBeat: monitors Primary via heartbeat ping
- On Primary failure: Standby promotes itself, takes over service
  management, assumes registry authority
- Promotion is automatic — no manual intervention required
- Both share the same database files (shared storage) or Standby
  maintains a continuously replicated copy (Litestream or similar)

Split-brain prevention, promotion timing, and database consistency
during promotion are to be designed in a dedicated session.

This is distinct from the Primary/Satellite multi-location topology —
HA redundancy within a single site vs multi-site distribution.

---

## 9. Lifecycle API Endpoint Reference

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/api/lifecycle/services` | List all managed services and current status | Admin+ |
| GET | `/api/lifecycle/services/{name}` | Get service details, PID, restart count | Admin+ |
| POST | `/api/lifecycle/services/{name}/start` | Start a stopped service | Owner (5-min step-up) |
| POST | `/api/lifecycle/services/{name}/stop` | Gracefully stop a service | Owner (5-min step-up) |
| POST | `/api/lifecycle/services/{name}/restart` | Gracefully restart a service | Owner (5-min step-up) |
| GET | `/api/lifecycle/services/{name}/logs` | Tail recent log output | Admin+ |
| POST | `/api/lifecycle/shutdown` | Graceful platform shutdown | Owner (Immediate re-auth) |
| GET | `/api/lifecycle/startup-order` | View the defined startup order | Admin+ |

---

## 10. Environment Variables

| Env Var | Default | Description |
|---|---|---|
| `HEARTBEAT_HEALTH_POLL_INTERVAL_SECONDS` | `10` | How often HeartBeat checks service PIDs |
| `HEARTBEAT_HEALTH_REPORT_TIMEOUT_SECONDS` | `90` | Time before a non-reporting service is flagged |
| `HEARTBEAT_HEALTH_ACTIVE_POLL_FAILURES` | `3` | Consecutive active poll failures before marking unhealthy |
| `HEARTBEAT_SERVICE_GRACE_PERIOD_SECONDS` | `15` | Startup grace period before health checks begin |
| `HEARTBEAT_DRAIN_GRACE_SECONDS` | `30` | Max time to wait for service drain before SIGTERM |
| `HEARTBEAT_CRASH_LOOP_WINDOW_MINUTES` | `30` | Window for crash loop detection |
| `HEARTBEAT_CRASH_LOOP_MAX_RESTARTS` | `10` | Restarts within window before crash loop declared |
| `HEARTBEAT_CRASH_LOOP_PAUSE_MINUTES` | `30` | Pause duration when crash loop detected |
| `HEARTBEAT_RESTART_RETRY_INTERVAL_MINUTES` | `5` | Retry interval after max restart attempts |

---

## 11. Implementation Status

| Component | Status | Notes |
|---|---|---|
| NSSM Windows Service registration (installer) | ❌ Not built | Pro/Enterprise tier only |
| systemd unit file generation (installer) | ❌ Not built | Linux deployment |
| managed_services table in registry.db | ✅ Built | Migration 004, RegistryDatabase methods |
| access_control table in config.db | ✅ Built | Migration 003, ConfigDatabase methods |
| Internal process manager (Popen + PID tracking) | ✅ Built | `src/keepalive/process_handle.py` |
| PID health monitoring loop (psutil) | ✅ Built | `src/keepalive/manager.py` — 10s PID check |
| Active health polling (httpx) | ✅ Built | `src/keepalive/health_poller.py` — 30s poll |
| Restart policy enforcement | ✅ Built | Exponential backoff [0, 10, 30]s |
| Crash loop detection | ✅ Built | >10 restarts in 30min = 30min pause |
| Graceful drain (/internal/prepare-shutdown) | ❌ Not built | Requires child service cooperation |
| Startup sequence (priority-ordered) | ✅ Built | KeepAliveManager.start() |
| Shutdown sequence (reverse priority) | ✅ Built | KeepAliveManager.stop() |
| Float SSE shutdown notification | ❌ Not built | SSE transport exists |
| Lifecycle API endpoints | ✅ Built | `src/api/internal/lifecycle.py` — 6 endpoints |
| Readiness endpoint | ✅ Built | `GET /api/status/readiness` — no auth |
| Scoped discovery (caller filtering) | ✅ Built | `src/handlers/registry_handler.py` |
| Platform config filtering | ✅ Built | `src/handlers/platform_handler.py` |
| Passive health monitoring | ✅ Built | Services report in |
| Health status tracking | ✅ Built | Registry database |
| Failure notifications | ✅ Built | Notification system |
| System Tray App (Standard/Test) | ✅ Built | `src/tray/` — PySide6 monitoring UI |
| HeartBeatConfig tier/service fields | ✅ Built | tier, service_dir, log_dir |
| Enterprise HA warm standby | ❌ Design deferred | Separate session |

### Test Coverage

| Test File | Tests | Phase |
|---|---|---|
| `test_config.py` | 11 | Config |
| `test_access_control.py` | 17 | A — Access control |
| `test_managed_services.py` | 12 | A — Managed services |
| `test_keepalive_process_handle.py` | 23 | B — Process handle |
| `test_keepalive_health_poller.py` | 13 | B — Health poller |
| `test_keepalive_manager.py` | 21 | B — Keep Alive manager |
| `test_readiness.py` | 5 | C — Readiness endpoint |
| `test_scoped_discovery.py` | 13 | D — Scoped discovery |
| `test_lifecycle_api.py` | 9 | E — Lifecycle API |
| `test_tray.py` | 16 | F — System Tray App |
| **Total** | **140** | |

---

*End of Service Lifecycle Specification.*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-03-04 | Version: 1.1*
