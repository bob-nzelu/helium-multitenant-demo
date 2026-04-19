"""
Keep Alive Manager — Orchestrates lifecycle of all child services.

Implements HEARTBEAT_LIFECYCLE_SPEC.md:
    - Priority-ordered startup (Core → Relay/HIS → Edge)
    - PID monitoring every 10 seconds
    - Health endpoint polling every 30 seconds
    - Restart with exponential backoff
    - Crash loop detection (>10 restarts in 30 min → 30 min pause)
    - Reverse-priority graceful shutdown

Runs as an asyncio background task inside FastAPI lifespan.
"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..database.registry import get_registry_database
from .health_poller import HealthPoller
from .process_handle import ProcessHandle

logger = logging.getLogger(__name__)

# ── Timing Constants (from LIFECYCLE_SPEC env vars / defaults) ────────

PID_CHECK_INTERVAL = 10          # Check PIDs every 10 seconds
HEALTH_POLL_INTERVAL = 30        # Poll /health every 30 seconds
STARTUP_GRACE_PERIOD = 15        # Wait 15s before health checks on new service
HEALTHY_RESET_THRESHOLD = 600    # Reset restart count after 10 min healthy

# ── Restart Backoff ───────────────────────────────────────────────────

RESTART_DELAYS = [0, 10, 30]     # Delays for attempts 1, 2, 3

# After max attempts, retry at these intervals (per policy)
RETRY_INTERVALS = {
    "immediate_3": 300,          # Core/Relay: every 5 minutes
    "backoff_3": 600,            # HIS/Edge: every 10 minutes
    "none": 0,                   # Float: no auto-restart
}

# ── Crash Loop Detection ─────────────────────────────────────────────

CRASH_LOOP_WINDOW = 1800         # 30 minutes
CRASH_LOOP_MAX_RESTARTS = 10     # Max restarts in window
CRASH_LOOP_PAUSE = 1800          # 30 minute pause


class KeepAliveManager:
    """
    Manages lifecycle of all child services (Core, Relay, HIS, Edge).

    Startup: load managed_services from registry.db, start in priority order.
    Monitoring: poll PIDs every 10s, /health every 30s.
    Restart: exponential backoff per LIFECYCLE_SPEC restart policies.
    Shutdown: stop in reverse priority order with graceful drain.
    """

    def __init__(self):
        self._handles: Dict[str, ProcessHandle] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._poller = HealthPoller()
        self._running = False

        # Crash loop tracking: {service_name: [restart_timestamps]}
        self._restart_history: Dict[str, List[float]] = defaultdict(list)
        # Crash loop pause: {service_name: resume_timestamp}
        self._crash_loop_paused: Dict[str, float] = {}

    async def start(self) -> None:
        """
        Load managed_services manifest and start all auto_start services.

        Services are started in priority order. Within the same priority,
        services start in parallel. HeartBeat waits for each priority group
        to be alive (PID check) before starting the next group.
        """
        config = get_config()
        log_dir = config.get_log_dir()

        try:
            db = get_registry_database()
            services = db.get_managed_services(auto_start_only=True)
        except Exception as e:
            logger.warning(f"No managed services found: {e}")
            services = []

        if not services:
            logger.info("No managed services to start")
            self._running = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            return

        # Group services by startup_priority
        sorted_services = sorted(services, key=lambda s: s["startup_priority"])
        priority_groups = []
        for priority, group in groupby(sorted_services, key=lambda s: s["startup_priority"]):
            priority_groups.append((priority, list(group)))

        # Start each priority group in order
        for priority, group in priority_groups:
            service_names = [s["service_name"] for s in group]
            logger.info(
                f"Starting priority {priority} services: {service_names}"
            )

            # Create handles and start in parallel
            start_tasks = []
            for svc in group:
                handle = ProcessHandle.from_db_row(svc, log_dir=log_dir)
                self._handles[svc["service_name"]] = handle
                start_tasks.append(self._start_single(handle))

            # Wait for all in this priority group to start
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for svc, result in zip(group, results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Failed to start {svc['service_name']}: {result}"
                    )

            # Brief pause to let services initialize
            await asyncio.sleep(2)

        logger.info(
            f"Keep Alive startup complete: "
            f"{len(self._handles)} service(s) managed"
        )

        # Start background monitoring
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """
        Stop all services in reverse priority order with graceful drain.

        Cancel the monitoring task first, then stop services from highest
        priority number to lowest (Edge → Relay/HIS → Core).
        """
        self._running = False

        # Cancel monitor task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Close health poller
        await self._poller.close()

        if not self._handles:
            return

        # Group handles by priority (reverse order for shutdown)
        handles_with_priority = []
        try:
            db = get_registry_database()
            for name, handle in self._handles.items():
                svc = db.get_managed_service(name)
                priority = svc["startup_priority"] if svc else 0
                handles_with_priority.append((priority, name, handle))
        except Exception:
            # Fallback: stop all in parallel
            handles_with_priority = [
                (0, name, handle) for name, handle in self._handles.items()
            ]

        # Sort by priority descending (highest priority number = stop first)
        handles_with_priority.sort(key=lambda x: x[0], reverse=True)

        # Group by priority for parallel stopping
        sorted_items = handles_with_priority
        for priority, group in groupby(sorted_items, key=lambda x: x[0]):
            group_list = list(group)
            service_names = [g[1] for g in group_list]
            logger.info(f"Stopping priority {priority} services: {service_names}")

            stop_tasks = [handle.stop() for _, _, handle in group_list]
            await asyncio.gather(*stop_tasks, return_exceptions=True)

            # Update DB status
            try:
                db = get_registry_database()
                for _, name, _ in group_list:
                    db.mark_service_stopped(name)
            except Exception:
                pass

        self._handles.clear()
        logger.info("All managed services stopped")

    async def start_service(self, service_name: str) -> Dict[str, Any]:
        """Start a single service by name."""
        handle = self._handles.get(service_name)

        if handle is None:
            # Load from DB
            try:
                db = get_registry_database()
                svc = db.get_managed_service(service_name)
                if svc is None:
                    return {"status": "error", "message": f"Unknown service: {service_name}"}
                config = get_config()
                handle = ProcessHandle.from_db_row(svc, log_dir=config.get_log_dir())
                self._handles[service_name] = handle
            except Exception as e:
                return {"status": "error", "message": str(e)}

        if handle.is_alive():
            return {
                "status": "already_running",
                "service_name": service_name,
                "pid": handle.pid,
            }

        try:
            pid = await handle.start()
            # Update DB
            try:
                db = get_registry_database()
                db.mark_service_started(service_name, pid)
            except Exception:
                pass

            return {
                "status": "started",
                "service_name": service_name,
                "pid": pid,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def stop_service(self, service_name: str) -> Dict[str, Any]:
        """Gracefully stop a single service."""
        handle = self._handles.get(service_name)
        if handle is None:
            return {"status": "error", "message": f"Unknown service: {service_name}"}

        await handle.stop()

        # Update DB
        try:
            db = get_registry_database()
            db.mark_service_stopped(service_name)
        except Exception:
            pass

        return {
            "status": "stopped",
            "service_name": service_name,
        }

    async def restart_service(self, service_name: str) -> Dict[str, Any]:
        """Stop then start a service."""
        await self.stop_service(service_name)
        await asyncio.sleep(1)
        return await self.start_service(service_name)

    async def get_status(self) -> Dict[str, Any]:
        """
        Return status of all managed services.

        Returns:
            {
                "services": {
                    "core": {"pid": 1234, "status": "healthy", ...},
                    "relay": {"pid": 5678, "status": "starting", ...},
                    ...
                },
                "total": int,
                "healthy": int,
                "unhealthy": int,
            }
        """
        services = {}
        healthy_count = 0
        unhealthy_count = 0

        for name, handle in self._handles.items():
            info = handle.to_dict()
            services[name] = info

            if handle.status == "healthy":
                healthy_count += 1
            elif handle.status in ("unhealthy", "crash_loop", "stopped"):
                unhealthy_count += 1

        return {
            "services": services,
            "total": len(services),
            "healthy": healthy_count,
            "unhealthy": unhealthy_count,
        }

    # ── Internal Methods ──────────────────────────────────────────────

    async def _start_single(self, handle: ProcessHandle) -> int:
        """Start a single service and update DB."""
        pid = await handle.start()

        try:
            db = get_registry_database()
            db.mark_service_started(handle.service_name, pid)
        except Exception as e:
            logger.debug(f"DB update failed for {handle.service_name}: {e}")

        return pid

    async def _monitor_loop(self) -> None:
        """
        Background monitoring loop.

        Every PID_CHECK_INTERVAL seconds:
            - Check all PIDs are alive
            - If a PID is gone → service crashed → apply restart policy

        Every HEALTH_POLL_INTERVAL seconds:
            - Poll all /health endpoints
            - Update handle statuses
            - 3 consecutive failures → unhealthy → restart

        Skips health checks during STARTUP_GRACE_PERIOD after start.
        Resets restart count after HEALTHY_RESET_THRESHOLD continuous healthy seconds.
        """
        last_health_poll = 0.0

        while self._running:
            try:
                now = time.monotonic()

                # ── PID Check ─────────────────────────────────────
                for name, handle in list(self._handles.items()):
                    if handle.status in ("stopped", "crash_loop"):
                        continue

                    if handle.pid is not None and not handle.is_alive():
                        logger.warning(
                            f"Service {name} PID {handle.pid} is gone — "
                            f"process crashed"
                        )
                        handle.status = "unhealthy"
                        handle.pid = None
                        handle.process = None

                        await self._handle_service_failure(name)

                # ── Health Poll (every HEALTH_POLL_INTERVAL) ──────
                if now - last_health_poll >= HEALTH_POLL_INTERVAL:
                    last_health_poll = now

                    statuses = await self._poller.poll_all(self._handles)

                    for name, health_status in statuses.items():
                        handle = self._handles.get(name)
                        if handle is None:
                            continue

                        # Skip health checks during grace period
                        if handle.last_started_at:
                            elapsed = (
                                datetime.now(timezone.utc) - handle.last_started_at
                            ).total_seconds()
                            if elapsed < STARTUP_GRACE_PERIOD:
                                continue

                        if health_status in ("healthy", "degraded"):
                            handle.record_health_success()
                            if health_status == "degraded":
                                handle.status = "degraded"

                            # Reset restart count after sustained health
                            if (
                                handle.healthy_duration_seconds
                                and handle.healthy_duration_seconds >= HEALTHY_RESET_THRESHOLD
                                and handle.restart_count > 0
                            ):
                                handle.restart_count = 0
                                try:
                                    db = get_registry_database()
                                    db.reset_restart_count(name)
                                except Exception:
                                    pass
                        else:
                            handle.record_health_failure()
                            if handle.status == "unhealthy" and handle.is_alive():
                                # Service is alive but not responding — restart
                                logger.warning(
                                    f"Service {name} unhealthy (3 consecutive "
                                    f"health check failures)"
                                )
                                await self._handle_service_failure(name)

                    # Update DB statuses
                    try:
                        db = get_registry_database()
                        for name, handle in self._handles.items():
                            db.update_service_status(
                                name, handle.status, handle.pid
                            )
                    except Exception:
                        pass

                await asyncio.sleep(PID_CHECK_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(PID_CHECK_INTERVAL)

    async def _handle_service_failure(self, service_name: str) -> None:
        """
        Apply restart policy with backoff. Detect crash loops.

        Restart policy from LIFECYCLE_SPEC:
            Attempt 1: immediate restart
            Attempt 2: wait 10 seconds
            Attempt 3: wait 30 seconds
            After 3: alert, retry every retry_interval

        Crash loop detection:
            >10 restarts in 30 minutes → pause 30 minutes
        """
        handle = self._handles.get(service_name)
        if handle is None:
            return

        # Check if in crash loop pause
        pause_until = self._crash_loop_paused.get(service_name, 0)
        if time.time() < pause_until:
            logger.debug(
                f"{service_name} in crash loop pause until "
                f"{datetime.fromtimestamp(pause_until, tz=timezone.utc).isoformat()}"
            )
            return

        # Check auto_restart setting
        try:
            db = get_registry_database()
            svc = db.get_managed_service(service_name)
            if svc and not svc.get("auto_restart", True):
                logger.info(f"{service_name} auto_restart=false — not restarting")
                return

            restart_policy = svc.get("restart_policy", "immediate_3") if svc else "immediate_3"
        except Exception:
            restart_policy = "immediate_3"

        if restart_policy == "none":
            logger.info(f"{service_name} restart_policy=none — not restarting")
            return

        # Record restart timestamp for crash loop detection
        now = time.time()
        self._restart_history[service_name].append(now)

        # Prune old entries outside the crash loop window
        cutoff = now - CRASH_LOOP_WINDOW
        self._restart_history[service_name] = [
            t for t in self._restart_history[service_name] if t > cutoff
        ]

        # Crash loop detection
        if len(self._restart_history[service_name]) >= CRASH_LOOP_MAX_RESTARTS:
            logger.error(
                f"CRASH LOOP DETECTED: {service_name} restarted "
                f"{len(self._restart_history[service_name])} times in "
                f"{CRASH_LOOP_WINDOW}s — pausing for {CRASH_LOOP_PAUSE}s"
            )
            handle.status = "crash_loop"
            self._crash_loop_paused[service_name] = now + CRASH_LOOP_PAUSE

            try:
                db = get_registry_database()
                db.update_service_status(service_name, "crash_loop")
            except Exception:
                pass

            # TODO: Emit SSE alert to connected Float instances
            return

        # Determine restart delay
        attempt = handle.restart_count
        if attempt < len(RESTART_DELAYS):
            delay = RESTART_DELAYS[attempt]
        else:
            delay = RETRY_INTERVALS.get(restart_policy, 300)

        handle.restart_count += 1

        # Update DB
        try:
            db = get_registry_database()
            db.increment_restart_count(service_name)
        except Exception:
            pass

        if delay > 0:
            logger.info(
                f"Restarting {service_name} in {delay}s "
                f"(attempt {handle.restart_count})"
            )
            await asyncio.sleep(delay)

        # Stop if still alive (e.g., deadlocked process)
        if handle.is_alive():
            await handle.stop(grace_seconds=10)

        # Restart
        try:
            pid = await handle.start()
            logger.info(
                f"Restarted {service_name}: PID={pid} "
                f"(attempt {handle.restart_count})"
            )

            try:
                db = get_registry_database()
                db.mark_service_started(service_name, pid)
            except Exception:
                pass

        except Exception as e:
            logger.error(
                f"Failed to restart {service_name} "
                f"(attempt {handle.restart_count}): {e}"
            )


# ── Singleton ──────────────────────────────────────────────────────────

_keepalive_instance: Optional[KeepAliveManager] = None


def get_keepalive_manager() -> KeepAliveManager:
    """Get singleton KeepAliveManager."""
    global _keepalive_instance
    if _keepalive_instance is None:
        _keepalive_instance = KeepAliveManager()
    return _keepalive_instance


def reset_keepalive_manager() -> None:
    """Reset singleton (for testing/shutdown)."""
    global _keepalive_instance
    _keepalive_instance = None
